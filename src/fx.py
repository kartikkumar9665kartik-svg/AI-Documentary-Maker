"""Frame-level effects: fonts, text layers, ken-burns, color grade, grain, glitch, transitions."""
import subprocess
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont
from . import config as C

_font_cache = {}
_grade_cache = {}


# ----------------------------------------------------------------- fonts / text
def find_font(family="Noto Sans Devanagari", bold=True):
    key = (family, bold)
    if key in _font_cache:
        return _font_cache[key]
    pattern = family + (":bold" if bold else "")
    path = None
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if out:
            path = out
    except Exception:
        pass
    for fb in ["/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]:
        if path is None:
            try:
                open(fb, "rb").close()
                path = fb
            except OSError:
                pass
    if path and "DejaVu" in path or path and "Liberation" in path:
        print("  [font] WARNING: Devanagari font not found - Hindi text on screen may show boxes. "
              "Install: sudo apt-get install fonts-noto-core")
    _font_cache[key] = path
    return path


def get_font(size, family="Noto Sans Devanagari", bold=True):
    path = find_font(family, bold)
    size = max(8, int(size))
    try:
        try:
            return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
        except Exception:
            return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def wrap_text(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def text_layer(text, size, color=(255, 255, 255), max_w=None, family="Noto Sans Devanagari",
               bold=True, stroke=0, stroke_color=(0, 0, 0), align="center", line_gap=1.25, pad=20):
    """Returns RGBA uint8 array with the text drawn on a transparent background."""
    font = get_font(size, family, bold)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    max_w = max_w or int(C.W * 0.85)
    lines = []
    for para in str(text).split("\n"):
        lines += wrap_text(tmp, para, font, max_w)
    lh = int(size * line_gap)
    widths = [int(tmp.textlength(l, font=font)) for l in lines] or [10]
    w, h = max(widths) + 2 * pad + 2 * stroke, lh * len(lines) + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        x = pad + stroke if align == "left" else (w - widths[i]) // 2
        d.text((x, pad + i * lh), l, font=font, fill=color + (255,),
               stroke_width=stroke, stroke_fill=stroke_color + (255,))
    return np.array(img)


def paste_rgba(dst, src, cx, cy, alpha=1.0):
    """Alpha-blend RGBA (RGB order) `src` centred at (cx,cy) onto BGR `dst` in place."""
    h, w = src.shape[:2]
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    X0, Y0, X1, Y1 = max(0, x0), max(0, y0), min(dst.shape[1], x0 + w), min(dst.shape[0], y0 + h)
    if X0 >= X1 or Y0 >= Y1:
        return
    s = src[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
    a = (s[..., 3:4].astype(np.float32) / 255.0) * alpha
    roi = dst[Y0:Y1, X0:X1].astype(np.float32)
    rgb = s[..., 2::-1].astype(np.float32)  # RGB -> BGR
    dst[Y0:Y1, X0:X1] = (roi * (1 - a) + rgb * a).astype(np.uint8)


def ease(p):
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


# ----------------------------------------------------------------- camera
def camera(plate, z, cx, cy):
    """View `plate` (BGR) through a camera: zoom z about centre (cx,cy) -> C.W x C.H image."""
    ph, pw = plate.shape[:2]
    hw, hh = C.W / (2 * z), C.H / (2 * z)
    cx = min(max(cx, hw), pw - hw)
    cy = min(max(cy, hh), ph - hh)
    M = np.float32([[z, 0, C.W / 2 - z * cx], [0, z, C.H / 2 - z * cy]])
    return cv2.warpAffine(plate, M, (C.W, C.H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def make_plate(img_bgr, pw=None, ph=None):
    """Make a 1.25x-oversize plate from any photo: wide photos fill it, others sit on a blurred backdrop."""
    pw, ph = pw or int(C.W * 1.25), ph or int(C.H * 1.25)
    h, w = img_bgr.shape[:2]
    ar = w / h
    s = max(pw / w, ph / h)
    bg = cv2.resize(img_bgr, (int(w * s) + 1, int(h * s) + 1), interpolation=cv2.INTER_AREA)
    y0, x0 = (bg.shape[0] - ph) // 2, (bg.shape[1] - pw) // 2
    bg = bg[y0:y0 + ph, x0:x0 + pw].copy()
    if ar >= 1.55:
        return bg
    bg = cv2.GaussianBlur(bg, (0, 0), 25 * C.S)
    bg = (bg * 0.35).astype(np.uint8)
    fh = int(ph * 0.86)
    fw = int(fh * ar)
    if fw > pw * 0.9:
        fw = int(pw * 0.9)
        fh = int(fw / ar)
    fg = cv2.resize(img_bgr, (fw, fh), interpolation=cv2.INTER_AREA)
    b = max(6, int(8 * C.S))
    fg = cv2.copyMakeBorder(fg, b, b, b, b, cv2.BORDER_CONSTANT, value=(225, 230, 235))
    fh2, fw2 = fg.shape[:2]
    y, x = (ph - fh2) // 2, (pw - fw2) // 2
    sh = np.zeros((ph, pw), np.uint8)
    sh[y + 14:y + 14 + fh2, x + 14:x + 14 + fw2] = 150
    sh = cv2.GaussianBlur(sh, (0, 0), 18 * C.S)
    bg = (bg.astype(np.float32) * (1 - sh[..., None] / 255.0 * 0.7)).astype(np.uint8)
    bg[y:y + fh2, x:x + fw2] = fg
    return bg


def motion_params(mode, p, pw, ph, zoom_amt=0.30, pan=1.0, focus=None):
    """Camera keyframes for ken-burns modes. p in 0..1 -> (z, cx, cy).
    focus=(fx,fy) fractions of the plate: zoom moves toward / away from that point."""
    e = ease(p)
    cx, cy = pw / 2, ph / 2
    fxp, fyp = (focus[0] * pw, focus[1] * ph) if focus else (cx, cy)
    h = 0.18 * pan
    if mode == "zoom_out":
        k = 1 - e
        return 1.0 + zoom_amt * k, cx + (fxp - cx) * k * 0.8, cy + (fyp - cy) * k * 0.8
    if mode == "pan_left":
        return 1.18, pw * (0.5 + h - 2 * h * p), cy
    if mode == "pan_right":
        return 1.18, pw * (0.5 - h + 2 * h * p), cy
    if mode == "pan_up":
        return 1.18, cx, ph * (0.5 + h - 2 * h * p)
    if mode == "pan_down":
        return 1.18, cx, ph * (0.5 - h + 2 * h * p)
    if mode == "push_left":  # zoom in drifting left
        return 1.0 + zoom_amt * e, pw * (0.56 - 0.1 * e), cy
    return 1.0 + zoom_amt * e, cx + (fxp - cx) * e * 0.8, cy + (fyp - cy) * e * 0.8  # zoom_in


# ----------------------------------------------------------------- grading
def _lut_for(style):
    if style in _grade_cache:
        return _grade_cache[style]
    x = np.arange(256, dtype=np.float32) / 255.0
    s = x + 0.18 * np.sin((x - 0.5) * np.pi) * 0.5 * 2  # gentle S-curve
    s = np.clip(s, 0, 1)
    if style == "sepia":
        r, g, b = s * 1.05, s * 0.92, s * 0.72
    elif style == "bw":
        r = g = b = s
    else:  # documentary: warm highlights, teal-ish shadows
        r = np.clip(s * 1.04 + 0.02 * (1 - s) * 0, 0, 1)
        g = s
        b = np.clip(s * 0.96 + 0.035 * (1 - s), 0, 1)
    lut = np.stack([b, g, r], axis=-1)
    lut = (np.clip(lut, 0, 1) * 255).astype(np.uint8).reshape(256, 1, 3)
    _grade_cache[style] = lut
    return lut


def _lut_custom(contrast, warmth):
    key = ("c", round(contrast, 3), round(warmth, 3))
    if key not in _grade_cache:
        x = np.arange(256, dtype=np.float32) / 255.0
        s = np.clip((x - 0.5) * contrast + 0.5, 0, 1)
        r = np.clip(s * (1 + warmth), 0, 1)
        b = np.clip(s * (1 - warmth) + 0.03 * (1 - s), 0, 1)
        lut = (np.stack([b, s, r], -1) * 255).astype(np.uint8).reshape(256, 1, 3)
        _grade_cache[key] = lut
    return _grade_cache[key]


_vig = {}


def _vignette(strength=0.42):
    k = (C.W, C.H, round(strength, 3))
    if k not in _vig:
        yy, xx = np.mgrid[0:C.H, 0:C.W].astype(np.float32)
        d = np.sqrt(((xx - C.W / 2) / (C.W / 2)) ** 2 + ((yy - C.H / 2) / (C.H / 2)) ** 2)
        _vig[k] = np.clip(1.0 - strength * np.clip(d - 0.45, 0, 1) ** 1.6, 0.3, 1.0)[..., None].astype(np.float32)
    return _vig[k]


_rng = np.random.default_rng(7)


def grade(frame, style="documentary", grain=0.05, t=0.0, dust=True, params=None):
    """Full cinematic look: desaturate + tint curve + vignette + film grain + flicker + dust."""
    f = frame
    vstrength = 0.42
    if params:  # style learned from a reference video / preset
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        g3 = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        sat = params.get("sat", 0.62)
        f = cv2.addWeighted(f, sat, g3, 1 - sat, 0) if sat <= 1.0 else cv2.addWeighted(f, sat, g3, 1 - sat, 0)
        f = cv2.LUT(f, _lut_custom(params.get("contrast", 1.0), params.get("warmth", 0.03)))
        vstrength = params.get("vignette", 0.42)
    elif style in ("documentary", "sepia", "bw"):
        sat = {"documentary": 0.62, "sepia": 0.15, "bw": 0.0}[style]
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        g3 = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        f = cv2.addWeighted(f, sat, g3, 1 - sat, 0)
        f = cv2.LUT(f, _lut_for(style))
    out = f.astype(np.float32) * _vignette(vstrength)
    if grain > 0:
        gh, gw = C.H // 2, C.W // 2
        n = _rng.normal(0, 255 * grain, (gh, gw)).astype(np.float32)
        n = cv2.resize(n, (C.W, C.H), interpolation=cv2.INTER_LINEAR)
        out += n[..., None]
    out *= 1.0 + 0.015 * np.sin(t * 37.0) + _rng.normal(0, 0.004)  # projector flicker
    out = np.clip(out, 0, 255).astype(np.uint8)
    if dust and _rng.random() < 0.10:  # occasional scratch / hair
        x = int(_rng.integers(0, C.W))
        cv2.line(out, (x, 0), (x + int(_rng.integers(-3, 3)), C.H), (200, 200, 200), 1)
    if dust and _rng.random() < 0.25:
        for _ in range(int(_rng.integers(2, 7))):
            cv2.circle(out, (int(_rng.integers(0, C.W)), int(_rng.integers(0, C.H))),
                       int(_rng.integers(1, 3)), (190, 190, 190), -1)
    return out


# ----------------------------------------------------------------- transitions
def glitch_shift(f, amount):
    out = f.copy()
    b, g, r = cv2.split(out)
    dx = int(amount * 25 * C.S)
    out = cv2.merge([np.roll(b, dx, 1), g, np.roll(r, -dx, 1)])
    for _ in range(int(4 + amount * 10)):
        y = int(_rng.integers(0, C.H - 10))
        h = int(_rng.integers(6, int(60 * C.S) + 7))
        out[y:y + h] = np.roll(out[y:y + h], int(_rng.integers(-200, 200) * amount), axis=1)
    return out


def transition(a, b, p, kind):
    """Blend frame a -> b at progress p (0..1)."""
    if kind == "cut":
        return b if p >= 0.5 else a
    if kind == "dip":
        if p < 0.5:
            return (a.astype(np.float32) * (1 - 2 * p)).astype(np.uint8)
        return (b.astype(np.float32) * (2 * p - 1)).astype(np.uint8)
    if kind == "flash":
        base = cv2.addWeighted(a, 1 - p, b, p, 0)
        k = 1 - abs(2 * p - 1)
        return cv2.addWeighted(base, 1 - k * 0.9, np.full_like(base, 255), k * 0.9, 0)
    if kind == "glitch":
        base = a if p < 0.5 else b
        amt = 1 - abs(2 * p - 1)
        out = glitch_shift(base, amt)
        noise = _rng.integers(0, 60, out.shape, dtype=np.uint8)
        return cv2.addWeighted(out, 1 - 0.25 * amt, noise, 0.25 * amt, 0)
    if kind == "whip":
        e = ease(p)
        dx = int(C.W * e)
        out = np.concatenate([a, b], axis=1)[:, dx:dx + C.W]
        k = int(1 + 120 * C.S * (1 - abs(2 * e - 1)))
        return cv2.blur(out, (max(1, k), 1)) if k > 1 else out
    return cv2.addWeighted(a, 1 - ease(p), b, ease(p), 0)  # fade
