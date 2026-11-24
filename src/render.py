"""Timeline -> frames -> FFmpeg (video + mixed audio + burned subtitles) -> 1080p MP4."""
import math, os, subprocess, time
import numpy as np
from . import config as C, fx


def render(scenes, overlays, specs, bounds, total, grade_style, audio_wav, ass_path, out_mp4, recipe=None):
    n = len(scenes)
    gp = recipe["grade_params"] if recipe and recipe.get("use_grade_params") else None
    grain = gp["grain"] if gp else 0.045
    glitch_p = (recipe or {}).get("effects", {}).get("glitch_prob", 0.0)
    rnd = np.random.default_rng(99)
    T = C.TRANSITION
    starts = [bounds[i][0] - (T / 2 if i > 0 else 0) for i in range(n)]
    ends = [bounds[i][1] + (T / 2 if i < n - 1 else 0) for i in range(n)]
    kinds = [s.get("transition", "fade") for s in specs]
    os.makedirs(os.path.dirname(out_mp4) or ".", exist_ok=True)
    vf = f"ass={ass_path}" if ass_path else "null"
    cmd = ["ffmpeg", "-y", "-loglevel", "warning", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{C.W}x{C.H}", "-r", str(C.FPS), "-i", "-", "-i", audio_wav,
           "-vf", vf + ",format=yuv420p", "-c:v", "libx264", "-preset", C.PRESET, "-crf", str(C.CRF),
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out_mp4]
    log = open(os.path.join(C.WORK_DIR, "ffmpeg.log"), "w")
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)

    def get(i, g):
        t = max(0.0, g - starts[i])
        return overlays[i].apply(scenes[i].frame(t), t)

    nframes = int(math.ceil(total * C.FPS))
    cur, t0 = 0, time.time()
    for k in range(nframes):
        g = k / C.FPS
        while cur < n - 1 and g >= ends[cur] - T / 2 + 1e-9 and g >= bounds[cur][1]:
            cur += 1
        in_tr = cur < n - 1 and g >= bounds[cur][1] - T / 2
        if in_tr:
            p = (g - (bounds[cur][1] - T / 2)) / T
            a, b = get(cur, g), get(cur + 1, g)
            f = fx.transition(a, b, min(1.0, max(0.0, p)), kinds[cur])
        else:
            f = get(cur, g)
        f = fx.grade(f, grade_style, grain=grain, t=g, params=gp)
        if glitch_p and rnd.random() < glitch_p:
            f = fx.glitch_shift(f, 0.5)
        if g < 0.8:
            f = (f.astype(np.float32) * fx.ease(g / 0.8)).astype(np.uint8)
        tail0 = total - 1.2
        if g > tail0:
            f = (f.astype(np.float32) * (1 - fx.ease((g - tail0) / 1.2))).astype(np.uint8)
        try:
            ff.stdin.write(f.tobytes())
        except BrokenPipeError:
            break
        if k % (C.FPS * 5) == 0:
            el = time.time() - t0
            print(f"  [render] {k}/{nframes} frames ({100 * k / nframes:.0f}%) elapsed {el:.0f}s", flush=True)
    ff.stdin.close()
    ff.wait()
    log.close()
    if ff.returncode != 0:
        raise RuntimeError("ffmpeg failed, see work/ffmpeg.log:\n" + open(os.path.join(C.WORK_DIR, "ffmpeg.log")).read()[-1500:])
    print(f"  [render] done in {time.time() - t0:.0f}s -> {out_mp4}")
