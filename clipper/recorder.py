"""Schritt 1: Streams aufnehmen und die letzten Minuten als lokalen Cache halten."""
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("recorder")


def _streamlink_flags():
    """Nur Flags nutzen, die die installierte streamlink-Version kennt."""
    try:
        text = subprocess.run(["streamlink", "--help"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    return [f for f in ("--twitch-disable-ads", "--twitch-low-latency") if f in text]


class Recorder(threading.Thread):
    def __init__(self, channel, buf_dir, quality, segment_seconds, buffer_seconds, flags):
        super().__init__(daemon=True, name=f"rec-{channel}")
        self.channel = channel
        self.dir = Path(buf_dir) / channel
        self.quality = quality
        self.seg = segment_seconds
        self.keep = buffer_seconds + 90  # Reserve, damit laufende Clips ihre Segmente noch finden
        self.flags = flags
        self.live = False
        self.stopped = False
        self._procs = ()

    def run(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._cleanup, daemon=True).start()
        while not self.stopped:
            try:
                self._record_once()
            except Exception as e:
                log.warning("[%s] Aufnahme-Fehler: %s", self.channel, e)
            self.live = False
            time.sleep(15)

    def stop(self):
        self.stopped = True
        for p in self._procs:
            if p.poll() is None:
                p.kill()

    def last_error(self):
        """Letzte Meldung von streamlink (z.B. warum kein Video kommt)."""
        try:
            lines = [l.strip() for l in (self.dir / "streamlink.log").read_text(errors="replace").splitlines() if l.strip()]
            return lines[-1][-160:] if lines else ""
        except OSError:
            return ""

    def _record_once(self):
        pattern = str(self.dir / f"{int(time.time())}_%06d.ts")
        sl = subprocess.Popen(
            ["streamlink", "--stdout", "--retry-streams", "20", "--retry-open", "3", *self.flags,
             f"https://twitch.tv/{self.channel}", self.quality],
            stdout=subprocess.PIPE, stderr=open(self.dir / "streamlink.log", "w"))
        ff = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-c", "copy",
             "-f", "segment", "-segment_time", str(self.seg), "-segment_format", "mpegts",
             "-reset_timestamps", "1", pattern],
            stdin=sl.stdout, stderr=open(self.dir / "ffmpeg.log", "w"))
        sl.stdout.close()
        self._procs = (ff, sl)
        log.info("[%s] warte auf Stream / nehme auf ...", self.channel)
        try:
            while ff.poll() is None and not self.stopped:
                time.sleep(5)
                self.live = bool(list_segments(self.dir, self.seg, max_age=3 * self.seg))
        finally:
            for p in (ff, sl):
                if p.poll() is None:
                    p.kill()
        log.info("[%s] Stream beendet/unterbrochen, neuer Versuch", self.channel)

    def speed(self, span=120):
        """(Tempo, Mbit/s): Videozeit pro Echtzeit-Sekunde (1.0 = Leitung haelt mit) und Datenrate."""
        now = time.time()
        segs = [x for x in list_segments(self.dir, self.seg) if now - x[1] <= span]
        size = sum(x[2].stat().st_size for x in segs if x[2].exists())
        return sum(e - a for a, e, _ in segs) / span, size * 8 / span / 1e6

    def _cleanup(self):
        while True:
            cutoff = time.time() - self.keep
            for f in self.dir.glob("*.ts"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    pass
            time.sleep(20)


_durations = {}


def _duration(path, fallback):
    """Echte Laenge eines Segments (gecacht)."""
    key = str(path)
    if key not in _durations:
        if len(_durations) > 5000:
            _durations.clear()
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  key], capture_output=True, text=True, timeout=20).stdout.strip()
            _durations[key] = float(out)
        except Exception:
            return fallback
    return _durations[key]


def list_segments(seg_dir, segment_seconds, max_age=None):
    """Fertige Segmente als (start, ende, pfad). Ende = Wanduhr (mtime), Start = Ende - echte Laenge.

    Das neueste Segment wird noch geschrieben und deshalb ausgelassen.
    """
    files = []
    for f in Path(seg_dir).glob("*.ts"):
        try:
            files.append((f.stat().st_mtime, f))
        except OSError:
            pass
    files.sort()
    files = files[:-1]
    if max_age is not None:
        now = time.time()
        files = [x for x in files if now - x[0] <= max_age]
    out, prev_end = [], None
    for end, f in files:
        start = end - _duration(f, segment_seconds)
        if prev_end is not None:
            start = max(start, prev_end)
        out.append((start, end, f))
        prev_end = end
    return out


def cut_clip(seg_dir, segment_seconds, t_start, t_end, out_path, wait=0):
    """Schneidet [t_start, t_end] (Wanduhr) verlustfrei aus dem Cache. Gibt True bei Erfolg."""
    deadline = time.time() + wait
    while True:
        segs = list_segments(seg_dir, segment_seconds)
        if (segs and segs[-1][1] >= t_end) or time.time() >= deadline:
            break
        time.sleep(1)
    use = [s for s in segs if s[1] > t_start and s[0] < t_end]
    if not use:
        return False
    out_path = Path(out_path)
    lst = out_path.with_suffix(".txt")
    lst.write_text("".join(f"file '{s[2].resolve().as_posix()}'\n" for s in use))
    offset = max(0.0, t_start - use[0][0])
    duration = min(t_end, use[-1][1]) - max(t_start, use[0][0])
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
         "-ss", f"{offset:.2f}", "-i", str(lst), "-t", f"{duration:.2f}", "-c", "copy",
         "-movflags", "+faststart", str(out_path)],
        capture_output=True, text=True)
    lst.unlink(missing_ok=True)
    if r.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 10_000:
        log.warning("Schnitt fehlgeschlagen: %s", r.stderr.strip()[-300:])
        return False
    return True


def check_tools():
    missing = [t for t in ("ffmpeg", "streamlink") if not shutil.which(t)]
    if missing:
        raise SystemExit(f"Fehlt: {', '.join(missing)} (ffmpeg installieren, `pip install streamlink`)")


def start_recorders(cfg, buf_dir):
    flags = _streamlink_flags()
    g = cfg["general"]
    recs = [Recorder(c["name"].lower(), buf_dir, g["quality"], g["segment_seconds"], g["buffer_seconds"], flags)
            for c in cfg["channel"]]
    for r in recs:
        r.start()
    return recs

