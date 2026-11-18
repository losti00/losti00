"""Facecam automatisch finden: ein Gesicht, das ueber den ganzen Clip an derselben Stelle bleibt."""
import logging
import statistics
from pathlib import Path

log = logging.getLogger("cam")
_cache = {}      # channel -> letzte erkannte Cam (Fallback, wenn der Streamer kurz weg ist)
_warned = False


def detect_cam(video, channel=None, samples=12):
    """Liefert [x, y, w, h] als Bildanteile (0-1) oder None."""
    global _warned
    try:
        import cv2
    except ImportError:
        if not _warned:
            log.warning("opencv nicht installiert -> keine Facecam-Erkennung (pip install opencv-python-headless)")
            _warned = True
        return None
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    det = None
    faces, used = [], 0     # (frame_nr, cx, cy, breite, seitenverhaeltnis) normiert
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / samples))
        ok, frame = cap.read()
        if not ok:
            continue
        used += 1
        h, w = frame.shape[:2]
        gw, gh = 1280, int(h * 1280 / w)
        if det is None:
            det = cv2.FaceDetectorYN.create(str(Path(__file__).with_name("yunet.onnx")), "", (gw, gh), 0.6, 0.3, 50)
        _, found = det.detect(cv2.resize(frame, (gw, gh)))
        for f in (found if found is not None else []):
            x, y, fw, fh = (float(v) for v in f[:4])
            faces.append((i, (x + fw / 2) / gw, (y + fh / 2) / gh, fw / gw, gw / gh))
    cap.release()
    best = []
    for f in faces:  # groesste Gruppe von Gesichtern an (fast) derselben Stelle
        group = [g for g in faces if abs(g[1] - f[1]) < 0.05 and abs(g[2] - f[2]) < 0.07]
        if len({g[0] for g in group}) > len({g[0] for g in best}):
            best = group
    if not used or len({g[0] for g in best}) < max(3, used * 0.4):
        if channel in _cache:
            return _cache[channel]
        return None
    cx, cy, fw = (statistics.median(g[k] for g in best) for k in (1, 2, 3))
    aspect = best[0][4]
    # Ausschnitt um das Gesicht im Format 3:2 (passt auf 1080x720), Gesicht leicht oberhalb der Mitte
    w = min(1.0, fw * 3.2)
    h = min(1.0, w * aspect / 1.5)
    w = h * 1.5 / aspect
    x = min(max(cx - w / 2, 0.0), 1.0 - w)
    y = min(max(cy - h * 0.45, 0.0), 1.0 - h)
    cam = [round(v, 4) for v in (x, y, w, h)]
    if channel:
        _cache[channel] = cam
    return cam
