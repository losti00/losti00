"""Clip-Pipeline: Aufnahme -> Chat-Spike -> KI-Score -> 9:16 -> Telegram.

    python -m clipper              Pipeline starten
    python -m clipper telegram     chat_id herausfinden / Bot testen
    python -m clipper selftest     Offline-Test mit Testbild (ohne Twitch)
"""
import json
import logging
import os
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import cam as camdetect, discover, notify, recorder, render, score
from .chat import Detector, Spike, TwitchChat

log = logging.getLogger("clipper")

DEFAULTS = {
    "general": {"quality": "720p60,720p,best", "buffer_seconds": 300, "segment_seconds": 10, "pre_seconds": 55,
                "post_seconds": 25, "min_clip_seconds": 61, "min_score": 6, "workers": 2, "language": "", "out_dir": "clips",
                "keep_rejected": False},
    "detect": {"window": 10, "baseline": 120, "factor": 3.0, "min_messages": 8, "cooldown": 40},
    "ai": {"whisper_model": "small", "ollama_url": "http://localhost:11434", "ollama_model": "llama3.1:8b"},
    "telegram": {"bot_token": "", "chat_id": ""},
    "auto": {"enabled": False, "games": ["Grand Theft Auto V", "Grand Theft Auto VI"], "top": 10,
             "languages": ["de", "en"], "refresh_minutes": 30, "client_id": "", "client_secret": ""},
    "channel": [],
}


def load_config(path="config.toml"):
    cfg = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in DEFAULTS.items()}
    p = Path(path)
    if p.exists():
        user = tomllib.loads(p.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict):
                cfg.setdefault(k, {}).update(v)
            else:
                cfg[k] = v
    return cfg


def process(cfg, spike, detector, buf_dir, wait=True):
    """Ein Spike -> ein fertiger Clip (oder aussortiert). Laeuft im Worker-Thread."""
    g = cfg["general"]
    ch = spike.channel
    chan_cfg = next((c for c in cfg["channel"] if c["name"].lower() == ch), {})
    t0, t1 = spike.t - g["pre_seconds"], spike.t + g["post_seconds"]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(spike.t))
    out_dir = Path(g["out_dir"]) / ch
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / f"{stamp}_raw.mp4"
    try:
        if wait:
            time.sleep(max(0, t1 - time.time()))
        if not recorder.cut_clip(Path(buf_dir) / ch, g["segment_seconds"], t0, t1, raw,
                                 wait=g["segment_seconds"] * 3 if wait else 0):
            log.warning("[%s] Spike x%.1f, aber kein Video im Cache (Stream offline?)", ch, spike.ratio)
            return None
        messages = detector.between(ch, spike.t - cfg["detect"]["window"], t1) or spike.messages
        words = score.transcribe(raw, cfg["ai"]["whisper_model"], g["language"])
        s, title, source = score.score_clip(spike, messages, words, cfg["ai"], spike_at=spike.t - t0)
        if s < g["min_score"]:
            log.info("[%s] aussortiert: Score %d/10 (%s), Chat x%.1f", ch, s, source, spike.ratio)
            if not g["keep_rejected"]:
                raw.unlink(missing_ok=True)
            return None
        final = out_dir / f"{stamp}_score{s}.mp4"
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                    "csv=p=0", str(raw)], capture_output=True, text=True).stdout.strip() or 0)
        if dur < g["min_clip_seconds"]:
            log.info("[%s] verworfen: nur %.0f s Material im Cache (Stream gerade erst gestartet?)", ch, dur)
            raw.unlink(missing_ok=True)
            return None
        a, b = render.fit_window(words, dur, g["min_clip_seconds"])
        cam = chan_cfg.get("cam") or camdetect.detect_cam(raw, ch)
        if not render.render_vertical(raw, final, cam, words, a, b):
            log.warning("[%s] Clip konnte nicht gerendert werden, Rohdatei bleibt liegen: %s", ch, raw)
            return None
        raw.unlink(missing_ok=True)
        meta = {"channel": ch, "time": stamp, "score": s, "score_source": source, "title": title,
                "seconds": round(b - a, 1), "facecam": cam, "chat_ratio": round(spike.ratio, 1), "chat_count": spike.count,
                "transcript": " ".join(w for _, _, w in words), "chat": messages[-40:]}
        final.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        final.with_suffix(".txt").write_text(title + "\n", encoding="utf-8")
        caption = f"🎬 {ch} · {s}/10 · Chat x{spike.ratio:.1f}"
        if title:
            caption += f"\n{title}"
        sent = notify.send_video(cfg, final, caption)
        log.info("[%s] CLIP %s  %.0fs  Score %d/10 (%s)  Cam %s  \"%s\"%s", ch, final, b - a, s, source,
                 "erkannt" if cam else "nicht gefunden", title, "  -> Telegram" if sent else "")
        return final
    except Exception:
        log.exception("[%s] Fehler bei der Clip-Verarbeitung", ch)
        return None


def run(cfg):
    recorder.check_tools()
    render.subtitles_supported()
    for old in Path(cfg["general"]["out_dir"]).glob("*/*_raw.*"):  # Reste abgebrochener Laeufe
        old.unlink(missing_ok=True)
    auto = cfg["auto"]
    if auto["enabled"]:
        known = {c["name"].lower() for c in cfg["channel"]}
        cfg["channel"] += [{"name": n} for n in discover.top_channels(auto) if n not in known]
    if not cfg["channel"]:
        raise SystemExit("Keine Kanaele. In config.toml [[channel]] eintragen oder [auto] aktivieren.")
    buf_dir = Path("buffer")
    names = [c["name"].lower() for c in cfg["channel"]]
    detector = Detector(**cfg["detect"])
    recs = recorder.start_recorders(cfg, buf_dir)
    chat = TwitchChat(names, detector)
    chat.start()
    pool = ThreadPoolExecutor(max_workers=cfg["general"]["workers"])
    if not notify.token(cfg) or not cfg["telegram"]["chat_id"]:
        log.warning("Telegram nicht konfiguriert - Clips landen nur in %s/", cfg["general"]["out_dir"])
    log.info("Pipeline laeuft fuer %d Kanaele. Strg+C zum Beenden.", len(names))
    last_status = last_refresh = time.time()
    try:
        while True:
            time.sleep(1)
            for spike in detector.tick():
                log.info("[%s] CLIP-MOMENT! Chat x%.1f (%d Nachrichten/%ds)", spike.channel, spike.ratio,
                         spike.count, cfg["detect"]["window"])
                pool.submit(process, cfg, spike, detector, buf_dir)
            if auto["enabled"] and time.time() - last_refresh > auto["refresh_minutes"] * 60:
                last_refresh = time.time()
                try:
                    for n in discover.top_channels(auto):
                        if n not in names:
                            names.append(n)
                            cfg["channel"].append({"name": n})
                            recs += recorder.start_recorders({**cfg, "channel": [{"name": n}]}, buf_dir)
                            chat.add(n)
                except Exception as e:
                    log.warning("Top-Streams konnten nicht aktualisiert werden: %s", e)
            if time.time() - last_status > 300:
                last_status = time.time()
                log.info("Status: live=%s | Chat %s | Warteschlange %d",
                         ",".join(r.channel for r in recs if r.live) or "-",
                         "ok" if chat.connected else "GETRENNT", pool._work_queue.qsize())
                dead = [r.channel for r in recs if not r.live]
                if dead:
                    log.warning("Kein Video von: %s  (offline - oder Leitung zu langsam: dann [auto] top "
                                "senken bzw. quality reduzieren)", ", ".join(dead))
    except KeyboardInterrupt:
        log.info("Beende ...")
        os._exit(0)


def selftest(cfg):
    """Erzeugt ein Testvideo im Cache, simuliert einen Chat-Spike und laeuft durch alle Schritte."""
    import shutil
    buf_dir, ch = Path("work/selftest_buffer"), "selftest"
    shutil.rmtree(buf_dir, ignore_errors=True)
    (buf_dir / ch).mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "120", "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "60", "-c:a", "aac", "-f", "segment", "-segment_time", "10", "-segment_format", "mpegts",
         str(buf_dir / ch / "0_%06d.ts")], check=True)
    now = time.time()
    for i, f in enumerate(sorted((buf_dir / ch).glob("*.ts"))):
        os.utime(f, (now - 120 + (i + 1) * 10,) * 2)
    det = Detector(**cfg["detect"])
    t = now - 250
    while t < now - 50:  # ruhiger Chat
        det.feed(ch, "hallo", t)
        t += 2.5
    for i in range(40):  # Explosion
        det.feed(ch, ["KEKW", "NEIN WAY", "CLIP DAS", "WAAAS", "krank"][i % 5], now - 49 + i * 0.2)
    spikes = det.tick(now - 40)
    assert spikes, "Spike wurde nicht erkannt"
    print(f"1/3 Spike erkannt: x{spikes[0].ratio:.1f}, {spikes[0].count} Nachrichten")
    cfg["channel"] = [{"name": ch}]
    cfg["general"]["min_score"] = 1
    fake_words = [(3 + i * 0.5 + (i // 6) * 1.2, 3.4 + i * 0.5 + (i // 6) * 1.2, w + ("." if i % 6 == 5 else ""))
                  for i, w in enumerate(("Das ist ein Test der Untertitel " * 22).split())]
    orig = score.transcribe
    score.transcribe = lambda *a, **k: orig(*a, **k) or fake_words
    out = process(cfg, spikes[0], det, buf_dir, wait=False)
    assert out and out.exists(), "Kein Clip erzeugt"
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height:format=duration", "-of", "csv=p=0", str(out)],
                          capture_output=True, text=True).stdout.split()
    print(f"2/3 Clip gerendert: {out}  ({' '.join(info)})")
    print("3/3 Telegram:", "konfiguriert (Clip sollte auf dem Handy sein)"
          if notify.token(cfg) and cfg["telegram"]["chat_id"] else "nicht konfiguriert (uebersprungen)")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "telegram":
        notify.print_chat_ids(cfg)
    elif cmd == "selftest":
        selftest(cfg)
    elif cmd == "run":
        run(cfg)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
