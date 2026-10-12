# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""AutoPost - image_sources.py
Get a topic-matched background image with NO API keys and NO cost.

Fallback chain (each step verified 2026-09-25):
  1. Pollinations  (AI image gen, free)          - primary
  2. Wikimedia Commons (real CC photos, search)  - if Pollinations down/fails
  3. Openverse     (real CC photos, search)      - if Commons fails
Returns True on success (writes dst); False only if ALL failed (caller then
paints a solid-color frame as the absolute last resort).
"""
import json, os, re, time, urllib.parse, urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
KEYWORDS = 5   # how many search terms queried from a prompt


def _req(url, timeout=45):
    """Fetch URL, return raw bytes. (Reads inside the context manager - the
    response object would otherwise be closed before the caller can read.)"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _keywords(prompt):
    """Pull the meaningful keywords from an image prompt so the search engines
    (which need simple nouns, not long AI-style prompt sentences) find real,
    on-topic photos. Emails a phrase, skips style words."""
    STOP = {"cinematic", "warm", "healing", "soft", "light", "aesthetic", "moody",
            "mysterious", "discovery", "wonder", "stylish", "trendy", "fashion",
            "ambient", "energetic", "bright", "motivating", "modern", "calming",
            "serene", "hopeful", "engaging", "polished", "high", "detail", "glowing",
            "neon", "scientific", "background", "photo", "image", "the", "a", "an",
            "and", "with", "of", "in", "on", "for", "of", "this"}
    words = re.findall(r"[A-Za-z]{4,}", prompt.lower())
    keep = [w for w in words if w not in STOP and not w.isdigit()]
    # drop duplicate stems
    seen, out = set(), []
    for w in keep:
        if w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= KEYWORDS:
            break
    return out or ["nature"]


def _from_wikimedia(prompt, dst, seed):
    """Wikimedia Commons: search free-to-use photos, download a video-frame
    thumbsized original (960px fits our 720x1280 canvas fine)."""
    terms = _keywords(prompt)
    query = " ".join(terms)
    api = ("https://commons.wikimedia.org/w/api.php?action=query&generator=search"
           "&gsrsearch=" + urllib.parse.quote(query) +
           "&gsrnamespace=6&gsrlimit=8&prop=imageinfo&iiprop=url|size|extmetadata"
           "&iiurlwidth=900&format=json")
    j = json.loads(_req(api))
    pages = (j.get("query", {}).get("pages", {}) or {}).values()
    cands = [p for p in pages if p.get("imageinfo")]
    if not cands:
        return False
    for p in cands[:5]:
        try:
            ii = p["imageinfo"][0]
            # skip svg/pdf/audio/video - need a raster photo
            mime = ii.get("mime", "")
            if not mime.startswith("image/"):
                continue
            url = ii.get("thumburl") or ii.get("url")
            if not url:
                continue
            with open(dst, "wb") as f:
                f.write(_req(url))
            if Path(dst).stat().st_size > 3000:
                return True
        except Exception:
            continue
    return False


def _from_openverse(prompt, dst, seed):
    """Openverse (CC search, free, no key): backup photo source."""
    terms = _keywords(prompt)
    query = " ".join(terms)
    api = ("https://api.openverse.org/v1/images/?q=" + urllib.parse.quote(query) +
           "&license_type=commercial&page_size=5")
    j = json.loads(_req(api))
    for res in (j.get("results") or [])[:4]:
        try:
            url = res.get("url")
            if not url:
                continue
            with open(dst, "wb") as f:
                f.write(_req(url))
            if Path(dst).stat().st_size > 3000:
                return True
        except Exception:
            continue
    return False


def make_image(prompt, dst, seed, fallback_log=None):
    """Write a topic-matched image to dst. Returns True on success.

    Chain: Pollinations (ai gen) -> Wikimedia Commons -> Openverse.
    A failed chain does NOT silently paint a black frame - the caller gets
    False and paints solid color only then. Logs each fallback via fallback_log
    (a callable), so the dashboard/db can say WHY an image was substituted."""
    def note(m):
        try:
            if fallback_log:
                fallback_log(m)
        except Exception:
            pass

    q = urllib.parse.quote(prompt[:300], safe="")
    url = ("https://image.pollinations.ai/prompt/" + q +
           "?width=720&height=1280&nologo=true&model=flux&seed=" + str(seed))
    for a in range(3):
        try:
            with open(dst, "wb") as f:
                f.write(_req(url, timeout=180))
            if Path(dst).stat().st_size > 3000:
                return True
        except Exception as e:
            note(f"  image retry {a+1}: {e}")
        time.sleep(3)

    note("  pollinations down -> trying Wikimedia Commons")
    if _from_wikimedia(prompt, dst, seed):
        note("  used Wikimedia Commons (on-topic real photo)")
        return True

    note("  commons miss -> trying Openverse")
    if _from_openverse(prompt, dst, seed):
        note("  used Openverse (on-topic real photo)")
        return True

    note("  ALL image sources down -> solid-color frame (last resort)")
    return False