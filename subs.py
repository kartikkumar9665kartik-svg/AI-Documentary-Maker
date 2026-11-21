"""Automatic subtitles (ASS) from the script text, timed to the voice-over."""
import re


def _ts(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _split_lines(words, per_line=7, max_chars=40):
    lines, cur = [], []
    for w in words:
        if cur and (len(cur) >= per_line or len(" ".join(cur + [w])) > max_chars):
            lines.append(cur); cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def build_chunks(scene_texts, bounds, time_at, starts_idx, max_words=7):
    """Group script words into 1-2 line subtitle chunks. Returns [(start,end,text)]."""
    chunks = []
    k0 = 0
    for si, text in enumerate(scene_texts):
        words = text.split()
        if not words:
            continue
        s_t, e_t = bounds[si]
        base = int(starts_idx[si])
        # sentence-aware chunking: break after . ? ! । or at max_words*2
        buf, buf_start = [], 0
        groups = []
        for i, w in enumerate(words):
            buf.append(w)
            if re.search(r"[.?!।,;:]$", w) and len(buf) >= 3 or len(buf) >= max_words * 2 - 2:
                groups.append((buf_start, list(buf))); buf_start = i + 1; buf = []
        if buf:
            groups.append((buf_start, buf))
        for gi, (gs, gw) in enumerate(groups):
            st = min(max(time_at(base + gs), s_t), e_t)
            if gi + 1 < len(groups):
                en = min(max(time_at(base + groups[gi + 1][0]), s_t), e_t)
            else:
                en = e_t
            if en - st < 0.4:
                en = min(st + 0.8, e_t + 0.2)
            lines = _split_lines(gw, per_line=max_words)
            chunks.append((st, en, "\\N".join(" ".join(l) for l in lines)))
    # avoid overlaps
    for i in range(len(chunks) - 1):
        if chunks[i][1] > chunks[i + 1][0]:
            chunks[i] = (chunks[i][0], chunks[i + 1][0] - 0.02, chunks[i][2])
    return chunks


def write_ass(chunks, path, font="Noto Sans Devanagari", size=62):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,4,2,2,120,120,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(head)
        for st, en, tx in chunks:
            tx = tx.replace("{", "(").replace("}", ")")
            f.write(f"Dialogue: 0,{_ts(st)},{_ts(en)},Default,,0,0,0,,{{\\fad(120,120)}}{tx}\n")
    return path
