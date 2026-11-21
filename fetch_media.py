"""Free media finder: Wikimedia Commons only (open licences). Every file used is logged to credits."""
import hashlib, html, os, re, time
import requests
from . import config as C

API = "https://commons.wikimedia.org/w/api.php"
CREDITS = []          # filled while rendering -> written to output/credits.txt
_last = [0.0]


def _get(url, **kw):
    wait = 0.6 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    return requests.get(url, headers={"User-Agent": C.UA}, timeout=60, **kw)


def _ok_license(meta):
    lic = (meta.get("LicenseShortName", {}).get("value", "") or "").lower()
    if not lic:
        return None
    if any(b in lic for b in ("nc", "nd", "fair use", "non-commercial", "all rights")):
        return None
    good = ("public domain", "pd", "cc0", "cc by", "cc-by", "attribution", "no restrictions")
    return meta["LicenseShortName"]["value"] if any(g in lic for g in good) else None


def _clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def _search(query, video=False, limit=25):
    q = ("filetype:video " if video else "filetype:bitmap ") + query
    params = dict(action="query", format="json", generator="search", gsrnamespace=6, gsrlimit=limit,
                  gsrsearch=q, prop="imageinfo", iiprop="url|size|mime|extmetadata", iiurlwidth=2400)
    try:
        r = _get(API, params=params)
        pages = r.json().get("query", {}).get("pages", {})
    except Exception as e:
        print(f"  [fetch] search failed for '{query}': {e}")
        return []
    items = sorted(pages.values(), key=lambda p: p.get("index", 99))
    out = []
    for p in items:
        ii = (p.get("imageinfo") or [None])[0]
        if not ii:
            continue
        lic = _ok_license(ii.get("extmetadata", {}))
        if not lic:
            continue
        out.append(dict(title=p["title"], info=ii, license=lic))
    return out


def _download(url, ext):
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    path = os.path.join(C.CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ext)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    r = _get(url, stream=True)
    r.raise_for_status()
    with open(path, "wb") as f:
        for ch in r.iter_content(1 << 16):
            f.write(ch)
    return path


def _credit(kind, it):
    ii = it["info"]
    meta = ii.get("extmetadata", {})
    CREDITS.append(f"[{kind}] {it['title']} | author: {_clean(meta.get('Artist', {}).get('value', 'unknown'))[:80]}"
                   f" | licence: {it['license']} | {ii.get('descriptionurl', '')}")


def find_image(query, used=None, min_w=700):
    """Returns local jpg/png path or None. `used` = set of titles to avoid repeats."""
    used = used if used is not None else set()
    for it in _search(query):
        ii = it["info"]
        if it["title"] in used or ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if ii.get("width", 0) < min_w:
            continue
        url = ii.get("thumburl") or ii["url"]
        try:
            path = _download(url, ".jpg" if ii["mime"] == "image/jpeg" else ".png")
        except Exception as e:
            print(f"  [fetch] download failed: {e}")
            continue
        used.add(it["title"])
        _credit("image", it)
        print(f"  [fetch] image '{query}' -> {it['title']} ({it['license']})")
        return path
    print(f"  [fetch] no open-licence image for '{query}'")
    return None


def find_video(query, max_mb=60):
    for it in _search(query, video=True, limit=15):
        ii = it["info"]
        if not (ii.get("mime", "").startswith("video") or ii.get("mime") == "application/ogg"):
            continue
        if ii.get("size", 0) > max_mb * 1024 * 1024:
            continue
        try:
            ext = os.path.splitext(ii["url"])[1] or ".webm"
            path = _download(ii["url"], ext)
        except Exception as e:
            print(f"  [fetch] video download failed: {e}")
            continue
        _credit("video", it)
        print(f"  [fetch] video '{query}' -> {it['title']} ({it['license']})")
        return path
    print(f"  [fetch] no open-licence video for '{query}'")
    return None


def resolve_local(name, folder):
    """Use a file from assets/<folder>/ if the script names one (your own licensed media)."""
    if not name:
        return None
    for p in (name, os.path.join("assets", folder, name)):
        if os.path.exists(p):
            return p
    return None


def write_credits(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("MEDIA CREDITS (Wikimedia Commons - open licences)\n"
                "Paste this in your video description. CC BY / CC BY-SA require attribution.\n\n")
        f.write("\n".join(sorted(set(CREDITS))) or "No web media used.")
        f.write("\n")
