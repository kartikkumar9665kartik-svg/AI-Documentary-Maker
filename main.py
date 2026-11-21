"""AI-Documentary-Maker: script.json + voice-over (+ optional reference video style) -> documentary MP4.
Run:  python -m src.main --script inputs/script.json --voice inputs/voiceover.mp3 [--reference URL] [--style PRESET]"""
import argparse, glob, json, os, re, sys
import numpy as np
from . import config as C, audio, subs, scenes as S, fetch_media as fm, render as R
from . import style_analyzer as SA

AUTO_SFX = {"whip": ("whoosh", 0.6), "glitch": ("glitch", 0.6), "flash": ("impact", 0.7)}
DEV = str.maketrans("०१२३४५६७८९", "0123456789")


# ------------------------------------------------------------------ style (reference video / preset)
def resolve_style(a, cfg):
    ref = a.reference or cfg.get("reference_url") or ""
    path = None
    if ref:
        if ref.startswith("http"):
            print(f"[0/6] Reference style: downloading for analysis only -> {ref}")
            path = SA.download_reference(ref, C.WORK_DIR)
        elif os.path.exists(ref):
            path = ref
    if path is None and not a.style and not cfg.get("style") and os.path.exists("inputs/reference.mp4"):
        path = "inputs/reference.mp4"
    if path is None and os.path.exists("inputs/reference.mp4") and ref:
        path = "inputs/reference.mp4"       # URL failed -> use the uploaded copy
    if path:
        try:
            pr, recipe = SA.analyze_reference(path, os.path.join("output", "style_profile.json"))
            print("[0/6] Reference style learned:\n" + SA.summary(pr, recipe))
            for f in glob.glob(os.path.join(C.WORK_DIR, "reference.*")):
                os.remove(f)                # do not keep the downloaded video
            return recipe
        except Exception as e:
            print(f"[0/6] Style analysis failed ({e}) -> using normal style")
    name = a.style or cfg.get("style")
    if name:
        print(f"[0/6] Style preset: {name}")
        return SA.load_preset(name)
    return json.loads(json.dumps(SA.DEFAULT_RECIPE))


def pick_transitions(specs, recipe):
    """Choose transitions between scenes by the learned weights (explicit 'transition' in script wins)."""
    if recipe["name"] == "default":
        return
    rng = np.random.default_rng(21)
    tw = recipe["transition_weights"]
    keys = list(tw)
    p = np.array([tw[k] for k in keys], float)
    last = "fade"
    for sp in specs[:-1]:
        if "transition" in sp:
            last = sp["transition"]
            continue
        pp = p.copy()
        if last in ("glitch", "flash", "whip"):          # no two loud transitions in a row
            for i, k in enumerate(keys):
                if k in ("glitch", "flash", "whip"):
                    pp[i] = 0
        last = str(rng.choice(keys, p=pp / pp.sum()))
        sp["transition"] = last


def auto_overlays(specs, bounds, recipe, total):
    """Big year/number text on screen at the rate seen in the reference (only where narration has a year)."""
    want = int(round(recipe["text"]["overlays_per_min"] * total / 60.0))
    have = sum(1 for s in specs if s.get("overlay"))
    for i, sp in enumerate(specs):
        if have >= want:
            break
        if sp.get("overlay") or sp.get("kind") in ("title",):
            continue
        m = re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", sp.get("narration", "").translate(DEV))
        if m:
            sp["overlay"] = {"text": m[0], "at": 1.0, "dur": min(recipe["text"]["avg_duration"], bounds[i][1] - bounds[i][0] - 1.0),
                             "pos": recipe["text"]["position"]}
            have += 1
    for sp in specs:
        if sp.get("overlay") and "pos" not in sp["overlay"] and recipe["name"] != "default":
            sp["overlay"]["pos"] = recipe["text"]["position"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="inputs/script.json")
    ap.add_argument("--voice", default="inputs/voiceover.mp3")
    ap.add_argument("--out", default="output/documentary.mp4")
    ap.add_argument("--quality", choices=["draft", "final"], default="final")
    ap.add_argument("--whisper-model", default="small")
    ap.add_argument("--no-whisper", action="store_true")
    ap.add_argument("--reference", default="", help="YouTube link (or local video) whose editing STYLE to match")
    ap.add_argument("--style", default="", help="style preset name from styles/ (dark_history, fast_explainer, slow_cinematic)")
    a = ap.parse_args()

    cfg = json.load(open(a.script, encoding="utf-8"))
    specs = cfg["scenes"]
    texts = [s.get("narration", "") for s in specs]
    C.set_quality(a.quality)
    os.makedirs(C.WORK_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    lang = cfg.get("language", "hi")
    print(f"== AI-Documentary-Maker | {len(specs)} scenes | {C.W}x{C.H}@{C.FPS} ==")

    recipe = resolve_style(a, cfg)
    recipe["use_grade_params"] = recipe["name"] != "default" and "grade" not in cfg

    # 1) voice-over + timing -------------------------------------------------
    voice_wav = os.path.join(C.WORK_DIR, "voice.wav")
    if os.path.exists(a.voice):
        print("[1/6] Voice-over clean-up + timing")
        audio.clean_voice(a.voice, voice_wav)
        vdur = audio.duration(voice_wav)
        words = None if a.no_whisper else audio.transcribe_words(voice_wav, a.whisper_model, lang)
        bounds, time_at, starts_idx = audio.align(texts, words, vdur)
    else:
        print("[1/6] No voice-over file -> free Edge-TTS voice")
        bounds, vdur = audio.tts_scenes(texts, cfg.get("tts_voice", "hi-IN-MadhurNeural"),
                                        cfg.get("tts_rate", "+0%"), voice_wav)
        starts, k = [], 0
        for t in texts:
            starts.append(k); k += max(1, len(t.split()))
        starts_idx = np.array(starts)

        def time_at(kk):
            for i in range(len(texts) - 1, -1, -1):
                if kk >= starts[i]:
                    cnt = max(1, len(texts[i].split()))
                    s_t, e_t = bounds[i]
                    return s_t + (e_t - s_t - 0.25) * (kk - starts[i]) / cnt
            return 0.0
    total = vdur + C.TAIL
    bounds[-1] = (bounds[-1][0], total)
    for i, (s, e) in enumerate(bounds):
        print(f"   scene {i + 1:02d} [{specs[i].get('kind', 'photo'):9s}] {s:6.1f}s -> {e:6.1f}s")

    pick_transitions(specs, recipe)
    auto_overlays(specs, bounds, recipe, total)

    # 2) subtitles -----------------------------------------------------------
    ass = None
    if cfg.get("subtitles", True):
        print("[2/6] Subtitles")
        chunks = subs.build_chunks(texts, bounds, time_at, starts_idx)
        ass = subs.write_ass(chunks, os.path.join(C.WORK_DIR, "subs.ass"))
    else:
        print("[2/6] Subtitles off")

    # 3) scenes (search + download free media) --------------------------------
    print("[3/6] Building scenes + finding free-licence media")
    ctx = {"used": set()}
    T = C.TRANSITION
    scene_objs, overlays, cut_events = [], [], []
    n = len(specs)
    for i, sp in enumerate(specs):
        s_t = bounds[i][0] - (T / 2 if i > 0 else 0)
        e_t = bounds[i][1] + (T / 2 if i < n - 1 else 0)
        L = e_t - s_t
        kind = sp.get("kind", "photo")
        print(f"  scene {i + 1}/{n}: {kind}")
        cls = S.KINDS.get(kind)
        if cls is None:
            raise SystemExit(f"Unknown scene kind '{kind}'. Use one of: {', '.join(S.KINDS)}")
        if kind in ("newspaper", "document"):
            sp = dict(sp, kind=kind)
        obj = None
        if kind == "photo" and recipe["name"] != "default":
            nshots = int(max(1, min(8, round(L / recipe["target_asl"])))) if recipe.get("target_asl") else 1
            if nshots >= 2 and not sp.get("no_split"):
                obj = S.ShotSeq(sp, L, recipe, seed=i, n=nshots)
                print(f"     style: {nshots} quick shots")
                for rel in obj.cut_times():
                    cut_events.append((s_t + rel, i))
            else:
                rng = np.random.default_rng(i)
                mw = recipe["motion_weights"]
                ks = [k for k in mw if k != "static"]
                pr = np.array([mw[k] for k in ks], float)
                sp = dict(sp, zoom_amt=recipe["zoom_amt"], pan_scale=recipe["pan_scale"])
                if "motion" not in sp:
                    sp["motion"] = str(rng.choice(ks, p=pr / pr.sum()))
        if obj is None:
            obj = cls(sp, L)
        obj.prepare(ctx)
        scene_objs.append(obj)
        overlays.append(S.Overlay(sp, L))
        specs[i] = sp

    # 4) audio ---------------------------------------------------------------
    print("[4/6] Sound effects + background music + mix")
    snd = recipe["sound"]
    srng = np.random.default_rng(5)
    events = []
    for i, sp in enumerate(specs):
        for ev in sp.get("sfx", []):
            events.append((bounds[i][0] + ev.get("at", 0.0), ev["name"], ev.get("vol", 0.9), ev.get("dur")))
        if sp.get("kind") == "document" and sp.get("stamp"):
            events.append((bounds[i][0] + sp.get("stamp_at", (bounds[i][1] - bounds[i][0] + T) * 0.55) - T / 2, "stamp", 0.9, None))
        tr = sp.get("transition", "fade")
        if i < n - 1:
            if tr in AUTO_SFX:
                events.append((bounds[i][1] - 0.2, AUTO_SFX[tr][0], AUTO_SFX[tr][1], None))
            elif tr == "cut" and srng.random() < snd["sfx_on_cut"]:
                events.append((bounds[i][1] - 0.1, "whoosh", 0.35, None))
        ov = sp.get("overlay")
        if ov and snd.get("impact_on_text") and recipe["name"] != "default":
            events.append((bounds[i][0] + ov.get("at", 1.0) - 0.05, "impact", 0.45, None))
    for (t, _i) in cut_events:                     # quick cuts inside photo scenes
        if srng.random() < snd["sfx_on_cut"]:
            events.append((t, "whoosh", 0.3, None))
    mcfg = cfg.get("music", {})
    mpath = fm.resolve_local(mcfg.get("file"), "music")
    if not mpath and os.path.isdir("assets/music"):
        for f in sorted(os.listdir("assets/music")):
            if f.lower().endswith((".mp3", ".wav", ".ogg", ".m4a")):
                mpath = os.path.join("assets/music", f); break
    music = audio.load_music(mpath, total) if mpath else audio.make_music(total, mcfg.get("mood", "dark"))
    print("   music:", mpath or f"synthesized ({mcfg.get('mood', 'dark')})", f"| {len(events)} sound effects")
    voice = audio.decode(voice_wav)
    mix_wav = audio.mix(voice, events, music, total, os.path.join(C.WORK_DIR, "mix.wav"),
                        music_vol=mcfg.get("volume", snd["music_volume"]), duck=snd["duck"])

    # 5) render --------------------------------------------------------------
    print("[5/6] Rendering video (this is the slow part)")
    R.render(scene_objs, overlays, specs, bounds, total, cfg.get("grade", "documentary"), mix_wav, ass, a.out,
             recipe if recipe["use_grade_params"] or recipe["effects"]["glitch_prob"] else None)

    # 6) credits -------------------------------------------------------------
    print("[6/6] Credits")
    outdir = os.path.dirname(a.out) or "."
    fm.write_credits(os.path.join(outdir, "credits.txt"))
    if ass:
        import shutil
        shutil.copy(ass, os.path.join(outdir, "subtitles.ass"))
    print(f"DONE -> {a.out}")


if __name__ == "__main__":
    sys.exit(main())
