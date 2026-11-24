"""Audio: voice clean-up, Whisper alignment, free TTS fallback, synthesized SFX + music, ducked mix.
Everything is generated in code, so there are no licensing problems with sounds."""
import asyncio, os, subprocess, wave
import numpy as np
from scipy.signal import butter, sosfilt, fftconvolve
from scipy.ndimage import uniform_filter1d
from . import config as C

SR = 44100
_rng = np.random.default_rng(5)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-800:])
    return r


def decode(path, ac=1):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", str(ac), "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[-500:])
    return np.frombuffer(r.stdout, np.float32).copy()


def write_wav(path, x):
    """x: float array (n,) mono or (n,2) stereo -> 16-bit wav."""
    x = np.atleast_2d(x.T).T if x.ndim == 1 else x
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return float(r.stdout.strip())


# ------------------------------------------------------------------ voice
def clean_voice(src, dst):
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af",
         "highpass=f=80,lowpass=f=12000,acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=3,"
         "loudnorm=I=-16:TP=-1.5:LRA=9", "-ar", str(SR), "-ac", "1", dst])
    return dst


def transcribe_words(wav, model_size="small", lang="hi"):
    """Word timestamps with faster-whisper (free, runs on CPU). Returns [(start,end,word)] or None."""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segs, _ = model.transcribe(wav, language=lang, word_timestamps=True, vad_filter=True)
        words = [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or [])]
        print(f"  [whisper] {len(words)} words found")
        return words or None
    except Exception as e:
        print(f"  [whisper] unavailable -> proportional timing ({e})")
        return None


def align(scene_texts, words, total):
    """Returns (bounds[(s,e)], time_at(k)) mapping script words to voice time."""
    counts = [max(1, len(t.split())) for t in scene_texts]
    m = sum(counts)
    starts_idx = np.concatenate([[0], np.cumsum(counts)[:-1]])
    if not words:
        def time_at(k): return total * k / m
        bounds_t = [time_at(k) for k in starts_idx] + [total]
    else:
        n = len(words)
        lead = max(0.0, words[0][0] - 0.1)
        span_end = min(total, words[-1][1] + 0.15)

        def time_at(k):
            pos = min(k * n / m, n - 1e-6)
            i = int(pos); fr = pos - i
            a = words[i][0]
            b = words[i + 1][0] if i + 1 < n else words[-1][1]
            return a + (b - a) * fr
        bounds_t = [0.0] + [time_at(k) for k in starts_idx[1:]] + [total]
        gaps = [((words[i][1] + words[i + 1][0]) / 2, words[i + 1][0] - words[i][1])
                for i in range(n - 1) if words[i + 1][0] - words[i][1] >= 0.18]
        for j in range(1, len(bounds_t) - 1):  # snap to nearest pause (within 1.5 s)
            near = [g for g in gaps if abs(g[0] - bounds_t[j]) < 1.5]
            if near:
                bounds_t[j] = min(near, key=lambda g: abs(g[0] - bounds_t[j]))[0]
        for j in range(1, len(bounds_t)):
            bounds_t[j] = max(bounds_t[j], bounds_t[j - 1] + 1.0)
        bounds_t[-1] = max(total, bounds_t[-2] + 1.0)
    bounds = [(bounds_t[i], bounds_t[i + 1]) for i in range(len(scene_texts))]
    return bounds, time_at, starts_idx


def tts_scenes(texts, voice, rate, out_wav):
    """Free fallback voice (Microsoft Edge TTS) when no voice-over file is given. Exact scene timing."""
    import edge_tts
    pieces, bounds, t = [], [], 0.0
    gap = np.zeros(int(0.25 * SR), np.float32)
    for i, tx in enumerate(texts):
        mp3 = os.path.join(C.WORK_DIR, f"tts_{i}.mp3")
        asyncio.run(edge_tts.Communicate(tx, voice, rate=rate).save(mp3))
        x = decode(mp3)
        pieces += [x, gap]
        d = (len(x) + len(gap)) / SR
        bounds.append((t, t + d)); t += d
    write_wav(out_wav, np.concatenate(pieces))
    return bounds, t


# ------------------------------------------------------------------ filters
def _f(x, kind, lo, hi=None, order=3):
    if kind == "band":
        sos = butter(order, [lo, hi], btype="band", fs=SR, output="sos")
    else:
        sos = butter(order, lo, btype=kind, fs=SR, output="sos")
    return sosfilt(sos, x).astype(np.float32)


def reverb(x, tail=1.2, mix=0.25):
    n = int(tail * SR)
    ir = (_rng.normal(0, 1, n) * np.exp(-np.arange(n) / SR * 6.0 / tail)).astype(np.float32)
    ir = _f(ir, "low", 5000)
    wet = fftconvolve(x, ir)[:len(x)].astype(np.float32)
    wet *= np.max(np.abs(x)) / (np.max(np.abs(wet)) + 1e-9)
    return x * (1 - mix) + wet * mix


def _norm(x, peak=0.9):
    return (x / (np.max(np.abs(x)) + 1e-9) * peak).astype(np.float32)


def _env(n, tau):
    return np.exp(-np.arange(n) / SR / tau).astype(np.float32)


def _noise(n):
    return _rng.normal(0, 1, n).astype(np.float32)


# ------------------------------------------------------------------ SFX (synthesized)
def sfx_gunshot(dur=None, pitch=1.0, tail=0.9):
    n = int(tail * SR)
    crack = _f(_noise(n) * _env(n, 0.010), "high", 900)
    body = _f(_noise(n) * _env(n, 0.07), "low", 1800)
    freq = (55 + 110 * np.exp(-np.arange(n) / SR / 0.02)) * pitch
    thump = np.sin(2 * np.pi * np.cumsum(freq) / SR) * _env(n, 0.12)
    return _norm(reverb(_norm(crack * 0.9 + body * 0.7 + thump * 0.9), 0.9, 0.3))


def sfx_gunshots(dur=None):
    out = np.zeros(int(1.8 * SR), np.float32)
    for i, (t0, v, p) in enumerate([(0, 1.0, 1.0), (0.27, 0.8, 0.9), (0.55, 0.9, 1.1)]):
        s = sfx_gunshot(pitch=p) * v
        a = int(t0 * SR)
        out[a:a + len(s)] += s[:len(out) - a]
    return _norm(out)


def sfx_explosion(dur=None):
    n = int(3.5 * SR)
    t = np.arange(n) / SR
    boom = _f(_noise(n), "low", 400) * _env(n, 0.9)
    sweep = _f(_noise(n) * _env(n, 0.25), "low", 3000)
    sub = np.sin(2 * np.pi * np.cumsum(50 * np.exp(-t / 1.2) + 28) / SR) * _env(n, 1.1)
    debris = _f(_noise(n) * (_rng.random(n) > 0.9992) * 6, "band", 1500, 6000) * _env(n, 1.2)
    x = boom * 1.0 + sweep * 0.8 + sub * 1.2 + debris * 0.3
    return _norm(reverb(x, 2.0, 0.3), 0.95)


def sfx_whoosh(dur=None):
    d = dur or 0.9
    n = int(d * SR)
    t = np.linspace(0, 1, n)
    x = _f(_noise(n), "band", 400, 5000, 2) * (np.sin(np.pi * t) ** 2).astype(np.float32)
    return _norm(x, 0.8)


def sfx_typewriter(dur=None):
    d = dur or 2.5
    out = np.zeros(int((d + 0.6) * SR), np.float32)
    t = 0.05
    while t < d:
        n = int(0.05 * SR)
        click = _f(_noise(n) * _env(n, 0.004), "band", 1800, 7000) + np.sin(2 * np.pi * 180 * np.arange(n) / SR) * _env(n, 0.01) * 0.6
        a = int(t * SR)
        out[a:a + n] += click[:len(out) - a] * float(_rng.uniform(0.6, 1.0))
        t += float(_rng.uniform(0.05, 0.11))
    ding = np.sin(2 * np.pi * 2300 * np.arange(int(0.6 * SR)) / SR) * _env(int(0.6 * SR), 0.18) * 0.5
    a = int(d * SR)
    out[a:a + len(ding)] += ding[:len(out) - a]
    return _norm(out, 0.7)


def sfx_paper(dur=None):
    n = int(0.9 * SR)
    x = _f(_noise(n), "high", 1500) * _env(n, 0.25) * (0.4 + 0.6 * (_rng.random(n) > 0.5))
    return _norm(uniform_filter1d(x, 3), 0.6)


def sfx_impact(dur=None):
    n = int(2.2 * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * np.cumsum(70 * np.exp(-t / 0.4) + 32) / SR) * _env(n, 0.8)
    x += _f(_noise(n) * _env(n, 0.05), "low", 900) * 0.8
    return _norm(reverb(x, 1.6, 0.3), 0.95)


def sfx_stamp(dur=None):
    n = int(0.8 * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * np.cumsum(90 * np.exp(-t / 0.05) + 55) / SR) * _env(n, 0.12)
    x += _f(_noise(n) * _env(n, 0.012), "band", 1000, 5000) * 0.8
    return _norm(x, 0.9)


def sfx_heartbeat(dur=None):
    out = np.zeros(int(1.6 * SR), np.float32)
    for t0, v in [(0.0, 1.0), (0.28, 0.7), (1.0, 1.0), (1.28, 0.7)]:
        n = int(0.25 * SR)
        tt = np.arange(n) / SR
        b = np.sin(2 * np.pi * np.cumsum(70 * np.exp(-tt / 0.05) + 40) / SR) * _env(n, 0.07) * v
        a = int(t0 * SR)
        out[a:a + n] += b
    return _norm(out, 0.9)


def sfx_war_drum(dur=None):
    out = np.zeros(int(4 * SR), np.float32)
    for k in range(6):
        n = int(0.9 * SR)
        tt = np.arange(n) / SR
        d = np.sin(2 * np.pi * np.cumsum(95 * np.exp(-tt / 0.08) + 52) / SR) * _env(n, 0.25)
        d += _f(_noise(n) * _env(n, 0.02), "band", 200, 2000) * 0.5
        a = int(k * 0.62 * SR)
        out[a:a + n] += d[:len(out) - a] * (0.7 + 0.3 * (k % 2 == 0))
    return _norm(reverb(out, 1.5, 0.25), 0.95)


def sfx_glitch(dur=None):
    n = int(0.5 * SR)
    x = np.zeros(n, np.float32)
    pos = 0
    while pos < n:
        L = int(_rng.integers(300, 3000))
        seg = np.sign(_noise(L)) * float(_rng.uniform(0.2, 1.0)) if _rng.random() > 0.4 else np.zeros(L)
        x[pos:pos + L] = seg[:n - pos]
        pos += L
    return _norm(_f(x, "band", 300, 9000, 2), 0.6)


def sfx_clock(dur=None):
    out = np.zeros(int(3 * SR), np.float32)
    for k in range(6):
        n = int(0.04 * SR)
        c = _f(_noise(n) * _env(n, 0.006), "band", 1500, 5000)
        a = int(k * 0.5 * SR)
        out[a:a + n] += c * (1.0 if k % 2 == 0 else 0.7)
    return _norm(out, 0.6)


SFX = dict(gunshot=sfx_gunshot, gunshots=sfx_gunshots, explosion=sfx_explosion, whoosh=sfx_whoosh,
           typewriter=sfx_typewriter, paper=sfx_paper, impact=sfx_impact, stamp=sfx_stamp,
           heartbeat=sfx_heartbeat, war_drum=sfx_war_drum, glitch=sfx_glitch, clock=sfx_clock)


# ------------------------------------------------------------------ music (synthesized cinematic bed)
MOODS = {  # root note in Hz, chord semitone roots, bpm
    "dark": dict(root=55.0, prog=[0, -4, -9, -2], bpm=54, minor=True),
    "epic": dict(root=73.4, prog=[0, -4, 3, -2], bpm=70, minor=True),
    "sad": dict(root=55.0, prog=[0, -4, 3, -5], bpm=46, minor=True),
}


def make_music(total, mood="dark"):
    m = MOODS.get(mood, MOODS["dark"])
    n = int(total * SR)
    out = np.zeros(n, np.float32)
    seg = 8.0
    nseg = int(np.ceil(total / seg)) + 1
    for k in range(nseg):
        root = m["root"] * 2 ** (m["prog"][k % 4] / 12)
        a = int((k * seg - 1.5) * SR)
        L = int((seg + 3.0) * SR)
        a0, b0 = max(0, a), min(n, a + L)
        if b0 <= a0:
            continue
        tt = (np.arange(a0, b0) / SR).astype(np.float32)
        env = np.sin(np.pi * np.clip((np.arange(a0, b0) - a) / L, 0, 1)) ** 2
        pad = np.zeros(len(tt), np.float32)
        for semi in (0, 3, 7, 12, 15):
            f0 = root * 2 ** (semi / 12) * 2
            for det in (-0.0025, 0.0, 0.0025):
                ph = (tt * f0 * (1 + det)) % 1.0
                pad += (2 * ph - 1).astype(np.float32) * 0.04
        pad = _f(pad, "low", 700 + 250 * np.sin(k))
        sub = np.sin(2 * np.pi * root * tt) * 0.5
        out[a0:b0] += (pad + sub) * env
    # slow shimmer
    tt = np.arange(n, dtype=np.float32) / SR
    out += (np.sin(2 * np.pi * m["root"] * 8 * tt) * 0.02 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.07 * tt))).astype(np.float32)
    # heartbeat-like pulse that builds up
    beat = 60.0 / m["bpm"]
    k, t0 = 0, 2.0
    while t0 < total - 1:
        nn = int(0.5 * SR)
        a = int(t0 * SR)
        tb = np.arange(nn) / SR
        kick = np.sin(2 * np.pi * np.cumsum(60 * np.exp(-tb / 0.06) + 38) / SR) * _env(nn, 0.12)
        out[a:a + nn] += kick[:n - a] * (0.15 + 0.55 * (t0 / total)) * (1.0 if k % 2 == 0 else 0.6)
        t0 += beat; k += 1
    out = reverb(_f(out, "low", 7000), 2.5, 0.35)
    fade = int(3 * SR)
    out[:fade] *= np.linspace(0, 1, fade) ** 2
    out[-fade:] *= np.linspace(1, 0, fade) ** 2
    return _norm(out, 0.9)


def load_music(path, total):
    x = decode(path)
    reps = int(np.ceil(total * SR / max(1, len(x))))
    x = np.tile(x, reps)[:int(total * SR)]
    fade = int(3 * SR)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    return _norm(x, 0.9)


# ------------------------------------------------------------------ mix
def mix(voice, events, music, total, out_wav, music_vol=0.30, sfx_vol=0.55, duck=0.78):
    """events: [(time, name, vol, dur)] ; music ducks under the voice ; final loudnorm to -14 LUFS."""
    n = int(total * SR)
    v = np.zeros(n, np.float32)
    v[:min(n, len(voice))] = voice[:n]
    env = uniform_filter1d(np.abs(v), int(0.12 * SR))
    env = np.clip(env / (np.percentile(env, 95) * 0.6 + 1e-9), 0, 1)
    env = uniform_filter1d(env, int(0.5 * SR))
    gain = 1 - duck * np.clip(env * 1.6, 0, 1)
    mus = np.zeros(n, np.float32)
    mus[:min(n, len(music))] = music[:n]
    sfx = np.zeros(n, np.float32)
    for (t, name, vol, dur) in events:
        gen = SFX.get(name)
        if gen is None:
            print(f"  [sfx] unknown sound '{name}' (available: {', '.join(SFX)})")
            continue
        s = gen(dur) * vol
        a = int(max(0, t) * SR)
        if a < n:
            sfx[a:a + len(s)] += s[:n - a]
    m = v * 0.95 + mus * gain * music_vol + sfx * sfx_vol
    m = np.tanh(m * 1.1) / np.tanh(1.1)
    raw = os.path.join(C.WORK_DIR, "mix_raw.wav")
    write_wav(raw, m)
    run(["ffmpeg", "-y", "-v", "error", "-i", raw, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", str(SR), out_wav])
    return out_wav
