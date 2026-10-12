# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""ARJUN'S EDITORIAL GATE - turns raw internet headlines into READY topics, or throws them away.

Why this exists: the raw topic bank is full of video titles, celebrity gossip, deaths, product deals, sports scores
and emoji clickbait. Building posts from that gave 'Best TikToks of 2025 So Far', 'Top Yahoo Boys' ... nothing a
calm self-healing / emotional-connection / curiosity page can stand behind.

Nothing is posted from a raw headline any more. A raw row is only a CANDIDATE. This module
  1. rejects the obvious junk with cheap rules (no model call),
  2. asks the local model to judge the rest against the page's niche and to REWRITE it as a clean, general,
     timeless subject (no names, no brands, no news event),
  3. stores the result as a NEW topic row (source='arjun', quality='ok') - the READY list.
Only quality='ok' rows (or the owner's own 'you' topics) are ever picked by NADIA / the dispatcher / the stock producer.

It is a side-desk job: it runs only while the AI engine is idle (never next to a render) and fails CLOSED - if the
model cannot be reached nothing is approved.
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nowindow  # noqa: E402,F401

ROOT = (Path(__file__).resolve().parents[1])
DB = ROOT / "data" / "brain.db"
MODEL = "qwen2.5:14b"

_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d]")
_BAD_WORDS = re.compile(
    r"\b(trending|viral|funniest|funny videos?|compilation|rewind|top hits|hits \d{4}|deals?|prices?|discount|coupon|sale|"
    r"box office|debut|trailer|season \d+|episode|live updates?|scores?|ladder|fixture|qualif\w+|pole|odds|vs\.?|"
    r"leaked|exposed|scandal|arrest\w*|murder\w*|killed|dies|died|death|dead|shooting|stabb\w*|crash\w*|"
    r"election|parliament|minister|coalition|senate|trump|putin|war\b|attack\w*|strike\w*|"
    r"top \d+|best \d+|\d+ best|\d+ top|ranking\w*|rankings|playlist|official video|lyrics|subscribe|"
    r"best of|highlights?|reaction|reacts?|unboxing|review)\b", re.I)


def conn():
    c = sqlite3.connect(str(DB), timeout=30)
    c.row_factory = sqlite3.Row
    cols = [r[1] for r in c.execute("PRAGMA table_info(topics)")]
    for col in ("quality", "post_topic", "quality_reason"):
        if col not in cols:
            c.execute("ALTER TABLE topics ADD COLUMN %s TEXT" % col)
    c.commit()
    return c


def quick_reject(title):
    """A reason string if the headline is obviously not a usable subject, else None. No model call."""
    t = (title or "").strip()
    if len(t) < 18 or len(t) > 150:
        return "wrong length"
    if _EMOJI.search(t):
        return "emoji / clickbait formatting"
    if re.search(r"[|#]|\s[lI]\s", t):
        return "video / channel title"
    caps = [w for w in re.findall(r"[A-Za-z]{3,}", t) if w.isupper()]
    if len(caps) >= 2:
        return "SHOUTING clickbait"
    if re.match(r"^\d+\s", t):
        return "listicle"
    m = _BAD_WORDS.search(t)
    if m:
        return "not our kind of subject (%s)" % m.group(0)
    try:
        import fact_grounding
        ok, why = fact_grounding.is_subject(t)
        if not ok:
            return why
    except Exception:
        pass
    return None


PROMPT = """You are the senior topic editor of a calm social-media page. Page niche: {niche}.
Audience: general public. Tone: warm, honest, factual, never sensational.

Headline found on the internet:
"{title}"

Decide if this can become a TIMELESS, GENERAL post subject for the page. It is SUITABLE only if the idea behind it is about
mind, body, emotions, healing habits, sleep, stress, relationships, friendship, loneliness, kindness, community, human
connection, nature, science, space, history, or a genuinely interesting everyday fact - and it can be written WITHOUT naming any
real person, brand, product, TV show, song, team or place-specific news event.
It is NOT SUITABLE if it is gossip, a celebrity, a death or crime, politics, sports results, shopping or deals, a TV/film/music
item, a video compilation title, a personal story, medical advice, or anything that needs the original article to make sense.

Reply ONLY JSON: {{"suitable": true/false, "post_topic": "a clear general subject as ONE sentence of 7 to 16 words, no names", "reason": "short"}}
If not suitable, post_topic is an empty string."""


def _ask(title):
    niche = "self-healing, spiritual, emotional connection and general curiosity"
    try:
        r = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig"))
        niche = r.get("niche") or niche
    except Exception:
        pass
    body = json.dumps({"model": MODEL, "stream": False, "format": "json", "keep_alive": "10m",
                       "options": {"temperature": 0.2, "num_predict": 140},
                       "prompt": PROMPT.format(niche=niche, title=title.replace('"', "'"))}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(json.loads(r.read().decode())["response"])


def _keywords(t):
    return set(w for w in re.findall(r"[a-z]{4,}", (t or "").lower()))


def _clean_subject(s):
    s = re.sub(r"\s+", " ", (s or "").strip().strip('"').strip())
    s = s.rstrip(".")
    words = s.split()
    if not (6 <= len(words) <= 20):
        return None
    if quick_reject(s):
        return None
    try:
        import fact_grounding
        if fact_grounding.person_names(s):
            return None
    except Exception:
        pass
    return s[0].upper() + s[1:]


def ready_count():
    c = conn()
    n = c.execute("SELECT COUNT(*) FROM topics WHERE used=0 AND skipped=0 AND (quality='ok' OR source='you')").fetchone()[0]
    c.close()
    return n


def pending_count():
    c = conn()
    n = c.execute("SELECT COUNT(*) FROM topics WHERE quality IS NULL AND used=0 AND skipped=0 AND source!='arjun' AND source!='you'").fetchone()[0]
    c.close()
    return n


def analyse_pending(limit=6, deadline_s=150):
    """Judge up to `limit` raw candidates (newest first). Returns a one-line summary. Fails closed."""
    c = conn()
    t0 = time.time()
    rows = c.execute("SELECT id, title, source, score FROM topics WHERE quality IS NULL AND used=0 AND skipped=0 "
                     "AND source!='arjun' AND source!='you' ORDER BY found DESC, score DESC LIMIT ?", (limit * 6,)).fetchall()
    ok = no = errs = 0
    llm_calls = 0
    existing = [_keywords(r[0]) for r in c.execute("SELECT COALESCE(post_topic,title) FROM topics WHERE quality='ok'")]
    for r in rows:
        if llm_calls >= limit or time.time() - t0 > deadline_s:
            break
        why = quick_reject(r["title"])
        if why:
            c.execute("UPDATE topics SET quality='no', quality_reason=?, skipped=1 WHERE id=?", (why, r["id"]))
            no += 1
            continue
        try:
            llm_calls += 1
            out = _ask(r["title"])
        except Exception as e:
            errs += 1
            if errs >= 2:
                break                      # model unreachable: stop, approve nothing (fail closed)
            continue
        subj = _clean_subject(out.get("post_topic")) if out.get("suitable") else None
        if not subj:
            c.execute("UPDATE topics SET quality='no', quality_reason=?, skipped=1 WHERE id=?",
                      ((str(out.get("reason") or "not suitable"))[:120], r["id"]))
            no += 1
            continue
        # FACTS FIRST (2026-10-11): a subject only becomes READY once real, citable sources exist for it. A topic with
        # no sources forces the writer to avoid every name / number / quote, and the grounding check then rejects the
        # script at SOPHIE - far too late. The fetch also warms fact_grounding's cache, so NADIA reuses it for free.
        try:
            import fact_grounding
            gr = fact_grounding.research_topic(subj)
        except Exception:
            errs += 1                      # network trouble is not the topic's fault: leave the row for the next pass
            if errs >= 2:
                break
            continue
        if gr.get("mode") != "grounded" or len(gr.get("verified_claims") or []) < 2:
            c.execute("UPDATE topics SET quality='no', quality_reason=?, skipped=1 WHERE id=?",
                      ("no verifiable facts found for: " + subj[:90], r["id"]))
            no += 1
            continue
        kw = _keywords(subj)
        if any(kw and e and len(kw & e) / max(len(kw), 1) >= 0.6 for e in existing):
            c.execute("UPDATE topics SET quality='no', quality_reason='same idea already ready', skipped=1 WHERE id=?", (r["id"],))
            no += 1
            continue
        try:
            c.execute("INSERT INTO topics(title,source,score,hook,found,quality,post_topic,quality_reason) VALUES(?,?,?,?,?,?,?,?)",
                      (subj, "arjun", (r["score"] or 0) + 2, "FACT", time.time(), "ok", subj, "from: " + r["title"][:100]))
            existing.append(kw)
            c.execute("UPDATE topics SET quality='done', skipped=1, quality_reason=? WHERE id=?", ("approved as: " + subj[:100], r["id"]))
            ok += 1
        except sqlite3.IntegrityError:
            c.execute("UPDATE topics SET quality='no', quality_reason='duplicate subject', skipped=1 WHERE id=?", (r["id"],))
            no += 1
    c.commit()
    c.close()
    return "%d approved, %d rejected, %d still waiting for review%s" % (ok, no, pending_count(), " (model unreachable)" if errs >= 2 else "")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(analyse_pending(n))
    print("ready topics:", ready_count())
