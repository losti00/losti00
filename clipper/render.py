"""Schritt 4: 16:9 -> 9:16 (Cam oben, Gameplay unten) mit Wort-fuer-Wort-Untertiteln."""
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("render")
W, H, CAM_H = 1080, 1920, 720
GAME_H = H - CAM_H


def _ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def write_ass(words, path, has_cam):
    """Zeigt je 3 Woerter, das gerade gesprochene farbig hervorgehoben."""
    margin_v = H - CAM_H - 70 if has_cam else 380
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: W,Arial,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,40,40,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    clean = [(s, e, re.sub(r"[{}\\\n]", "", w)) for s, e, w in words]
    lines = []
    for i in range(0, len(clean), 3):
        group = clean[i:i + 3]
        for j, (s, e, _) in enumerate(group):
            end = group[j + 1][0] if j + 1 < len(group) else min(e + 0.4, clean[i + 3][0] if i + 3 < len(clean) else e + 0.4)
            if end <= s:
                continue
            text = " ".join(
                ("{\\c&H00E6FF&\\fscx108\\fscy108}" + w + "{\\c&HFFFFFF&\\fscx100\\fscy100}") if k == j else w
                for k, (_, _, w) in enumerate(group))
            lines.append(f"Dialogue: 0,{_ts(s)},{_ts(end)},W,,0,0,0,,{text}")
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


def render_vertical(src, dst, cam=None, words=None):
    """cam = [x, y, w, h] als Bildanteile (0-1) oder None. Gibt True bei Erfolg."""
    src, dst = Path(src).resolve(), Path(dst).resolve()
    if cam:
        x, y, w, h = cam
        fc = (f"[0:v]split[a][b];"
              f"[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={W}:{CAM_H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{CAM_H}[cam];"
              f"[b]scale=-2:{GAME_H},crop={W}:{GAME_H}[game];"
              f"[cam][game]vstack[v0]")
    else:
        fc = (f"[0:v]split[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:4[bg];"
              f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]")
    ass = None
    if words:
        ass = dst.with_suffix(".ass")
        write_ass(words, ass, bool(cam))
        fc += f";[v0]subtitles={ass.name}[v]"  # relativer Name + cwd: keine Pfad-Escapes noetig
    else:
        fc += ";[v0]null[v]"
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-filter_complex", fc,
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-maxrate", "6M", "-bufsize", "12M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", dst.name],
        cwd=dst.parent, capture_output=True, text=True)
    if ass:
        ass.unlink(missing_ok=True)
    if r.returncode != 0:
        log.warning("Render fehlgeschlagen: %s", r.stderr.strip()[-400:])
        return False
    return True
