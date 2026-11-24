"""Reference Video Style Matching.

Give a reference video (YouTube link or a file). The analyzer measures HOW it is edited:
  pacing (shot length), camera movement, transitions, colour grade, text overlays, sound design.
Only numbers are kept (style_profile.json). No frames, footage, music or voice of the reference are
ever reused - the recipe just tells our own engine how fast to cut, which moves/transitions to prefer,
how to grade and how loud/ducked the music should be. Result = original editing in a similar style."""
import glob, json, os, subprocess, sys
import numpy as np
import cv2
from scipy.ndimage import median_filter
from scipy.signal import find_peaks
from . import config as C

AW, AH, AFPS = 320, 180, 12          # analysis resolution / fps (small = fast)
ASR = 16000


# ------------------------------------------------------------------ download (yt-dlp, analysis only)
def download_reference(url, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "reference.*")):
        os.remove(f)
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--quiet", "--no-warnings",
           "-f", "b[height<=480]/bv*[height<=480]+ba/b", "--max-filesize", "250M",
           "--merge-output-format", "mp4", "-o", os.path.join(out_dir, "reference.%(ext)s"), url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except Exception as e:
        print(f"  [style] download error: {e}")
        return None
    files = [f for f in glob.glob(os.path.join(out_dir, "reference.*")) if not f.endswith(".part")]
    if r.returncode != 0 or not files:
        print("  [style] could not download the reference video (YouTube often blocks cloud servers).")
        print("          " + (r.stderr or "").strip().splitlines()[-1][:200] if r.stderr.strip() else "")
        print("          Fix: upload a short screen-recording of it as inputs/reference.mp4 and re-run.")
        return None
    return files[0]


# ------------------------------------------------------------------ video analysis
def _mad(x):
    return float(np.median(np.abs(x - np.median(x))) * 1.4826) + 1e-9


def analyze_video(path, max_seconds=360):
    cmd = ["ffmpeg", "-v", "error", "-t", str(max_seconds), "-i", path, "-an",
           "-vf", f"fps={AFPS},scale={AW}:{AH}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    fsz = AW * AH * 3
    hd, br, dxs, dys, divs, mags = [], [], [], [], [], []
    tx_c, tx_b = [], []
    sat, warm, con, vig, grain = [], [], [], [], []
    prev_hist = prev_small = None
    yy, xx = np.mgrid[0:AH // 2, 0:AW // 2].astype(np.float32)
    xc, yc = xx - AW / 4, yy - AH / 4
    den = float((xc ** 2 + yc ** 2).sum())
    n = 0
    while True:
        buf = p.stdout.read(fsz)
        if len(buf) < fsz:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(AH, AW, 3)
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([g], [0], None, [32], [0, 256]).ravel()
        hist /= hist.sum()
        hd.append(0.0 if prev_hist is None else 0.5 * float(np.abs(hist - prev_hist).sum()))
        prev_hist = hist
        br.append(float(g.mean()))
        small = cv2.resize(g, (AW // 2, AH // 2), interpolation=cv2.INTER_AREA)
        if prev_small is None:
            dxs.append(0.0); dys.append(0.0); divs.append(0.0); mags.append(0.0)
        else:
            fl = cv2.calcOpticalFlowFarneback(prev_small, small, None, 0.5, 2, 15, 2, 5, 1.1, 0)
            u, v = fl[..., 0], fl[..., 1]
            dx, dy = float(np.median(u)), float(np.median(v))
            dxs.append(dx); dys.append(dy)
            divs.append(float((xc * (u - dx) + yc * (v - dy)).sum() / den))
            mags.append(float(np.hypot(u, v).mean()))
        prev_small = small
        e = cv2.Canny(g, 100, 200)
        tx_c.append(float(e[60:120, 40:280].mean() / 255))
        tx_b.append(float(e[135:175, 40:280].mean() / 255))
        if n % 4 == 0:
            hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
            sat.append(float(hsv[..., 1].mean() / 255))
            warm.append(float((fr[..., 2].mean() - fr[..., 0].mean()) / 255))
            con.append(float(g.std() / 255))
            cen = g[54:126, 100:220].mean() + 1e-6
            cor = np.mean([g[:24, :40].mean(), g[:24, -40:].mean(), g[-24:, :40].mean(), g[-24:, -40:].mean()])
            vig.append(float(max(0.0, (cen - cor) / cen)))
            nz = np.abs(g.astype(np.float32) - cv2.GaussianBlur(g, (0, 0), 1.2))
            gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))
            m = gx < 12
            grain.append(float(nz[m].mean()) if m.sum() > 500 else 0.0)
        n += 1
    p.wait()
    if n < AFPS * 5:
        raise RuntimeError("reference video too short or unreadable")
    hd, br = np.array(hd), np.array(br)
    dxs, dys, divs, mags = map(np.array, (dxs, dys, divs, mags))
    dur = n / AFPS

    # ---- cuts / dissolves ----
    thr = max(0.22, float(np.median(hd)) + 6 * _mad(hd))
    pk, _ = find_peaks(hd, height=thr, distance=int(0.4 * AFPS))
    cuts = list(pk)
    win = np.convolve(hd, np.ones(6), "same")
    dthr = max(0.7, float(np.median(win)) + 6 * _mad(win))
    dpk, _ = find_peaks(win, height=dthr, distance=int(1.0 * AFPS))
    dissolves = [i for i in dpk if all(abs(i - c) > 6 for c in cuts) and hd[max(0, i - 3):i + 4].max() < thr]
    # ---- transition kinds ----
    kinds = {"cut": 0, "dip": 0, "flash": 0, "whip": 0, "glitch": 0, "fade": len(dissolves)}
    for k, i in enumerate(cuts):
        w = br[max(0, i - 3):i + 4]
        near = [c for c in cuts if c != i and abs(c - i) <= 4]
        if near:
            kinds["glitch"] += 1
        elif w.min() < 28:
            kinds["dip"] += 1
        elif w.max() > max(225.0, br.mean() + 55):
            kinds["flash"] += 1
        elif mags[max(0, i - 2):i + 2].mean() > 5.0:
            kinds["whip"] += 1
        else:
            kinds["cut"] += 1
    total_tr = max(1, sum(kinds.values()))
    # ---- shots + camera ----
    bounds = sorted(set([0] + cuts + dissolves + [n - 1]))
    lens = np.diff(bounds) / AFPS
    lens = lens[lens > 0.3]
    cam = {"zoom_in": 0, "zoom_out": 0, "pan_left": 0, "pan_right": 0, "pan_up": 0, "pan_down": 0, "static": 0}
    zooms, pans = [], []
    for a, b in zip(bounds[:-1], bounds[1:]):
        a, b = a + 3, b - 3
        if b - a < 6:
            continue
        dx, dy, dv = np.median(dxs[a:b]), np.median(dys[a:b]), np.median(divs[a:b])
        pan = max(abs(dx), abs(dy))
        if abs(dv) >= 0.0010 and abs(dv) * 120 >= pan:
            cam["zoom_in" if dv > 0 else "zoom_out"] += 1
            zooms.append(abs(dv))
        elif pan >= 0.20:
            if abs(dx) >= abs(dy):
                cam["pan_left" if dx > 0 else "pan_right"] += 1
            else:
                cam["pan_up" if dy > 0 else "pan_down"] += 1
            pans.append(pan)
        else:
            cam["static"] += 1
    # ---- text overlays (edge-density bursts that are not scene changes) ----
    ev = {"center": 0, "bottom": 0}
    durs = []
    for name, series in (("center", np.array(tx_c)), ("bottom", np.array(tx_b))):
        base = median_filter(series, size=min(len(series) // 2 * 2 + 1, 73), mode="nearest")
        on = (series > base * 1.6 + 0.012).astype(int)
        i = 0
        while i < len(on):
            if on[i]:
                j = i
                while j < len(on) and on[j]:
                    j += 1
                if 8 <= j - i <= 60 and all(abs(i - c) > 4 for c in cuts):
                    ev[name] += 1
                    durs.append((j - i) / AFPS)
                i = j
            else:
                i += 1
    profile = dict(
        duration_analyzed=round(dur, 1),
        pacing=dict(cuts_per_min=round(60 * (len(cuts) + len(dissolves)) / dur, 1),
                    avg_shot_len=round(float(lens.mean()), 2) if len(lens) else dur,
                    median_shot_len=round(float(np.median(lens)), 2) if len(lens) else dur),
        transitions={k: round(v / total_tr, 3) for k, v in kinds.items()},
        camera={k: round(v / max(1, sum(cam.values())), 3) for k, v in cam.items()},
        camera_speed=dict(zoom_div=round(float(np.median(zooms)), 5) if zooms else 0.0015,
                          pan_px=round(float(np.median(pans)), 3) if pans else 0.8),
        color=dict(saturation=round(float(np.mean(sat)), 3), brightness=round(float(br.mean() / 255), 3),
                   contrast=round(float(np.mean(con)), 3), warmth=round(float(np.mean(warm)), 3),
                   vignette=round(float(np.mean(vig)), 3), grain_noise=round(float(np.mean(grain)), 3)),
        text=dict(overlays_per_min=round(60 * (ev["center"] + ev["bottom"]) / dur, 1),
                  avg_duration=round(float(np.mean(durs)), 1) if durs else 3.0,
                  position="center" if ev["center"] >= ev["bottom"] else "bottom"),
    )
    profile["_cut_times"] = [round(c / AFPS, 2) for c in cuts]
    return profile


# ------------------------------------------------------------------ audio analysis
def analyze_audio(path, cut_times, max_seconds=360):
    r = subprocess.run(["ffmpeg", "-v", "error", "-t", str(max_seconds), "-i", path, "-vn", "-ac", "1",
                        "-ar", str(ASR), "-f", "f32le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, np.float32)
    if len(x) < ASR * 5:
        return None
    fl = int(0.05 * ASR)
    n = len(x) // fl
    fr = x[:n * fl].reshape(n, fl) * np.hanning(fl)
    sp = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    f = np.fft.rfftfreq(fl, 1 / ASR)
    tot = sp.sum(1) + 1e-12
    low = sp[:, (f >= 20) & (f < 120)].sum(1)
    voice = sp[:, (f >= 300) & (f < 3400)].sum(1)
    high = sp[:, (f >= 4000) & (f < 8000)].sum(1)
    db = 10 * np.log10(tot / fl + 1e-12)
    active = db > np.percentile(db, 5) + 6
    if active.sum() < 20:
        return None
    a = db[active]
    top, bottom = np.median(a[a >= np.median(a)]), np.median(a[a <= np.percentile(a, 35)])
    dr = float(np.percentile(a, 95) - np.percentile(a, 10))
    low_db = 10 * np.log10(low + 1e-12)
    pk, _ = find_peaks(low_db, height=np.median(low_db) + 10, distance=int(1.5 * 20))
    hdb = 10 * np.log10(high + 1e-12)
    hbase = np.median(hdb)
    hit = 0
    for t in cut_times:
        i0, i1 = int(max(0, t - 0.15) * 20), int((t + 0.35) * 20)
        if i1 > i0 and i1 <= n and hdb[i0:i1].max() > hbase + 8:
            hit += 1
    return dict(speech_fraction=round(float((voice / tot > 0.5)[active].mean()), 2),
                bed_vs_voice_db=round(float(bottom - top), 1),
                dynamic_range_db=round(dr, 1),
                bass_share=round(float(low.sum() / tot.sum()), 3),
                impacts_per_min=round(60 * len(pk) / (n * 0.05), 1),
                sfx_on_cut_fraction=round(hit / max(1, len(cut_times)), 2))


# ------------------------------------------------------------------ profile -> recipe for our engine
DEFAULT_RECIPE = dict(
    name="default", target_asl=None,
    motion_weights=dict(zoom_in=0.4, zoom_out=0.2, pan_left=0.15, pan_right=0.15, pan_up=0.05, pan_down=0.05, static=0.0),
    zoom_amt=0.30, pan_scale=1.0,
    transition_weights=dict(fade=0.7, dip=0.1, flash=0.05, glitch=0.05, whip=0.05, cut=0.05),
    grade_params=dict(sat=0.62, contrast=1.0, warmth=0.03, vignette=0.42, grain=0.045),
    text=dict(overlays_per_min=0.0, avg_duration=3.0, position="center"),
    sound=dict(music_volume=0.30, duck=0.78, sfx_on_cut=0.0, impact_on_text=False, bass=0.5),
    effects=dict(glitch_prob=0.0),
)


def _clip(v, lo, hi):
    return float(min(hi, max(lo, v)))


def _norm(d, floor=0.0):
    d = {k: max(floor, v) for k, v in d.items()}
    s = sum(d.values()) or 1.0
    return {k: round(v / s, 3) for k, v in d.items()}


def recipe_from_profile(pr, audio):
    r = json.loads(json.dumps(DEFAULT_RECIPE))
    r["name"] = "reference"
    asl = pr["pacing"]["avg_shot_len"]
    r["target_asl"] = round(_clip(asl, 1.8, 10.0), 2)
    cam = dict(pr["camera"])
    cam["static"] = cam.get("static", 0) * 0.5   # a still frame is dull in a still-photo documentary
    r["motion_weights"] = _norm(cam, 0.02)
    sp = pr["camera_speed"]
    r["zoom_amt"] = round(_clip(np.expm1(sp["zoom_div"] * AFPS * min(r["target_asl"], 8.0)), 0.08, 0.5), 3)
    r["pan_scale"] = round(_clip(sp["pan_px"] / 1.0, 0.5, 2.0), 2)
    tw = dict(pr["transitions"])
    r["transition_weights"] = _norm(tw, 0.03)
    c = pr["color"]
    r["grade_params"] = dict(
        sat=round(_clip(c["saturation"] / 0.30, 0.15, 1.2), 2),
        contrast=round(_clip(c["contrast"] / 0.22, 0.85, 1.35), 2),
        warmth=round(_clip(c["warmth"] * 1.2, -0.08, 0.10), 3),
        vignette=round(_clip(c["vignette"] / 0.94, 0.12, 0.7), 2),
        grain=round(_clip(c["grain_noise"] * 0.012, 0.02, 0.085), 3))
    t = pr["text"]
    r["text"] = dict(overlays_per_min=round(_clip(t["overlays_per_min"], 0, 8), 1),
                     avg_duration=_clip(t["avg_duration"], 1.5, 5.0), position=t["position"])
    if audio:
        r["sound"] = dict(
            music_volume=round(_clip(0.30 * 10 ** ((audio["bed_vs_voice_db"] + 18) / 20), 0.12, 0.5), 2),
            duck=round(_clip(0.5 + (audio["dynamic_range_db"] - 12) * 0.02, 0.5, 0.9), 2),
            sfx_on_cut=round(_clip(audio["sfx_on_cut_fraction"], 0, 0.8), 2),
            impact_on_text=audio["impacts_per_min"] >= 2.0,
            bass=round(_clip(audio["bass_share"] * 2, 0.1, 1.0), 2))
    g = pr["transitions"].get("glitch", 0)
    r["effects"] = dict(glitch_prob=round(_clip(g * 0.01, 0.0, 0.006), 4))
    return r


def analyze_reference(path, out_json=None):
    print(f"  [style] analysing '{os.path.basename(path)}' (editing style only, nothing is copied)")
    pr = analyze_video(path)
    au = analyze_audio(path, pr.pop("_cut_times"))
    if au:
        pr["sound"] = au
    recipe = recipe_from_profile(pr, au)
    if out_json:
        os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
        json.dump(dict(profile=pr, recipe=recipe), open(out_json, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    return pr, recipe


def summary(pr, recipe):
    p, c = pr["pacing"], recipe
    top = lambda d, k=3: ", ".join(f"{a} {int(b * 100)}%" for a, b in sorted(d.items(), key=lambda x: -x[1])[:k])
    return "\n".join([
        f"   pacing     : {p['cuts_per_min']} cuts/min, average shot {p['avg_shot_len']}s -> our shots ~{c['target_asl']}s",
        f"   camera     : {top(pr['camera'])} (zoom +{int(c['zoom_amt'] * 100)}%)",
        f"   transitions: {top(pr['transitions'])}",
        f"   grade      : sat {c['grade_params']['sat']}, contrast {c['grade_params']['contrast']}, "
        f"warmth {c['grade_params']['warmth']}, vignette {c['grade_params']['vignette']}, grain {c['grade_params']['grain']}",
        f"   text       : ~{c['text']['overlays_per_min']} overlays/min, position {c['text']['position']}",
        f"   sound      : music vol {c['sound']['music_volume']}, ducking {c['sound']['duck']}, "
        f"sfx-on-cut {c['sound']['sfx_on_cut']}, impacts on text {c['sound']['impact_on_text']}"])


# ------------------------------------------------------------------ presets / loading
def load_preset(name):
    path = name if name.endswith(".json") else os.path.join("styles", name + ".json")
    d = json.load(open(path, encoding="utf-8"))
    r = json.loads(json.dumps(DEFAULT_RECIPE))
    for k, v in d.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k].update(v)
        else:
            r[k] = v
    return r
