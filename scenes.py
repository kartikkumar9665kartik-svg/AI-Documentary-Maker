"""Scene renderers. Each scene: __init__(spec, L) ; prepare(ctx) ; frame(t) -> BGR uint8 (C.H x C.W).
L = scene length in seconds (narration time + transition overlap)."""
import math, os, subprocess
import numpy as np
import cv2
from PIL import Image, ImageDraw
from . import config as C
from . import fx, fetch_media as fm

RED = (38, 38, 205)  # BGR
_rng = np.random.default_rng(11)


# ------------------------------------------------------------------ helpers
def load_bgr(path, max_w=3200):
    if not path:
        return None
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    if img.shape[1] > max_w:
        s = max_w / img.shape[1]
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return img


def get_image(spec, ctx, key_img="image", key_q="query"):
    p = fm.resolve_local(spec.get(key_img), "images")
    if not p and spec.get(key_q):
        p = fm.find_image(spec[key_q], ctx["used"])
    return load_bgr(p)


def noise_tex(h, w, base, amp=14, blur=1.2, blotch=0.0):
    n = _rng.normal(0, amp, (h, w)).astype(np.float32)
    n = cv2.GaussianBlur(n, (0, 0), blur)
    tex = np.array(base, np.float32)[None, None, :] + n[..., None]
    if blotch:
        b = cv2.resize(_rng.normal(0, 1, (max(2, h // 150), max(2, w // 150))).astype(np.float32),
                       (w, h), interpolation=cv2.INTER_CUBIC)
        tex *= (1 + blotch * b[..., None])
    return np.clip(tex, 0, 255).astype(np.uint8)


def sepia_tone(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    return np.clip(np.stack([g * 0.62, g * 0.82, g * 1.0], -1) * 255, 0, 255).astype(np.uint8)


def fallback_card(text, label=""):
    """Used when no image could be found: dark textured card with the keywords typed on it."""
    pw, ph = int(C.W * 1.25), int(C.H * 1.25)
    bg = noise_tex(ph, pw, (34, 34, 38), 10, 2, 0.15)
    lay = fx.text_layer(text or label or "", 110 * C.S, (230, 230, 230), max_w=int(pw * 0.7))
    fx.paste_rgba(bg, lay, pw // 2, ph // 2)
    return bg


def lerp(a, b, e):
    return a + (b - a) * e


# ------------------------------------------------------------------ photo
class PhotoScene:
    def __init__(self, spec, L):
        self.s, self.L = spec, L

    def prepare(self, ctx, plate=None):
        if plate is not None:
            self.plate = plate
        else:
            img = get_image(self.s, ctx)
            if img is None:
                self.plate = fallback_card(self.s.get("query", ""))
            else:
                if self.s.get("sepia"):
                    img = sepia_tone(img)
                self.plate = fx.make_plate(img)
        self.mode = self.s.get("motion", "zoom_in")
        self.zoom_amt = self.s.get("zoom_amt", 0.30)
        self.pan_scale = self.s.get("pan_scale", 1.0)
        self.focus = (self.s["focus_x"], self.s["focus_y"]) if "focus_x" in self.s else None
        self.circle = self.s.get("circle")  # {"x":0.5,"y":0.4,"r":0.08,"at":1.5}

    def frame(self, t):
        p = min(1.0, t / self.L)
        ph, pw = self.plate.shape[:2]
        z, cx, cy = fx.motion_params(self.mode, p, pw, ph, self.zoom_amt, self.pan_scale, self.focus)
        src = self.plate
        if self.circle:
            c = self.circle
            at = c.get("at", self.L * 0.35)
            prog = fx.ease((t - at) / 0.9)
            if prog > 0:
                src = self.plate.copy()
                ctr = (int(c["x"] * pw), int(c["y"] * ph))
                r = int(c.get("r", 0.08) * ph)
                for off, th in ((0, 7), (5, 4)):
                    cv2.ellipse(src, ctr, (int(r * 1.25) + off, r + off), -15, 0, 360 * prog, RED,
                                max(2, int(th * C.S * 1.3)), cv2.LINE_AA)
        return fx.camera(src, z, cx, cy)


# ------------------------------------------------------------------ several quick shots in one scene (pacing)
class ShotSeq:
    """Splits one narration scene into n quick photo shots (different camera moves/crops/transitions),
    so the pacing matches the reference style. Uses spec['queries'] (several images) if given."""

    def __init__(self, spec, L, recipe, seed=0, n=2):
        self.s, self.L, self.n = spec, L, n
        rng = np.random.default_rng(seed)
        mw = dict(recipe["motion_weights"])
        modes, last = [], None
        for i in range(n):
            w = {k: v for k, v in mw.items() if k != last} or mw
            ks = list(w)
            pr = np.array([w[k] for k in ks], float)
            m = str(rng.choice(ks, p=pr / pr.sum()))
            last = m
            modes.append("zoom_in" if m == "static" else m)
        tw = recipe["transition_weights"]
        tk = list(tw)
        tp = np.array([tw[k] for k in tk], float)
        self.kinds = [str(rng.choice(tk, p=tp / tp.sum())) for _ in range(n - 1)]
        self.tr = [0.0 if k == "cut" else 0.35 for k in self.kinds]
        self.seg = L / n
        self.starts = [i * self.seg - (self.tr[i - 1] / 2 if i > 0 else 0) for i in range(n)]
        self.ends = [(i + 1) * self.seg + (self.tr[i] / 2 if i < n - 1 else 0) for i in range(n)]
        qs = spec.get("queries") or ([spec["query"]] if spec.get("query") else [None])
        self.subs = []
        for i in range(n):
            sub = dict(spec, motion=modes[i], zoom_amt=recipe["zoom_amt"], pan_scale=recipe["pan_scale"],
                       focus_x=float(rng.uniform(0.3, 0.7)), focus_y=float(rng.uniform(0.35, 0.65)),
                       query=qs[i % len(qs)])
            if "image" in spec and len(qs) > 1:
                sub.pop("image")
            if i > 0:
                sub.pop("circle", None)
            self.subs.append(PhotoScene(sub, self.ends[i] - self.starts[i]))

    def cut_times(self):
        return [(i + 1) * self.seg for i in range(self.n - 1)]

    def prepare(self, ctx):
        cache = {}
        for sub in self.subs:
            key = sub.s.get("query") or sub.s.get("image")
            sub.prepare(ctx, plate=cache.get(key))
            cache[key] = sub.plate

    def frame(self, t):
        j = int(round(t / self.seg))
        if 1 <= j <= self.n - 1 and self.tr[j - 1] > 0 and abs(t - j * self.seg) <= self.tr[j - 1] / 2:
            p = (t - (j * self.seg - self.tr[j - 1] / 2)) / self.tr[j - 1]
            a = self.subs[j - 1].frame(t - self.starts[j - 1])
            b = self.subs[j].frame(t - self.starts[j])
            return fx.transition(a, b, min(1.0, max(0.0, p)), self.kinds[j - 1])
        i = min(self.n - 1, int(t // self.seg))
        return self.subs[i].frame(max(0.0, t - self.starts[i]))


# ------------------------------------------------------------------ evidence board
def make_polaroid(img, label, pw, tilt):
    border, ph_photo = int(pw * 0.06), int(pw * 0.76)
    bottom = int(pw * 0.26)
    w0, h0 = pw, border + ph_photo + bottom
    card = Image.new("RGBA", (w0, h0), (235, 229, 214, 255))
    if img is None:
        img = fallback_card(label)[:300, :400]
    tw, th = w0 - 2 * border, ph_photo
    ih, iw = img.shape[:2]
    s = max(tw / iw, th / ih)
    r = cv2.resize(img, (int(iw * s) + 1, int(ih * s) + 1), interpolation=cv2.INTER_AREA)
    y0, x0 = (r.shape[0] - th) // 2, (r.shape[1] - tw) // 2
    r = r[y0:y0 + th, x0:x0 + tw]
    gray = cv2.cvtColor(r, cv2.COLOR_BGR2GRAY)
    r = cv2.addWeighted(r, 0.45, cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), 0.55, 0)
    card.paste(Image.fromarray(cv2.cvtColor(r, cv2.COLOR_BGR2RGB)), (border, border))
    d = ImageDraw.Draw(card)
    size = pw * 0.10
    font = fx.get_font(size, "Noto Sans Devanagari", True)
    while d.textlength(label, font=font) > w0 - 2 * border and size > 12:
        size *= 0.92
        font = fx.get_font(size, "Noto Sans Devanagari", True)
    d.text(((w0 - d.textlength(label, font=font)) / 2, border + ph_photo + bottom * 0.18), label,
           font=font, fill=(30, 30, 30, 255))
    pr = int(pw * 0.035)
    d.ellipse([w0 / 2 - pr, border * 0.2, w0 / 2 + pr, border * 0.2 + 2 * pr], fill=(200, 30, 30, 255))
    pad = 60
    canvas = Image.new("RGBA", (w0 + 2 * pad, h0 + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rectangle([pad + 10, pad + 14, pad + w0 + 10, pad + h0 + 14], fill=(0, 0, 0, 150))
    from PIL import ImageFilter
    sh = sh.filter(ImageFilter.GaussianBlur(10))
    canvas = Image.alpha_composite(canvas, sh)
    canvas.alpha_composite(card, (pad, pad))
    canvas = canvas.rotate(tilt, resample=Image.BICUBIC, expand=True)
    pin_off = (0, -(h0 / 2 - border * 0.2 - pr))
    a = math.radians(tilt)
    pin_off = (pin_off[0] * math.cos(a) + pin_off[1] * math.sin(a),
               -pin_off[0] * math.sin(a) + pin_off[1] * math.cos(a))
    return np.array(canvas), pin_off


class BoardScene:
    def __init__(self, spec, L):
        self.s, self.L = spec, L

    def prepare(self, ctx):
        items = self.s.get("items", [])
        n = max(1, len(items))
        self.BW, self.BH = int(C.W * 1.7), int(C.H * 1.7)
        bg = noise_tex(self.BH, self.BW, (62, 98, 140), 26, 1.4, 0.10)
        yy, xx = np.mgrid[0:self.BH, 0:self.BW].astype(np.float32)
        d = np.sqrt(((xx - self.BW / 2) / (self.BW / 2)) ** 2 + ((yy - self.BH / 2) / (self.BH / 2)) ** 2)
        bg = (bg * np.clip(1.15 - 0.45 * d, 0.45, 1.1)[..., None]).astype(np.uint8)
        self.base0 = bg
        rows = 1 if n <= 3 else 2
        cols = math.ceil(n / rows)
        rng = np.random.default_rng(self.s.get("seed", 3))
        pw = int(min(self.BW / cols * 0.66, 620 * C.S))
        self.items = []
        for i, it in enumerate(items):
            r, c = divmod(i, cols)
            x = self.BW * (c + 0.5) / cols + rng.uniform(-0.03, 0.03) * self.BW
            y = self.BH * (r + 0.5) / rows + rng.uniform(-0.04, 0.04) * self.BH
            img = get_image(it, ctx)
            tilt = rng.uniform(-7, 7)
            rgba, pin = make_polaroid(img, it.get("label", ""), pw, tilt)
            self.items.append(dict(x=x, y=y, rgba=rgba, pin=(x + pin[0], y + pin[1]),
                                   t0=0.5 + i * min(1.1, self.L * 0.45 / n)))
        self.strings = self.s.get("strings") or [[i, i + 1] for i in range(len(self.items) - 1)]
        self.focus = self.s.get("focus", len(self.items) - 1)
        self.circle = self.s.get("circle")
        self.base = self.base0.copy()
        self.baked, self.last_t = set(), -1

    def _reset(self):
        self.base = self.base0.copy()
        self.baked = set()

    def _string(self, img, a, b, prog):
        if prog <= 0:
            return
        p0, p1 = np.array(self.items[a]["pin"]), np.array(self.items[b]["pin"])
        mid = (p0 + p1) / 2 + np.array([0, 0.10 * np.linalg.norm(p1 - p0) + 20])
        ts = np.linspace(0, prog, 40)[:, None]
        pts = ((1 - ts) ** 2) * p0 + 2 * (1 - ts) * ts * mid + (ts ** 2) * p1
        pts = pts.astype(np.int32)
        cv2.polylines(img, [pts + np.array([4, 6])], False, (15, 25, 40), max(2, int(4 * C.S)), cv2.LINE_AA)
        cv2.polylines(img, [pts], False, RED, max(2, int(4 * C.S)), cv2.LINE_AA)

    def frame(self, t):
        if t < self.last_t:
            self._reset()
        self.last_t = t
        pop = 0.45
        for i, it in enumerate(self.items):
            a = (t - it["t0"]) / pop
            if a >= 1 and i not in self.baked:
                fx.paste_rgba(self.base, it["rgba"], it["x"], it["y"])
                self.baked.add(i)
        img = self.base.copy()
        for i, it in enumerate(self.items):
            a = (t - it["t0"]) / pop
            if 0 < a < 1:
                e = fx.ease(a)
                sc = 0.55 + 0.45 * e
                small = cv2.resize(it["rgba"], None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
                fx.paste_rgba(img, small, it["x"], it["y"], alpha=e)
        for a, b in self.strings:
            if a < len(self.items) and b < len(self.items):
                st = max(self.items[a]["t0"], self.items[b]["t0"]) + 0.35
                self._string(img, a, b, fx.ease((t - st) / 0.9))
        f = min(self.focus, len(self.items) - 1)
        fp = self.items[f]
        if self.circle is not None and self.circle < len(self.items):
            c = self.items[self.circle]
            prog = fx.ease((t - max(self.L * 0.5, c["t0"] + 0.8)) / 0.9)
            if prog > 0:
                rw, rh = int(self.items[0]["rgba"].shape[1] * 0.42), int(self.items[0]["rgba"].shape[0] * 0.42)
                ctr = (int(c["x"]), int(c["y"] - rh * 0.1))
                for off, th in ((0, 7), (6, 4)):
                    cv2.ellipse(img, ctr, (rw + off, rh + off), -12, 0, 360 * prog, RED,
                                max(2, int(th * C.S * 1.4)), cv2.LINE_AA)
        p = fx.ease((t - 0.3) / max(0.5, self.L - 0.9))
        z = lerp(0.64, 1.12, p)
        cx = lerp(self.BW / 2, fp["x"], p)
        cy = lerp(self.BH / 2, fp["y"], p)
        cx += 12 * math.sin(t * 0.9)  # slight handheld drift
        cy += 8 * math.cos(t * 0.7)
        return fx.camera(img, z, cx, cy)


# ------------------------------------------------------------------ newspaper / document
class PaperScene:
    """kind 'newspaper' (headline + columns + photo) or 'document' (typed lines + optional stamp)."""

    def __init__(self, spec, L):
        self.s, self.L = spec, L

    def prepare(self, ctx):
        s = self.s
        PW, PH = int(1500 * C.S), int(1000 * C.S)
        self.PW, self.PH = PW, PH
        paper = noise_tex(PH, PW, (178, 205, 222), 9, 1.3, 0.05)
        yy, xx = np.mgrid[0:PH, 0:PW].astype(np.float32)
        edge = np.minimum.reduce([xx, PW - xx, yy, PH - yy]) / (60 * C.S)
        paper = (paper * np.clip(0.55 + 0.45 * edge, 0.55, 1)[..., None]).astype(np.uint8)
        pil = Image.fromarray(cv2.cvtColor(paper, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)
        ink = (28, 24, 22)
        m = int(60 * C.S)
        regions = []
        serif = "Noto Serif Devanagari"
        if s.get("kind") == "document":
            d.text((m, m * 0.7), s.get("header", "OFFICIAL RECORD"), font=fx.get_font(34 * C.S, serif, True), fill=ink)
            d.line([m, m * 0.7 + 50 * C.S, PW - m, m * 0.7 + 50 * C.S], fill=ink, width=3)
            font = fx.get_font(40 * C.S, "Noto Sans Devanagari", False)
            lines = s.get("lines", ["..."])
            y = m * 0.7 + 90 * C.S
            blank = pil.copy()
            for ln in lines:
                for sub in fx.wrap_text(d, ln, font, PW - 2 * m):
                    d.text((m, y), sub, font=font, fill=ink)
                    regions.append((int(m - 5), int(y - 5), int(m + d.textlength(sub, font=font) + 10), int(y + 56 * C.S)))
                    y += 62 * C.S
            self.blank = cv2.cvtColor(np.array(blank), cv2.COLOR_RGB2BGR)
        else:
            name = s.get("paper_name", "The Daily Chronicle")
            f1 = fx.get_font(70 * C.S, "Noto Serif Devanagari", True)
            d.text(((PW - d.textlength(name, font=f1)) / 2, 18 * C.S), name, font=f1, fill=ink)
            d.line([m, 110 * C.S, PW - m, 110 * C.S], fill=ink, width=4)
            d.text((m, 118 * C.S), s.get("date", ""), font=fx.get_font(26 * C.S, serif, False), fill=ink)
            # column placeholders + optional photo
            img = get_image(s, ctx)
            cy0 = int(420 * C.S)
            colw = (PW - 2 * m - 3 * 30 * C.S) / 4
            for c in range(4):
                x0 = m + c * (colw + 30 * C.S)
                if c == 3 and img is not None:
                    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    th = int(colw * 0.8)
                    g = cv2.resize(g, (int(colw), th), interpolation=cv2.INTER_AREA)
                    pil.paste(Image.fromarray(g).convert("RGB"), (int(x0), cy0))
                    cy_start = cy0 + th + 14
                else:
                    cy_start = cy0
                y = cy_start
                while y < PH - m:
                    wl = colw * float(_rng.uniform(0.82, 1.0))
                    d.rectangle([x0, y, x0 + wl, y + 7 * C.S], fill=(95, 92, 88))
                    y += 20 * C.S
            blank = pil.copy()
            hfont = fx.get_font(88 * C.S, serif, True)
            y = 150 * C.S
            for sub in fx.wrap_text(d, s.get("headline", "BREAKING NEWS"), hfont, PW - 2 * m):
                d.text((m, y), sub, font=hfont, fill=ink)
                regions.append((m - 5, int(y), int(m + d.textlength(sub, font=hfont) + 10), int(y + 110 * C.S)))
                y += 108 * C.S
            if s.get("sub"):
                sf = fx.get_font(36 * C.S, serif, False)
                d.text((m, y + 6), s["sub"], font=sf, fill=(60, 55, 50))
            self.blank = cv2.cvtColor(np.array(blank), cv2.COLOR_RGB2BGR)
        self.full = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        self.regions = regions
        self.total = sum(r[2] - r[0] for r in regions) or 1
        self.desk = noise_tex(C.H, C.W, (28, 38, 52), 8, 2.5, 0.2)
        self.stamp = None
        if s.get("stamp"):
            lay = fx.text_layer(s["stamp"], 90 * C.S, (200, 30, 30), stroke=0, pad=26)
            im = Image.fromarray(lay)
            ImageDraw.Draw(im).rectangle([4, 4, im.width - 5, im.height - 5], outline=(200, 30, 30, 255), width=8)
            self.stamp = np.array(im.rotate(14, expand=True, resample=Image.BICUBIC))

    def frame(self, t):
        L = self.L
        cur = self.blank.copy()
        reveal_start, reveal_len = 0.9, min(3.0, L * 0.45)
        left = fx.ease((t - reveal_start) / reveal_len) * self.total
        for (x0, y0, x1, y1) in self.regions:
            w = x1 - x0
            k = int(min(w, max(0, left)))
            if k > 0:
                cur[y0:y1, x0:x0 + k] = self.full[y0:y1, x0:x0 + k]
            left -= w
        if self.stamp is not None:
            sa = self.s.get("stamp_at", L * 0.55)
            a = (t - sa) / 0.18
            if a > 0:
                sc = 1.0 + 1.2 * (1 - fx.ease(a))
                st = cv2.resize(self.stamp, None, fx=sc, fy=sc)
                fx.paste_rgba(cur, st, self.PW * 0.68, self.PH * 0.62, alpha=min(1.0, a) * 0.92)
        # place on desk: slide in, settle, slow push-in
        e = fx.ease(t / 0.8)
        scale = lerp(0.72, 0.84, fx.ease(t / L)) * lerp(0.92, 1.0, e) * (C.W / (self.PW * 1.18))
        ang = lerp(-9, -1.5, e)
        ty = C.H / 2 + (1 - e) * C.H * 0.7
        M = cv2.getRotationMatrix2D((self.PW / 2, self.PH / 2), ang, scale)
        M[0, 2] += C.W / 2 - self.PW / 2
        M[1, 2] += ty - self.PH / 2
        warped = cv2.warpAffine(cur, M, (C.W, C.H), flags=cv2.INTER_LINEAR)
        mask = cv2.warpAffine(np.full((self.PH, self.PW), 255, np.uint8), M, (C.W, C.H))
        sh = cv2.GaussianBlur(np.roll(np.roll(mask, 14, 0), 14, 1), (0, 0), 14 * C.S)
        out = (self.desk.astype(np.float32) * (1 - sh[..., None] / 255.0 * 0.6))
        m3 = (mask[..., None] / 255.0)
        out = out * (1 - m3) + warped.astype(np.float32) * m3
        return np.clip(out, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ map
class MapScene:
    def __init__(self, spec, L):
        self.s, self.L = spec, L

    def prepare(self, ctx):
        s = self.s
        pw, ph = int(C.W * 1.25), int(C.H * 1.25)
        img = get_image(s, ctx)
        bg = noise_tex(ph, pw, (30, 40, 52), 8, 2, 0.15)
        if img is None:
            img = noise_tex(int(ph * 0.86), int(pw * 0.86), (150, 190, 215), 14, 2, 0.12)
            note = fx.text_layer(s.get("title", s.get("query", "MAP")), 70 * C.S, (60, 60, 70), pad=10)
            fx.paste_rgba(img, note, img.shape[1] // 2, 80)
        img = sepia_tone(img) if s.get("sepia", True) else img
        ih, iw = img.shape[:2]
        sc = min(pw * 0.94 / iw, ph * 0.94 / ih)
        mw, mh = int(iw * sc), int(ih * sc)
        img = cv2.resize(img, (mw, mh), interpolation=cv2.INTER_AREA)
        self.mx, self.my = (pw - mw) // 2, (ph - mh) // 2
        self.mw, self.mh = mw, mh
        bg[self.my:self.my + mh, self.mx:self.mx + mw] = img
        self.plate = bg
        self.pts = [(self.mx + p["x"] * mw, self.my + p["y"] * mh, p.get("label", "")) for p in s.get("points", [])]
        self.labels = [fx.text_layer(l, 44 * C.S, (255, 255, 255), stroke=4, pad=6) if l else None
                       for (_, _, l) in self.pts]
        self.regions = s.get("regions", [])

    def _head(self, prog):
        """Position along the polyline at progress prog (0..1)."""
        P = [(x, y) for x, y, _ in self.pts]
        if len(P) < 2:
            return P[0] if P else (self.plate.shape[1] / 2, self.plate.shape[0] / 2), 1
        lens = [math.dist(P[i], P[i + 1]) for i in range(len(P) - 1)]
        tot, d = sum(lens), prog * sum(lens)
        for i, l in enumerate(lens):
            if d <= l or i == len(lens) - 1:
                k = min(1, d / l) if l else 1
                return (lerp(P[i][0], P[i + 1][0], k), lerp(P[i][1], P[i + 1][1], k)), i + 1 + (k >= 1)
            d -= l

    def frame(self, t):
        ph, pw = self.plate.shape[:2]
        img = self.plate.copy()
        pw_, ph_ = pw, ph
        for r in self.regions:
            a = fx.ease((t - r.get("at", 1.0)) / 1.2) * 0.45
            if a > 0:
                poly = np.array([[self.mx + x * self.mw, self.my + y * self.mh] for x, y in r["poly"]], np.int32)
                ov = img.copy()
                cv2.fillPoly(ov, [poly], RED)
                cv2.polylines(ov, [poly], True, (20, 20, 120), max(2, int(3 * C.S)), cv2.LINE_AA)
                img = cv2.addWeighted(ov, a, img, 1 - a, 0)
        start, end = 0.8, max(1.5, self.L - 1.0)
        prog = fx.ease((t - start) / (end - start))
        head, reached = self._head(prog) if self.pts else ((pw_ / 2, ph_ / 2), 0)
        if len(self.pts) >= 2 and prog > 0:
            P = [(x, y) for x, y, _ in self.pts]
            path = P[:int(reached)] + [head]
            pts = np.array(path, np.int32)
            for i in range(len(pts) - 1):  # dashed look
                a, b = pts[i].astype(float), pts[i + 1].astype(float)
                n = max(1, int(np.linalg.norm(b - a) / (26 * C.S)))
                for k in range(n):
                    p0 = a + (b - a) * (k / n)
                    p1 = a + (b - a) * ((k + 0.62) / n)
                    cv2.line(img, tuple(p0.astype(int)), tuple(p1.astype(int)), RED, max(3, int(7 * C.S)), cv2.LINE_AA)
            if len(pts) >= 2:
                v = pts[-1] - pts[-2]
                nv = np.linalg.norm(v)
                if nv > 1:
                    u = v / nv
                    tip = pts[-1]
                    L1 = tip - (u * 34 * C.S) + np.array([-u[1], u[0]]) * 18 * C.S
                    L2 = tip - (u * 34 * C.S) - np.array([-u[1], u[0]]) * 18 * C.S
                    cv2.fillConvexPoly(img, np.array([tip, L1, L2], np.int32), RED, cv2.LINE_AA)
        for i, (x, y, _) in enumerate(self.pts):
            if i < reached or (len(self.pts) == 1 and t > start):
                pulse = (t * 1.4 + i * 0.3) % 1.0
                cv2.circle(img, (int(x), int(y)), int((14 + 30 * pulse) * C.S), RED, max(2, int(3 * C.S)), cv2.LINE_AA)
                cv2.circle(img, (int(x), int(y)), int(11 * C.S), (255, 255, 255), -1, cv2.LINE_AA)
                cv2.circle(img, (int(x), int(y)), int(8 * C.S), RED, -1, cv2.LINE_AA)
                if self.labels[i] is not None:
                    fx.paste_rgba(img, self.labels[i], x, y - 60 * C.S)
        p = fx.ease(t / self.L)
        z = 1.0 + 0.5 * p
        cx = lerp(pw_ / 2, head[0], 0.75 * p)
        cy = lerp(ph_ / 2, head[1], 0.75 * p)
        return fx.camera(img, z, cx, cy)


# ------------------------------------------------------------------ video clip
class VideoScene:
    def __init__(self, spec, L):
        self.s, self.L = spec, L

    def prepare(self, ctx):
        p = fm.resolve_local(self.s.get("clip"), "clips")
        if not p and self.s.get("query"):
            p = fm.find_video(self.s["query"])
        self.cap, self.fallback = None, None
        if p:
            os.makedirs(C.WORK_DIR, exist_ok=True)
            out = os.path.join(C.WORK_DIR, f"clip_{abs(hash(p + str(self.L))) % 10**8}.mp4")
            vf = (f"scale={C.W}:{C.H}:force_original_aspect_ratio=increase,crop={C.W}:{C.H},"
                  f"fps={C.FPS},eq=saturation=0.8")
            ss = ["-ss", str(self.s.get("start", 0))]
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", *ss, "-i", p, "-t", f"{self.L:.2f}",
                   "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(out):
                self.cap = cv2.VideoCapture(out)
        if self.cap is None:
            print("  [video] no clip -> using photo fallback")
            self.fallback = PhotoScene(dict(self.s, motion="zoom_in"), self.L)
            self.fallback.prepare(ctx)
        self.idx, self.last = -1, None

    def frame(self, t):
        if self.fallback:
            return self.fallback.frame(t)
        want = int(t * C.FPS)
        while self.idx < want:
            ok, fr = self.cap.read()
            if not ok:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, fr = self.cap.read()
                if not ok:
                    break
            self.last, self.idx = fr, self.idx + 1
        if self.last is None:
            return np.zeros((C.H, C.W, 3), np.uint8)
        z = 1.0 + 0.06 * t / self.L
        return fx.camera(self.last, z, C.W / 2, C.H / 2)


# ------------------------------------------------------------------ title card
class TitleScene:
    def __init__(self, spec, L):
        self.s, self.L = spec, L

    def prepare(self, ctx):
        self.bg = noise_tex(int(C.H * 1.25), int(C.W * 1.25), (26, 26, 30), 9, 2.2, 0.2)
        img = get_image(self.s, ctx)
        if img is not None:
            p = fx.make_plate(img)
            self.bg = (p * 0.35).astype(np.uint8)
        self.title = fx.text_layer(self.s.get("text", ""), 120 * C.S, (240, 240, 240), stroke=0)
        self.sub = fx.text_layer(self.s.get("sub", ""), 54 * C.S, (200, 70, 70)) if self.s.get("sub") else None

    def frame(self, t):
        ph, pw = self.bg.shape[:2]
        f = fx.camera(self.bg, 1.0 + 0.1 * t / self.L, pw / 2, ph / 2)
        a = fx.ease(t / 0.9)
        tl = self.title
        sc = 1.12 - 0.12 * a
        fx.paste_rgba(f, cv2.resize(tl, None, fx=sc, fy=sc), C.W / 2, C.H / 2 - 20, alpha=a)
        w = int(tl.shape[1] * 0.8 * fx.ease((t - 0.5) / 0.9))
        if w > 0:
            cv2.rectangle(f, (C.W // 2 - w // 2, int(C.H / 2 + tl.shape[0] / 2 - 6)),
                          (C.W // 2 + w // 2, int(C.H / 2 + tl.shape[0] / 2 + 4)), RED, -1)
        if self.sub is not None:
            fx.paste_rgba(f, self.sub, C.W / 2, C.H / 2 + tl.shape[0] / 2 + 70 * C.S, alpha=fx.ease((t - 1.0) / 0.8))
        return f


# ------------------------------------------------------------------ overlay (important text animation)
class Overlay:
    """spec['overlay'] = {"text":"1453","sub":"...","at":1.0,"dur":3.5,"pos":"center|top|bottom"}
       spec['caption'] = small typed label bottom-left."""

    def __init__(self, spec, L):
        self.o, self.cap, self.L = spec.get("overlay"), spec.get("caption"), L
        if self.o:
            self.lay = fx.text_layer(self.o["text"], 130 * C.S, (255, 255, 255), stroke=int(6 * C.S))
            self.sub = fx.text_layer(self.o["sub"], 56 * C.S, (235, 235, 235), stroke=int(4 * C.S)) if self.o.get("sub") else None
        if self.cap:
            self.caplay = fx.text_layer(self.cap, 46 * C.S, (255, 255, 255), align="left", stroke=int(3 * C.S),
                                        max_w=int(C.W * 0.5))

    def apply(self, f, t):
        if self.o:
            at, dur = self.o.get("at", 1.0), self.o.get("dur", 3.5)
            a = fx.ease((t - at) / 0.5) * (1 - fx.ease((t - (at + dur - 0.5)) / 0.5))
            if a > 0:
                pos = self.o.get("pos", "center")
                cy = {"center": C.H * 0.5, "top": C.H * 0.2, "bottom": C.H * 0.64}[pos]
                sc = 1.0 + 0.18 * (1 - fx.ease((t - at) / 0.6))
                lay = cv2.resize(self.lay, None, fx=sc, fy=sc) if abs(sc - 1) > 0.01 else self.lay
                if pos == "center":  # darken behind for readability
                    f[:] = (f.astype(np.float32) * (1 - 0.35 * a)).astype(np.uint8)
                fx.paste_rgba(f, lay, C.W / 2, cy, alpha=a)
                bw = int(self.lay.shape[1] * 0.7 * fx.ease((t - at - 0.2) / 0.6))
                if bw > 0:
                    y = int(cy + self.lay.shape[0] / 2 - 10 * C.S)
                    cv2.rectangle(f, (C.W // 2 - bw // 2, y), (C.W // 2 + bw // 2, y + int(9 * C.S)), RED, -1)
                if self.sub is not None:
                    fx.paste_rgba(f, self.sub, C.W / 2, cy + self.lay.shape[0] / 2 + 50 * C.S,
                                  alpha=a * fx.ease((t - at - 0.5) / 0.5))
        if self.cap:
            a = fx.ease((t - 0.6) / 0.5)
            if a > 0:
                lay = self.caplay
                x1, y1 = int(60 * C.S), int(C.H - 340 * C.S)
                x2, y2 = x1 + lay.shape[1] - 20, y1 + lay.shape[0]
                ov = f.copy()
                cv2.rectangle(ov, (x1, y1), (x2, y2), (20, 20, 20), -1)
                cv2.rectangle(ov, (x1, y1), (x1 + 8, y2), RED, -1)
                f[:] = cv2.addWeighted(ov, 0.55 * a, f, 1 - 0.55 * a, 0)
                fx.paste_rgba(f, lay, x1 + lay.shape[1] / 2 + 10, (y1 + y2) / 2, alpha=a)
        return f


KINDS = {"photo": PhotoScene, "board": BoardScene, "newspaper": PaperScene, "document": PaperScene,
         "map": MapScene, "video": VideoScene, "title": TitleScene}
