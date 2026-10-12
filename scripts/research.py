# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - research.py
Finds what is trending right now, stores it, and never suggests a topic twice.

Sources (all free, no API key):
  reddit   - public .json endpoints on chosen subreddits
  trends   - Google Trends daily RSS feed
  yt       - YouTube trending page titles (best effort)
  you      - your own topic list from config/rules.json

  python research.py            refresh all enabled sources
  python research.py --show     print what is stored
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import os
import argparse, html as html_lib, json, re, sqlite3, sys, time, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db

ROOT = (Path(__file__).resolve().parents[1])
RULES = ROOT / "config" / "rules.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
# Reddit throttles the generic browser UA hard (429s after a handful of subs
# in one run, confirmed 2026-09-02) - a distinctive descriptive UA plus a
# small gap between subreddits keeps requests under Reddit's per-UA limit.
REDDIT_UA = "AutoPostResearchBot/1.0 (topic discovery, low volume)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  title   TEXT UNIQUE,
  source  TEXT,
  score   INTEGER DEFAULT 0,
  hook    TEXT DEFAULT 'FACT',
  found   REAL,
  used    INTEGER DEFAULT 0,
  skipped INTEGER DEFAULT 0
);
"""


def log(m):
    try:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m, flush=True)
    except UnicodeEncodeError:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m.encode("ascii", "replace").decode("ascii"), flush=True)


def conn():
    c = db.conn()
    c.executescript(SCHEMA)
    return c


def rules():
    try:
        return json.loads(RULES.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fetch(url, timeout=25, ua=None):
    req = urllib.request.Request(url, headers={"User-Agent": ua or UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def clean(t):
    t = re.sub(r"\s+", " ", (t or "")).strip()
    t = re.sub(r"^(TIL|LPT|PSA)[ :\-]+", "", t, flags=re.I)
    # Every source here (YT/Reddit/Trends XML) returns HTML-escaped titles.
    # Without this the raw &quot; etc. flows into the topic, the prompt, and
    # straight out to blog.your-domain.example, which renders topic verbatim.
    try:
        import textsan
        t = textsan.sanitize(t)
    except Exception:
        t = html_lib.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:150]


# ---------------------------------------------------------------- sources
def from_reddit(subs, limit=8):
    """Reddit: hit BOTH top-of-day and rising for velocity (early trend signal)."""
    out = []
    for i, s in enumerate(subs):
        if i > 0:
            time.sleep(3.0)   # increased delay - avoids the 429 burst-limit
        got = 0
        last = None
        for url in ("https://www.reddit.com/r/" + s + "/top/.rss?t=day",
                    "https://old.reddit.com/r/" + s + "/top/.rss?t=day",
                    "https://www.reddit.com/r/" + s + "/rising/.rss"):
            try:
                xml = fetch(url, ua=REDDIT_UA, timeout=15)
                for m in re.finditer(r"<entry>.*?<title>(.*?)</title>", xml, re.S):
                    t = clean(re.sub(r"<!\[CDATA\[|\]\]>|'", "'", m.group(1)))
                    if len(t) > 22:
                        bonus = 3 if "rising" in url else 1
                        out.append((t, "reddit", bonus))
                        got += 1
                        if got >= limit:
                            break
                if got:
                    break
            except Exception as e:
                last = str(e)[:60]
                if "429" in last:
                    log(f"  reddit r/{s}: rate limited, waiting...")
                    time.sleep(5.0)  # extra wait on 429
        if not got:
            log("  reddit r/" + s + ": no items" + (" (" + last + ")" if last else ""))
    return out


# formats that reliably perform on Shorts/Reels - score is the 'hook strength'
HOOK_SCORE = [
    (r"\b(top|best|most)\b",            6,  "LIST (top/best)"),
    (r"\b(why|how|what if|the truth)\b", 5,  "WHY/HOW"),
    (r"\b(never|no one|nobody|scientists)\b", 5, "SCARCITY/EXCLUSIVE"),
    (r"\b(did you know|shocking|mind|surprising)\b", 5, "CURIOSITY"),
    (r"\b(real|true|hidden|secret|behind)\b", 4, "REVEAL"),
    (r"^(is|are|can|do|does)\b",         4,  "QUESTION"),
]

def score_topic(t):
    tl = t.lower()
    best = 2
    tag = "FACT"
    for pat, sc, lab in HOOK_SCORE:
        if re.search(pat, tl) and sc > best:
            best, tag = sc, lab
    return best, tag


def ai_enhance_score(topic_title, base_score):
    """Use Ollama to analyze topic relevance and quality. Return adjusted score."""
    if os.environ.get("AUTOPOST_NO_AI_ENHANCE"):
        return base_score
    try:
        import ai_priority
        if not ai_priority.background_ok():
            return base_score      # a job step is using the AI - it goes first
    except Exception:
        pass          # side desk: CPU/network only, never compete with a render for the GPU
    try:
        import urllib.request, urllib.parse
        OLLAMA_API = "http://127.0.0.1:11434/api/generate"
        R = rules()
        model = R.get("ollama_model", "llama3.2:3b")
        niche = R.get("niche", "self-healing, spiritual")

        prompt = f"""Analyze this topic for a content channel about: {niche}
Topic: "{topic_title}"

Rate 1-10:
1. Niche fit
2. Content quality
3. Engagement

Format: SCORE:X
Just the score."""

        payload = json.dumps({"model": model, "prompt": prompt, "stream": False, "options": {"num_predict": 10}}).encode()
        req = urllib.request.Request(OLLAMA_API, data=payload, headers={"Content-Type": "application/json"})

        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.load(r)["response"].strip()
            match = re.search(r"SCORE:(\d+)", resp)
            if match:
                ai_score = int(match.group(1))
                multiplier = 0.5 + (ai_score / 10.0) * 1.5
                return int(base_score * multiplier)
    except Exception as e:
        log(f"  AI enhance failed (will use base score): {str(e)[:80]}")
    return base_score


def from_trends(geo="US"):
    out = []
    try:
        xml = fetch("https://trends.google.com/trending/rss?geo=" + geo)
        for m in re.finditer(r"<title>(?!Daily Search Trends)(.*?)</title>", xml, re.S):
            t = clean(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1)))
            if len(t) > 3 and not t.lower().startswith(("watch ", "see ", "subscribe")):
                s = score_topic(t)[0]
                out.append((t, "trends", s))
    except Exception as e:
        log("  google trends failed: " + str(e)[:70])
    return out[:15]


JUNK = ("keyboard shortcut", "playback", "subtitles and closed", "spherical video",
        "try searching", "general\"", "hotkey", "sign in", "options", "seek ")


def from_youtube(search_terms=None):
    """YouTube killed the classic /feed/trending page (confirmed 2026-09-02 - it
    now redirects straight to the homepage, so the old regex scrape always found
    0 items, silently). Replaced with yt-dlp search across topic-relevant terms
    spanning every content category (not just one niche) - what's currently
    ranking for those searches IS the real trending signal within our niches,
    and yt-dlp is actively maintained against YouTube's page changes, unlike a
    hand-rolled regex against YouTube's obfuscated internal JSON."""
    out = []
    terms = search_terms or ["mind blowing facts", "self healing habits"]
    try:
        import yt_dlp
    except ImportError:
        log("  youtube search skipped: yt-dlp not installed (pip install yt-dlp)")
        return out
    opts = {"skip_download": True, "extract_flat": True, "quiet": True, "no_warnings": True}
    for term in terms:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info("ytsearch6:" + term, download=False)
            for e in (info or {}).get("entries", []) or []:
                t = clean(e.get("title") or "")
                low = t.lower()
                if t and len(t) > 8 and not any(j in low for j in JUNK):
                    out.append((t, "yt", score_topic(t)[0]))
        except Exception as ex:
            log("  youtube search '" + term + "' failed: " + str(ex)[:70])
    return out[:24]


def _srt_to_sentences(srt_text):
    """Strip SRT numbering/timestamps down to plain spoken text, then split
    into standalone sentences - used to pull real content out of a video
    instead of just its title."""
    lines = []
    for line in srt_text.splitlines():
        line = line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        lines.append(re.sub(r"<[^>]+>", "", line))  # strip any inline tags
    full = " ".join(lines)
    full = re.sub(r"\s+", " ", full).strip()
    sentences = re.split(r"(?<=[.!?])\s+", full)
    return [s.strip() for s in sentences if 30 <= len(s.strip()) <= 140]


def from_youtube_transcripts(search_terms, max_videos=2):
    """Bonus source: read the ACTUAL transcript of a couple of top search
    results per refresh (not all - full extraction + subtitle fetch is much
    slower than the flat title-only search above, so this deliberately stays
    small) instead of just their titles. A title just repeats itself; the
    transcript surfaces specific claims/facts the video actually discusses,
    which make sharper topic candidates. Free, zero-config, via yt-dlp
    (already installed) - same underlying technique used by Agent Reach
    (github.com/Panniantong/Agent-Reach) for YouTube access, wired directly
    here instead of depending on that separate interactive-agent tool."""
    out = []
    try:
        import yt_dlp
    except ImportError:
        return out
    opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    checked = 0
    for term in (search_terms or [])[:4]:   # only the first few terms - keep this fast
        if checked >= max_videos:
            break
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info("ytsearch1:" + term, download=False)
            entries = (info or {}).get("entries") or []
            if not entries:
                continue
            e = entries[0]
            caps = (e.get("automatic_captions") or {}).get("en") or (e.get("subtitles") or {}).get("en")
            if not caps:
                continue
            track = next((t for t in caps if t.get("ext") == "srt"), None)
            if not track:
                continue
            checked += 1
            srt = fetch(track["url"], timeout=20)
            sentences = _srt_to_sentences(srt)
            picked = 0
            for s in sentences:
                sc = score_topic(s)[0]
                if sc >= 4:   # only genuinely hook-worthy lines, not every sentence
                    out.append((clean(s), "yt", sc))
                    picked += 1
                    if picked >= 3:
                        break
        except Exception as ex:
            log("  youtube transcript '" + term + "' failed: " + str(ex)[:70])
    return out


def from_news(feeds, per_feed=8):
    """Headlines from news RSS feeds (Google News sections for Australia / world / entertainment / science / health,
    ABC Australia, SBS, BBC, Guardian) and Google News SEARCH feeds for 'connecting people' themes.
    Each feed is (label, url). A failing feed is skipped and logged - never fatal."""
    out = []
    for label, url in feeds:
        try:
            xml = fetch(url, timeout=20)
        except Exception as e:
            log("  news feed '%s' failed: %s" % (label, str(e)[:60]))
            continue
        n = 0
        for m in re.finditer(r"<item>.*?<title>(.*?)</title>", xml, re.S):
            t = clean(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1)))
            for _ in range(2):                                                # Google News appends ' - Publisher' (sometimes two)
                t = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,45}$", "", t).strip()
            low = t.lower()
            if len(t) < 18 or low.startswith(("watch ", "live:", "live updates", "video:", "photos:")):
                continue
            out.append((t, "news", score_topic(t)[0]))
            n += 1
            if n >= per_feed:
                break
        log("  news '%s': %d headline(s)" % (label, n))
    return out



def from_trendpulse(sources):
    """Cross-platform trending via the trend-pulse library (MIT, PyPI: trend-pulse,
    github.com/claude-world/trend-pulse) - real-time trending from Mastodon and
    Bluesky (platforms we ALREADY post to, so this is directly on-brand signal,
    not generic noise) plus Hacker News/Google Trends as broader pulse checks.
    Verified live 2026-09-02 before wiring in. Its own reddit source hits the
    same 403 wall ours does - Reddit-side, not fixable here - so we don't lean
    on it for reddit."""
    out = []
    try:
        import asyncio
        from trend_pulse.aggregator import TrendAggregator
        agg = TrendAggregator()
        result = asyncio.run(agg.trending(sources=sources, count=10))
        for item in result.get("merged", []):
            t = clean(str(item.get("keyword") or ""))
            if t and len(t) > 8:
                out.append((t, "trendpulse", score_topic(t)[0]))
    except Exception as e:
        log("  trend-pulse failed: " + str(e)[:100])
    return out[:24]


def from_manual(topics):
    """Your own curated topics don't need to win on regex hook-phrasing the way
    scraped clickbait titles do - they're on-brand by definition, that's WHY
    they're on this list. Give them a high floor score (still checked against
    score_topic in case the phrasing is even stronger) so the manual_list
    source weight actually dominates ranking, instead of losing to Reddit's
    naturally clickbait-style titles despite being weighted lower."""
    return [(clean(t), "you", max(score_topic(t)[0], 5)) for t in topics if t and len(t) > 3]


STOPWORDS = {"the","and","for","are","with","that","this","from","you","your",
    "how","what","when","why","who","a","an","in","on","to","of","is","it","s",
    "more","new","over","its","they","be","as","at","by","or","not","just",
    "can","will","does","do","did","really","actually","really","also"}


def _keywords(title):
    """Significant words only - used for near-duplicate detection (exact title
    matching alone lets 'why X heals you' and 'how X actually heals' both
    through as if they were fresh, unrelated ideas)."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if len(w) >= 4 and w not in STOPWORDS}


def _is_near_duplicate(title, used_keyword_sets, threshold=0.6):
    """True if this title shares most of its meaningful words with something
    already USED - catches reworded repeats that exact-title matching misses.
    Only checked against already-USED topics, never against the unused queue -
    genuinely different candidates in the queue should stay independent."""
    kw = _keywords(title)
    if len(kw) < 2:
        return False
    for used_kw in used_keyword_sets:
        if not used_kw:
            continue
        overlap = len(kw & used_kw) / max(len(kw), 1)
        if overlap >= threshold:
            return True
    return False


def check_fresh(topic, exclude_self=False):
    """The 'creative check' run automatically at the START of every make_video.py/
    make_post.py job, on whatever topic it was actually handed - not just topics
    that came through research.store()'s own dedup (a manually-typed or directly-
    passed topic skips that path entirely). Returns (is_fresh, similar_to_or_None).
    Cheap: same keyword-overlap logic as store()'s dedup, just run one more time
    at the point actual work begins, so nothing slips through."""
    with conn() as c:
        used_titles = [r[0] for r in c.execute("SELECT title FROM topics WHERE used=1")]
    kw = _keywords(topic)
    if len(kw) < 2:
        return True, None
    me = re.sub(r"\W+", " ", (topic or "").lower()).strip()
    for used in used_titles:
        if exclude_self and re.sub(r"\W+", " ", (used or "").lower()).strip() == me:
            continue          # the topic RESERVES itself as 'used' the moment a job is made; that is not a duplicate
        used_kw = _keywords(used)
        if not used_kw:
            continue
        if len(kw & used_kw) / max(len(kw), 1) >= 0.6:
            return False, used
    return True, None


# ---------------------------------------------------------------- store
def store(items):
    added, deduped = 0, 0
    with conn() as c:
        # ensure column exists on legacy DBs
        cols = [r[1] for r in c.execute("PRAGMA table_info(topics)")]
        if "hook" not in cols:
            c.execute("ALTER TABLE topics ADD COLUMN hook TEXT DEFAULT 'FACT'")
        used_titles = [r[0] for r in c.execute("SELECT title FROM topics WHERE used=1")]
        used_keyword_sets = [_keywords(t) for t in used_titles]
        for title, src, score in items:
            if _is_near_duplicate(title, used_keyword_sets):
                deduped += 1
                continue
            sc, tag = score_topic(title)
            score = max(score, sc)
            # AI enhancement: only analyze high-scoring topics (faster, smarter)
            if score >= 4:  # Only analyze topics worth considering
                score = ai_enhance_score(title, score)
            try:
                c.execute("INSERT INTO topics(title,source,score,hook,found) VALUES(?,?,?,?,?)",
                          (title, src, score, tag, time.time()))
                added += 1
                used_keyword_sets.append(_keywords(title))  # don't store two near-dupes in the same batch either
            except sqlite3.IntegrityError:
                pass  # already known - never suggest twice
    if deduped:
        log(f"  skipped {deduped} near-duplicate of already-used topic(s)")
    return added


# How often each source is asked (minutes). ARJUN's desk wakes every 5 min but a source is only hit when its own
# time is up - otherwise 20 news feeds + YouTube every 5 min means YouTube answers 429 (too many requests) and
# the PC does pointless work. When the topic list is SHORT (< SHORT_BELOW usable topics) every source is due now.
CADENCE = {"reddit": 30, "trends": 30, "news": 15, "youtube": 60, "transcripts": 240, "manual": 60, "trendpulse": 30}
SHORT_BELOW = 25
_STATE = Path(__file__).resolve().parent.parent / "data" / "research_state.json"


def _due(name, force, st):
    if force:
        return True
    return time.time() - float(st.get(name, 0)) >= CADENCE.get(name, 30) * 60


def refresh(force=False):
    R = rules()
    try:
        st = json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    try:
        with conn() as c:
            short = c.execute("SELECT COUNT(*) FROM topics WHERE used=0 AND skipped=0").fetchone()[0] < SHORT_BELOW
    except Exception:
        short = False
    force = force or short
    src = R.get("topic_sources", {})
    items, used = [], []
    if src.get("reddit", {}).get("enabled") and _due("reddit", force, st):
        st["reddit"] = time.time()
        subs = src["reddit"].get("subreddits", [])
        log("reddit: " + ", ".join("r/" + s for s in subs))
        got = from_reddit(subs)
        items += got
        used.append("reddit(" + str(len(got)) + ")")
    if src.get("google_trends", {}).get("enabled") and _due("trends", force, st):
        st["trends"] = time.time()
        got = []
        for geo in src["google_trends"].get("geos", ["US"]):
            log("google trends " + geo + "...")
            got += from_trends(geo)
        items += got
        used.append("trends(" + str(len(got)) + ")")
    if src.get("news", {}).get("enabled") and _due("news", force, st):
        st["news"] = time.time()
        log("news feeds (Australia, world, entertainment, science, health, connecting-people themes)...")
        got = from_news([(f[0], f[1]) for f in src["news"].get("feeds", [])])
        items += got
        used.append("news(" + str(len(got)) + ")")
    if src.get("youtube_trending", {}).get("enabled") and _due("youtube", force, st):
        st["youtube"] = time.time()
        log("youtube search (trending replacement)...")
        terms = src["youtube_trending"].get("search_terms")
        got = from_youtube(terms)
        items += got
        used.append("yt(" + str(len(got)) + ")")
        if _due("transcripts", force, st):
            st["transcripts"] = time.time()
            log("youtube transcripts (real content, not just titles)...")
            got_t = from_youtube_transcripts(terms)
            items += got_t
            used.append("yt-transcript(" + str(len(got_t)) + ")")
    if src.get("manual_list", {}).get("enabled"):
        got = from_manual(src["manual_list"].get("topics", []))
        items += got
        used.append("yours(" + str(len(got)) + ")")
    if src.get("trendpulse", {}).get("enabled") and _due("trendpulse", force, st):
        st["trendpulse"] = time.time()
        log("trend-pulse (mastodon/bluesky/hackernews)...")
        got = from_trendpulse(src["trendpulse"].get("sources", ["mastodon", "bluesky", "hackernews"]))
        items += got
        used.append("trendpulse(" + str(len(got)) + ")")
    try:
        _STATE.parent.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass
    if not items:
        log("no source was due - nothing to fetch this time")
    n = store(items)
    log("found " + str(len(items)) + " candidates from " + " ".join(used) + " -> " + str(n) + " new")
    return n


SOURCE_KEY = {"reddit": "reddit", "news": "news", "trends": "google_trends", "yt": "youtube_trending",
             "you": "manual_list", "trendpulse": "trendpulse"}


def unused(limit=40):
    """Ranked by hook score x source weight x real performance multiplier.
    Source weight: rules.json topic_sources.*.weight, so 'your own topics rank
    above scraped ones' (manual_list weight=5) is actually true, not just claimed
    in the UI. Performance multiplier: learn.category_multipliers() - categories
    that have actually gotten more YouTube views than average get a real boost,
    once there's enough posting history to trust it (neutral 1.0 until then)."""
    import variety, learn
    weights = {src: (rules().get("topic_sources", {}).get(key, {}).get("weight", 1) or 1)
               for src, key in SOURCE_KEY.items()}
    try:
        perf = learn.category_multipliers()
    except Exception:
        perf = {}
    with conn() as c:
        # rank across EVERY unused candidate, not just the most recently found -
        # a fixed-size recency pre-filter here quietly excluded older manual/"you"
        # topics once scraped volume passed the cap, even though they should have
        # outranked everything on score*weight. Table stays small (low hundreds),
        # so scoring the full set in Python is cheap - no real need to pre-limit.
        rows = c.execute("SELECT * FROM topics WHERE used=0 AND skipped=0").fetchall()
    def rank_key(r):
        w = weights.get(r["source"], 1)
        # your own curated topics ARE the brand direction - never let early,
        # possibly-noisy view-performance data suppress them. Performance
        # learning only adjusts scraped/discovered topics (reddit/trends/yt).
        p = 1.0 if r["source"] == "you" else perf.get(variety.classify_category(r["title"]), 1.0)
        return (-(r["score"] * w * p), -r["found"])
    ranked = sorted(rows, key=rank_key)
    return ranked[:limit]


def add_manual(title):
    return store([(clean(title), "you", 999)])


def mark(topic_id, field):
    with conn() as c:
        c.execute("UPDATE topics SET " + field + "=1 WHERE id=?", (topic_id,))


def classify_topic_attributes(topic, caption):
    """Classify topic's category, tone, and sentiment using Ollama.
    Returns: (category, tone, sentiment) - all strings or "unknown" if failed"""
    import json
    import urllib.request

    try:
        import ai_priority
        if not ai_priority.background_ok():
            return ("unknown", "unknown", "unknown")     # a job step is using the AI - it goes first
    except Exception:
        pass

    try:
        # Use same Ollama config as rest of system
        model = "qwen2.5:0.5b"
        OLLAMA = "http://127.0.0.1:11434/api/generate"

        # Classify in one prompt to save time
        prompt = (
            f'Topic: "{topic}"\n'
            f'Caption: "{caption[:200]}"\n\n'
            f'Classify this content.\n'
            f'Category (choose 1): self_healing, spiritual, mental, physical, wellness\n'
            f'Tone (choose 1): inspirational, educational, motivational, how-to, practical\n'
            f'Sentiment (choose 1): positive, neutral, actionable\n'
            f'Response format: "category|tone|sentiment" (lowercase)'
        )

        payload = {"model": model, "prompt": prompt, "stream": False}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})

        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)["response"].strip().lower()

        # Parse response
        parts = resp.split("|")
        if len(parts) == 3:
            category, tone, sentiment = parts[0].strip(), parts[1].strip(), parts[2].strip()
            # Validate
            valid_categories = {"self_healing", "spiritual", "mental", "physical", "wellness"}
            valid_tones = {"inspirational", "educational", "motivational", "how-to", "practical"}
            valid_sentiments = {"positive", "neutral", "actionable"}

            category = category if category in valid_categories else "wellness"
            tone = tone if tone in valid_tones else "inspirational"
            sentiment = sentiment if sentiment in valid_sentiments else "positive"

            return (category, tone, sentiment)
    except Exception as e:
        log(f"  classification failed (non-fatal): {str(e)[:100]}")

    return ("wellness", "inspirational", "positive")  # Safe defaults


def detect_sentiment(title):
    """Quick sentiment detection: positive, neutral, or actionable.
    Uses keyword matching for speed (no AI call needed)."""
    if not title:
        return "neutral"

    title_lower = title.lower()

    positive_words = {"amazing", "incredible", "powerful", "transform", "heal", "improve",
                      "perfect", "excellent", "wonderful", "beautiful", "energy", "bliss",
                      "peace", "joy", "love", "success", "victory", "inspire"}

    actionable_words = {"how to", "tips", "guide", "methods", "techniques", "tricks",
                       "steps", "practice", "learn", "master", "improve", "increase"}

    pos_score = sum(1 for w in positive_words if w in title_lower)
    action_score = sum(1 for w in actionable_words if w in title_lower)

    if pos_score > action_score:
        return "positive"
    elif action_score > pos_score:
        return "actionable"
    else:
        return "neutral"


def detect_momentum(title, source="unknown"):
    """Estimate trend momentum: rising, stable, or falling.
    Returns multiplier: 1.5 (rising), 1.0 (stable), 0.8 (falling)."""
    try:
        # Check if topic is NEW (trending recently) or OLD (been trending)
        with conn() as c:
            r = c.execute(
                "SELECT COUNT(*) as cnt FROM topics WHERE title LIKE ? AND found > ?",
                (f"%{title[:30]}%", time.time() - 86400 * 3)  # In last 3 days
            ).fetchone()

        # If appeared multiple times recently = stable/rising trend
        count = r["cnt"] if r else 0
        if count >= 3:
            return 1.3  # Rising (appearing multiple times)
        elif count >= 1:
            return 1.0  # Stable (appeared once)
        else:
            return 0.8  # Falling (old or one-time)
    except Exception:
        return 1.0  # Default neutral


def score_topic_enhanced(topic_title, base_score=0):
    """Enhanced topic scoring combining multiple factors.
    Returns: final_score for topic ranking."""
    try:
        # Base score (from basic scoring)
        if base_score == 0:
            base_score = score_topic(topic_title)

        # Niche relevance (AI enhancement)
        niche_score = ai_enhance_score(topic_title, base_score)

        # Sentiment bonus
        sentiment = detect_sentiment(topic_title)
        sentiment_bonus = 1.2 if sentiment == "positive" else (1.1 if sentiment == "actionable" else 1.0)

        # Momentum bonus
        momentum = detect_momentum(topic_title)

        # Final combined score
        final_score = niche_score * sentiment_bonus * momentum

        return max(0, min(100, final_score))  # Clamp 0-100
    except Exception as e:
        log(f"  enhanced scoring failed: {str(e)[:100]}")
        return base_score


def calculate_variety_score(topic, recent_n=10):
    """Calculate diversity score based on last N posts.
    Checks: keyword overlap, category, tone, sentiment variety.
    Returns: score 0-1 (higher = more unique)"""
    try:
        with db.conn() as c:
            # Get recent posts
            recent = c.execute(
                "SELECT topic, category, tone, sentiment FROM runs "
                "WHERE status='ok' AND topic IS NOT NULL "
                "ORDER BY started DESC LIMIT ?",
                (recent_n,)
            ).fetchall()

        if not recent:
            return 1.0  # First post = maximum variety

        # Extract recent values
        recent_topics = [r["topic"] for r in recent]
        recent_categories = [r["category"] for r in recent if r["category"]]
        recent_tones = [r["tone"] for r in recent if r["tone"]]
        recent_sentiments = [r["sentiment"] for r in recent if r["sentiment"]]

        # 1. Keyword uniqueness (existing logic)
        def keywords(text):
            return set(re.findall(r'\b[a-z]{3,}\b', (text or "").lower()))

        new_kw = keywords(topic)
        keyword_overlaps = [len(new_kw & keywords(t)) / max(len(new_kw), 1) for t in recent_topics]
        keyword_uniqueness = 1 - (sum(keyword_overlaps) / len(keyword_overlaps) if keyword_overlaps else 0)

        # 2. Category diversity (avoid repeating same category)
        if recent_categories:
            category_repeat_ratio = recent_categories.count(recent_categories[0]) / len(recent_categories)
            category_diversity = 1 - category_repeat_ratio
        else:
            category_diversity = 1.0

        # 3. Tone variety (avoid same tone)
        if recent_tones:
            tone_repeat_ratio = recent_tones.count(recent_tones[0]) / len(recent_tones)
            tone_variety = 1 - tone_repeat_ratio
        else:
            tone_variety = 1.0

        # 4. Sentiment balance (mix of positive/neutral/actionable)
        if recent_sentiments:
            unique_sentiments = len(set(recent_sentiments))
            sentiment_mix = min(unique_sentiments / 3, 1.0)  # Max 3 types
        else:
            sentiment_mix = 1.0

        # Weighted score
        variety_score = (keyword_uniqueness * 0.35 +
                        category_diversity * 0.25 +
                        tone_variety * 0.25 +
                        sentiment_mix * 0.15)

        return max(0, min(1, variety_score))  # Clamp 0-1
    except Exception as e:
        log(f"  variety score calculation failed (non-fatal): {str(e)[:100]}")
        return 0.5  # Default neutral score


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--add")
    a = ap.parse_args()
    if a.add:
        print("added:", add_manual(a.add))
    elif a.show:
        for r in unused(50):
            print(" %-8s %5s  %s" % (r["source"], r["score"], r["title"][:90]))
    else:
        refresh()
