"""Schritt 4: 16:9 -> 9:16 (Cam oben, Gameplay unten) mit Wort-fuer-Wort-Untertiteln."""
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("render")
_subs_ok = None


def subtitles_supported():
    """Manche ffmpeg-Builds (z.B. das schlanke Homebrew-ffmpeg) haben keinen Untertitel-Filter (libass)."""
    global _subs_ok
    if _subs_ok is None:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        _subs_ok = " subtitles " in out
        if not _subs_ok:
            log.warning("ACHTUNG: Dieses ffmpeg kann keine Untertitel einbrennen (libass fehlt). "
                        "Clips werden OHNE Untertitel gerendert. Mac-Fix: brew install ffmpeg-full "
                        "&& brew unlink ffmpeg && brew link --force ffmpeg-full")
    return _subs_ok
W, H = 1080, 1920
HEAD_H = 220                      # Headline-Balken, liegt genau in der Bildmitte
CAM_H = (H - HEAD_H) // 2         # oben Facecam ...
GAME_H = H - HEAD_H - CAM_H       # ... unten Gameplay
FONTS = [
    "/System/Library/Fonts/Supplemental/Impact.ttf", "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc", "C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf",
]
_pil_warned = False


def make_headline(text, path, opaque):
    """Headline als PNG (1080 x HEAD_H). Kommt per overlay ins Video - braucht also kein libass."""
    global _pil_warned
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        if not _pil_warned:
            log.warning("Pillow nicht installiert -> keine Headline im Video (pip install pillow)")
            _pil_warned = True
        return False
    text = " ".join(text.upper().split())
    font_file = next((f for f in FONTS if Path(f).exists()), None)

    def font(size):
        return ImageFont.truetype(font_file, size) if font_file else ImageFont.load_default(size)

    img = Image.new("RGBA", (W, HEAD_H), (12, 12, 14, 255) if opaque else (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    words, lines, f = text.split(), [text], font(44)
    for size in range(104, 40, -4):                    # groesste Schrift, die in max. 2 Zeilen passt
        f = font(size)
        fits = lambda t: draw.textlength(t, font=f) <= W - 70
        if fits(text):
            lines = [text]
            break
        split = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
        split = [p for p in split if fits(p[0]) and fits(p[1])]
        if split and size * 2.2 <= HEAD_H - 16:
            lines = list(min(split, key=lambda p: abs(len(p[0]) - len(p[1]))))
            break
    line_h = f.size * 1.08
    y = (HEAD_H - line_h * len(lines)) / 2
    for i, line in enumerate(lines):
        x = (W - draw.textlength(line, font=f)) / 2
        colour = (255, 230, 0, 255) if (i == len(lines) - 1 and len(lines) > 1) else (255, 255, 255, 255)
        draw.text((x, y + i * line_h), line, font=f, fill=colour,
                  stroke_width=0 if opaque else max(4, f.size // 12), stroke_fill=(0, 0, 0, 255))
    img.save(path)
    return True


def _ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def write_ass(words, path, has_cam):
    """Zeigt je 3 Woerter, das gerade gesprochene farbig hervorgehoben."""
    margin_v = 340 if has_cam else 560   # unterhalb der Headline, ueber der TikTok-Bedienleiste
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


def fit_window(words, duration, min_len):
    """Schnittpunkte auf Sprechpausen legen, damit der Clip nicht mitten im Satz beginnt/endet.

    Liefert (start, ende) in Sekunden; ende - start ist immer >= min_len (sofern genug Material da ist).
    """
    slack = duration - min_len
    if slack <= 1 or not words:
        return 0.0, duration
    gaps = [(0.0, words[0][0], False)]
    gaps += [(a[1], b[0], a[2][-1:] in ".!?") for a, b in zip(words, words[1:])]
    gaps.append((words[-1][1], duration, True))

    def best(lo, hi, default):
        cand = [((g1 - g0) + (0.6 if punct else 0), (g0 + g1) / 2, g0, g1) for g0, g1, punct in gaps
                if g1 - g0 >= 0.25 and lo <= (g0 + g1) / 2 <= hi]
        return max(cand)[1:] if cand else (default, default, default)

    _, _, start_gap_end = best(0.0, slack * 0.6, 0.0)
    start = max(0.0, start_gap_end - 0.15)          # kurz vor dem ersten Wort einsteigen
    _, end_gap_start, _ = best(duration - slack * 0.4, duration, duration)
    end = min(duration, end_gap_start + 0.35)       # letztes Wort ausklingen lassen
    return start, end


def render_vertical(src, dst, cam=None, words=None, start=0.0, end=None, headline=""):
    """cam = [x, y, w, h] als Bildanteile (0-1) oder None. Gibt True bei Erfolg."""
    all_words = words
    if words:
        words = [(s - start, e - start, w) for s, e, w in words if s >= start and (end is None or e <= end)]
    cut = ["-ss", f"{start:.2f}"] + (["-t", f"{end - start:.2f}"] if end else [])
    src, dst = Path(src).resolve(), Path(dst).resolve()
    if cam:
        x, y, w, h = cam
        fc = (f"[0:v]split[a][b];"
              f"[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={W}:{CAM_H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{CAM_H}[cam];"
              f"[b]scale=-2:{GAME_H},crop={W}:{GAME_H}[game];"
              f"[cam]pad={W}:{H}:0:0:black[p];[p][game]overlay=0:{CAM_H + HEAD_H}[v0]")
    else:
        fc = (f"[0:v]split[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:4[bg];"
              f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:{CAM_H}-h[v0]")   # Bild direkt ueber der Headline
    inputs = [*cut, "-i", str(src)]
    png = dst.with_suffix(".png")
    if headline and make_headline(headline, png, opaque=True):
        inputs += ["-i", png.name]
        fc += f";[v0][1:v]overlay=0:{CAM_H}[v1]"         # Headline zentral in der Bildmitte
    else:
        fc += ";[v0]null[v1]"
    ass = None
    if words and subtitles_supported():
        ass = dst.with_suffix(".ass")
        write_ass(words, ass, bool(cam))
        fc += f";[v1]subtitles={ass.name}[v]"  # relativer Name + cwd: keine Pfad-Escapes noetig
    else:
        fc += ";[v1]null[v]"
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", fc,
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-maxrate", "6M", "-bufsize", "12M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", dst.name],
        cwd=dst.parent, capture_output=True, text=True)
    for tmp in (ass, png):
        if tmp:
            tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        log.warning("Render fehlgeschlagen: %s", r.stderr.strip()[-400:])
        if ass:  # zweiter Versuch ohne Untertitel, damit der Clip nicht verloren geht
            log.warning("Neuer Versuch ohne Untertitel")
            return render_vertical(src, dst, cam, None, start, end, headline)
        return False
    return True
