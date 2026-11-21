"""Global render settings. main.py calls set_quality() before anything is drawn."""
W, H, FPS, S = 1920, 1080, 30, 1.0   # S = scale factor for pixel sizes (W/1920)
CRF, PRESET = 18, "medium"
TRANSITION = 0.6                      # seconds of overlap between scenes
TAIL = 1.5                            # seconds of music/fade after voice ends
CACHE_DIR = "media_cache"
WORK_DIR = "work"
UA = ("AI-Documentary-Maker/1.0 (https://github.com/kartikkumar9665kartik-syg/"
      "AI-Documentary-Maker; educational documentary tool)")


def set_quality(q: str):
    global W, H, FPS, S, CRF, PRESET
    if q == "draft":
        W, H, FPS, CRF, PRESET = 1280, 720, 24, 26, "veryfast"
    else:
        W, H, FPS, CRF, PRESET = 1920, 1080, 30, 18, "medium"
    S = W / 1920.0
