"""Schritt 3: Lokale KI bewertet den Clip von 1 bis 10 (Whisper-Transkript + Chat -> Ollama)."""
import json
import logging
import math
import threading
import urllib.request

from .chat import hype_fraction

log = logging.getLogger("score")
_model, _model_lock = None, threading.Lock()
_whisper_failed = False


def preload(model_name):
    """Modell VOR dem Start der Aufnahmen laden (einmaliger Download ca. 480 MB bei "small"),
    damit der Download nicht mit den Streams um die Leitung konkurriert."""
    global _model, _whisper_failed
    if not model_name:
        return
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("faster-whisper nicht installiert -> kein Transkript, keine Untertitel")
        _whisper_failed = True
        return
    log.info("Lade Whisper-Modell '%s' (beim ersten Mal Download, bitte warten) ...", model_name)
    try:
        _model = WhisperModel(model_name, device="auto", compute_type="auto")
    except Exception:
        _model = WhisperModel(model_name, device="cpu", compute_type="int8")
    log.info("Whisper-Modell bereit.")


def transcribe(path, model_name, language):
    """Liefert [(start, ende, wort), ...] oder [] wenn Whisper nicht verfuegbar ist."""
    global _model, _whisper_failed
    if not model_name or _whisper_failed:
        return []
    with _model_lock:
        try:
            if _model is None:
                from faster_whisper import WhisperModel
                try:
                    _model = WhisperModel(model_name, device="auto", compute_type="auto")
                except Exception:
                    _model = WhisperModel(model_name, device="cpu", compute_type="int8")
            segs, _ = _model.transcribe(str(path), language=language or None, word_timestamps=True,
                                        vad_filter=True)
            return [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or []) if w.word.strip()]
        except ImportError:
            log.warning("faster-whisper nicht installiert -> kein Transkript, keine Untertitel")
            _whisper_failed = True
        except Exception as e:
            log.warning("Whisper-Fehler: %s", e)
    return []


def heuristic(spike, messages):
    s = 3 + 2 * math.log2(max(spike.ratio, 1)) + 3 * hype_fraction(messages)
    return max(1, min(10, round(s)))


def fallback_title(spike, words, spike_at):
    """Ohne KI: der Satz, der kurz vor dem Chat-Spike gesagt wurde."""
    sentence = []
    for s, e, w in words:
        if s > spike_at:
            break
        sentence.append(w)
        if w[-1:] in ".!?" and s < spike_at - 12:
            sentence = []
    text = " ".join(sentence[-12:]).strip(" ,.-")
    return (text[:70] if len(text) > 8 else f"{spike.channel} rastet aus").strip()


def score_clip(spike, messages, words, ai, spike_at=0.0):
    """Gibt (score, titel, quelle) zurueck."""
    h = heuristic(spike, messages)
    fb = fallback_title(spike, words, spike_at)
    if not ai.get("ollama_model"):
        return h, fb, "chat"
    transcript = " ".join(w for _, _, w in words)[:1500] or "(kein Transkript)"
    prompt = (
        "Du bewertest Livestream-Clips fuer TikTok. Gib NUR JSON zurueck: "
        '{"score": <1-10>, "title": "<kurzer TikTok-Titel mit Hook, max 60 Zeichen, in der Sprache des Streamers (Deutsch oder Englisch), beschreibt was passiert, kein Clickbait ohne Bezug>"}.\n'
        "Zieh Punkte ab, wenn der Ausschnitt ohne Vorwissen keinen Sinn ergibt.\n"
        "10 = extrem viral (lustig, schockierend, krasser Fail/Win, emotionale Reaktion), "
        "1 = langweilig. Der Chat ist gerade deutlich schneller geworden - das ist ein starkes Signal.\n\n"
        f"Chat-Tempo: {spike.ratio:.1f}x normal, {spike.count} Nachrichten im Fenster.\n"
        f"Chat-Nachrichten:\n" + "\n".join(m[:80] for m in messages[-40:]) +
        f"\n\nWas der Streamer sagt:\n{transcript}\n")
    try:
        req = urllib.request.Request(
            ai["ollama_url"].rstrip("/") + "/api/generate",
            data=json.dumps({"model": ai["ollama_model"], "prompt": prompt, "stream": False,
                             "format": "json", "options": {"temperature": 0.2}}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(json.loads(r.read())["response"])
        return max(1, min(10, round(float(data["score"])))), (str(data.get("title", "")).strip()[:80] or fb), "ki"
    except Exception as e:
        log.warning("Ollama nicht erreichbar (%s) -> Chat-Heuristik", e)
        return h, fb, "chat"
