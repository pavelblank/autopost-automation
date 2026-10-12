# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""DISPATCHER - agent 21. The only thing that decides WHAT gets made and WHEN.

WHY A NEW AGENT (this answers the owner's "which agent already handles the schedule?"):
none of the 18 chain agents can. Every one of them is per-job and each waits for a
predecessor, so the decision has to be made BEFORE a job exists. That decision lives in
scripts/scheduler.py today, outside the department, where nothing audits it. DISPATCHER
brings it inside, where the SUPERVISOR can see a schedule that silently stopped working.

the owner's daily rule (8 posts a day, 2 video + 3 text + 3 image+text):

   08:45  IMAGE   morning scroll - X / Bluesky / Mastodon / Telegram
   10:15  TEXT    the single strongest news slot of the day
   12:00  VIDEO   photoreal human character      <- the owner picked this hour
   14:30  IMAGE   mid-afternoon, professional audience
   17:30  TEXT    late afternoon - the biggest general window
   19:30  IMAGE   early evening - TikTok / Discord
   21:30  TEXT    evening unwind
   23:00  VIDEO   cartoon / animation             <- the owner picked this hour
           (topic RELATED to the 12:00 one but NOT the same)

WHY THESE HOURS - not guesswork. Source: Buffer, "The Best Time to Post on Social
Media in 2026", published 25 Mar 2026, drawn from their 2026 State of Social Media
Engagement report over 52 MILLION posts. The two video hours are the owner's own choice
and are kept exactly as he set them.

  X          weekdays 08:00-11:00, peak 09:00-10:00. Wednesday best. Sat is dead.
  Bluesky    behaves like X - early morning, pre-lunch.
  Mastodon   same news-check pattern as X.
  Telegram   morning commute and the evening unwind.
  TikTok     18:00-23:00 is its strongest window by a wide margin.
  YouTube    Shorts: evenings 18:00-23:00. Long-form: mornings 08:00-11:00.
  Discord    evenings, when people are actually at their computer.
  Overall    midweek (Wed/Thu) wins; the small hours and weekends are the weakest.

  So the two big windows are 08:00-10:00 (news platforms) and 18:00-22:00 (video and
  chat platforms), and the day is built around those two plus a solid late afternoon.

SPACING: the closest pair is 1h30 apart. Nothing collides, and no two posts land
together. Overlap with other slots is checked by /api/live and the office page.

SAFETY
  * NEVER enqueues while a generation is in flight - one GPU, one job at a time
  * NEVER enqueues while paused, or while department.enabled is false and he has not asked
  * NEVER publishes - it only enqueues. ELLA still needs MARCUS's release decision.
  * NEVER picks a topic already used, and the cartoon topic must not duplicate the
    photoreal one from the same day
  * at most one enqueue per slot per day, tracked on disk, so a restart cannot double-post
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import json, os, random, re, sys, time
from datetime import datetime
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

import db                       # noqa: E402
import paths                    # noqa: E402

LOG = ROOT / "data" / "dispatcher.log"
STATE = ROOT / "data" / "dispatcher.json"
RULES = ROOT / "config" / "rules.json"
METRICS = ROOT / "data" / "content_metrics.json"

# THE DAILY PLAN - 8 posts: 2 video, 3 text, 3 image+text.
# slot id, hour, minute, kind, style, platforms
SLOTS = [
    ("image-morning",   8, 45, "image", None,      ["socialai"]),
    ("text-morning",    10, 15, "text",  None,      ["socialai"]),
    ("video-real",      12,  0, "video", "real",    ["socialai"]),
    ("image-afternoon", 14, 30, "image", None,      ["socialai"]),
    ("text-late",       17, 30, "text",  None,      ["socialai"]),
    ("image-evening",   19, 30, "image", None,      ["socialai"]),
    ("text-evening",    21, 30, "text",  None,      ["socialai"]),
    ("video-cartoon",   23,  0, "video", "cartoon", ["socialai"]),
]

# What each kind of post can actually go to (only used when live publishing is on).
_KIND_TARGETS = {
    "text":  ["socialai", "telegram", "telegram_channel", "bluesky", "discord", "mastodon", "facebook",
              "threads", "linkedin", "wordpress", "webhook"],
    "image": ["socialai", "telegram", "telegram_channel", "bluesky", "discord", "mastodon", "facebook",
              "linkedin", "wordpress", "webhook"],
    "video": ["socialai", "youtube", "tiktok", "telegram", "telegram_channel", "discord", "facebook", "webhook"],
}


def live_targets(kind, platforms):
    """The plan says 'socialai' for every slot (the safe default while the rails were closed). With live publishing
    on (department.live_publish) a slot goes to every destination that accepts this kind of post AND is allowed
    by department.allowed_platforms. Rails off -> unchanged."""
    try:
        d = (_load(RULES, {}).get("department") or {})
        if not d.get("live_publish"):
            return platforms
        allowed = set(d.get("allowed_platforms") or [])
        out = [p for p in _KIND_TARGETS.get(kind, platforms) if p in allowed]
        return out or platforms
    except Exception:
        return platforms


# A day that ships 8 posts must never quietly lose one. This is the floor, checked on
# every pass; if fewer than this many slots fired, the supervisor is told something broke.
EXPECTED_POSTS_PER_DAY = len(SLOTS)

# styles are added to the job so NOAH can pick the right Wan2.1 prompt style
STYLE_TAG = {"real": "photoreal live-action human", "cartoon": "2D cartoon animation"}


def log(m):
    line = "[DISP %s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), m)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(st):
    """Write the slot-claim state ATOMICALLY, and never lie about it.

    This file is the only thing stopping the dispatcher from enqueueing the same slot
    twice. A plain write_text() is not enough: it truncates first, so a crash - or a
    second dispatcher writing at the same moment - leaves a half-written file, and the
    next pass reads a slot as "not claimed" and posts again. That is exactly the
    double-post this file exists to prevent.

    Write to a temp file in the same folder, then os.replace(), which is atomic on
    Windows and on POSIX. A reader either sees the old file or the new one, never a
    broken one.
    """
    try:                                   # a test / simulation must never write the LIVE claim file (3 corruptions 2026-10-11)
        import publish as _pub
        if _pub.started_from_tests() and Path(STATE).resolve() == (ROOT / "data" / "dispatcher.json").resolve():
            raise RuntimeError("refused: a process started from tools\\ tried to write the live dispatcher.json")
    except ImportError:
        pass
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(STATE))       # atomic swap


def _load_state():
    """Read the claim state. Returns None if it cannot be trusted.

    This does NOT go through _load(), because _load() swallows a parse error and hands
    back the default {}. That default means "no slot has fired today", which is exactly
    the wrong answer when the real reason is a damaged file - every slot would re-fire
    in one pass and publish the whole day twice.

    So the parse is done here, explicitly, and any failure returns None. Callers treat
    None as "do not post".
    """
    if not STATE.exists():
        return {}                        # genuinely nothing claimed yet - that is fine
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception as e:
        log("claim state UNREADABLE (%s) - refusing to enqueue until it is repaired"
            % str(e)[:80])
        return None
    if not isinstance(st, dict):
        log("claim state is not a dict - refusing to enqueue")
        return None
    return st


def done_today(slot_id):
    st = _load_state()
    if st is None:
        return True                     # fail closed: do NOT repost
    return st.get(slot_id) == datetime.now().strftime("%Y%m%d")


def claim(slot_id):
    """Take exclusive ownership of one slot for today.

    Confirmed by re-reading the file afterwards. If the write did not stick, this returns
    False rather than True - a claim that cannot be proven is a claim that will be
    re-attempted next pass and post the same thing twice.
    """
    st = _load_state()
    if st is None:
        return False
    day = datetime.now().strftime("%Y%m%d")
    if st.get(slot_id) == day:
        return False
    st[slot_id] = day
    try:
        _save(st)
    except Exception as e:
        log("slot %s claim FAILED to persist (%s) - not enqueueing" % (slot_id, str(e)[:90]))
        return False
    # prove it stuck
    back = _load_state()
    if back is None or back.get(slot_id) != day:
        log("slot %s claim did not stick - not enqueueing" % slot_id)
        return False
    return True


def rules():
    return _load(RULES, {})


def enabled():
    r = rules()
    dept = r.get("department") or {}
    # the owner's master switches. The dispatcher obeys them exactly as the old scheduler did.
    if r.get("paused"):
        return False, "rules.paused is true"
    if not dept.get("enabled"):
        return False, "department.enabled is false (manual mode)"
    return True, "ok"


def busy():
    """True when ANY generation or department step is in flight. One GPU."""
    try:
        import department_queue as Q
        c = Q.schema()
        r = c.execute("SELECT COUNT(*) n FROM department_steps WHERE status IN "
                      "('ready','running')").fetchone()
        if r and r["n"]:
            return True, "%s department step(s) runnable" % r["n"]
    except Exception:
        pass
    try:
        if db.conn().execute("SELECT COUNT(*) n FROM runs WHERE status='running' "
                             "OR (finished IS NULL AND started > ?)",
                             (time.time() - 3 * 3600,)).fetchone()["n"]:
            return True, "a run is still in progress"
    except Exception:
        pass
    return False, ""


def used_topics():
    try:
        rows = db.conn().execute("SELECT topic FROM topics_used").fetchall()
        return {re.sub(r"\W+", " ", (r[0] or "").lower()).strip() for r in rows}
    except Exception:
        return set()


def _norm(t):
    return re.sub(r"\W+", " ", (t or "").lower()).strip()


def related_but_different(topic_a):
    """The cartoon topic must be on the SAME theme, not the same sentence."""
    first = _norm(topic_a).split()[0] if _norm(topic_a) else ""
    if first:
        b = db.conn().execute(
            "SELECT title FROM topics WHERE used=0 AND lower(title) LIKE ? "
            "ORDER BY random() LIMIT 1", ("%" + first + "%",)).fetchone()
        if b and _norm(b[0]) != _norm(topic_a):
            return b[0]
    return None


def pick_topic(kind, style, partner_topic=None):
    """An UNUSED topic (topics.used flag) that is not already in topics_used.
    Never repeats something posted before, and never repeats the partner topic."""
    used = used_topics()
    if partner_topic:                      # cartoon: must differ from the photoreal one
        used.add(_norm(partner_topic))
    try:                                   # the editor adds these columns lazily: a fresh DB must not crash the picker
        with db.conn() as _c:
            _have = [r[1] for r in _c.execute("PRAGMA table_info(topics)")]
            for _col in ("quality", "post_topic", "quality_reason"):
                if _col not in _have:
                    _c.execute("ALTER TABLE topics ADD COLUMN %s TEXT" % _col)
    except Exception:
        pass
    # Only topics ARJUN's editorial gate has approved (quality='ok': a clean, general subject) or the owner's own
    # ('you'). A raw headline is never a post idea - that is how video-channel titles got rendered overnight.
    rows = db.conn().execute(
        "SELECT title FROM topics WHERE used=0 AND skipped=0 AND title IS NOT NULL AND title != '' "
        "AND (quality='ok' OR source='you') ORDER BY score DESC, random() LIMIT 120").fetchall()
    import research
    import fact_grounding
    for r in rows:
        if _norm(r[0]) in used or not fact_grounding.is_subject(r[0])[0]:
            continue                       # already used, or a personal story (not a subject)
        try:
            fresh, _similar = research.check_fresh(r[0])
        except Exception:
            fresh = True
        if fresh:
            return r[0]
    return None


def video_kind(rules_d, style):
    return "video"


def arm_today():
    """Call when automation is switched ON. Slots whose time has already passed today are marked done, so turning the
    system on at 11:30 PM does not fire four 'catch-up' posts at midnight. Only FUTURE slots run. Returns the slots skipped."""
    now = datetime.now()
    skipped = []
    for slot_id, hh, mm, kind, style, platforms in SLOTS:
        if now.replace(hour=hh, minute=mm, second=0, microsecond=0) <= now and not done_today(slot_id):
            if claim(slot_id):
                skipped.append(slot_id)
    if skipped:
        log("automation armed: today's passed slots marked done (no catch-up): %s" % ", ".join(skipped))
    return skipped


def run_once():
    """One pass. Returns what it decided, for the dashboard and the tests."""
    ok, why = enabled()
    if not ok:
        return {"enabled": False, "reason": why, "enqueued": []}

    now = datetime.now()
    done_enq = []
    for slot_id, hh, mm, kind, style, platforms in SLOTS:
        slot_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < slot_dt:
            continue                                  # slot has not arrived
        if done_today(slot_id):
            continue                                  # already handled today
        late = (now - slot_dt).total_seconds()
        if late > 8 * 3600:
            log("slot %s missed by %.0fh - writing it off for today" % (slot_id, late / 3600))
            claim(slot_id)
            continue

        b, bwhy = busy()
        if b:
            # hold the slot; do not burn it while the GPU is working
            log("slot %s due but held: %s" % (slot_id, bwhy))
            continue

        # FIRST: is a finished post waiting in the STOCK? Then publish it now - no generation, no waiting.
        try:
            import stock
            jid_s = stock.serve(kind, style, slot_id)
        except Exception as e:
            jid_s = None
            log("slot %s: stock check failed (%s) - generating normally" % (slot_id, str(e)[:80]))
        if jid_s:
            claim(slot_id)
            log("SERVED FROM STOCK %s -> %s kind=%s style=%s" % (slot_id, jid_s, kind, style))
            done_enq.append({"slot": slot_id, "job_id": jid_s, "kind": kind, "style": style, "topic": "(from stock)"})
            continue

        partner = None
        if kind == "video" and style == "cartoon":
            # the photoreal video from earlier today, if there was one
            r = db.conn().execute(
                "SELECT topic FROM runs WHERE kind='video' AND started > ? "
                "ORDER BY started DESC LIMIT 1", (now.replace(hour=0).timestamp(),)).fetchone()
            partner = r[0] if r else None

        topic = pick_topic(kind, style, partner)
        if not topic:
            log("slot %s: no unused topic available" % slot_id)
            continue

        if not claim(slot_id):
            continue
        try:
            import department_queue as Q
            jid = Q.enqueue(topic, kind, live_targets(kind, platforms), notify=True, source="dispatcher")
            if jid:
                jobmeta = Q.paths.job_dir(jid, "intake") / "job.json"
                meta = _load(jobmeta, {})
                meta["style"] = style
                meta["style_prompt_tag"] = STYLE_TAG.get(style)
                meta["slot"] = slot_id
                Q.write_job(jid, meta)
                log("ENQUEUED %s -> %s  kind=%s style=%s  '%s'"
                    % (slot_id, jid, kind, style, topic[:60]))
                done_enq.append({"slot": slot_id, "job_id": jid, "kind": kind,
                                 "style": style, "topic": topic})
            else:
                log("slot %s: enqueue returned nothing" % slot_id)
        except Exception as e:
            log("slot %s FAILED: %s" % (slot_id, str(e)[:150]))
    return {"enabled": True, "enqueued": done_enq,
            "busy": busy()[0], "slots": [(s[0], "%02d:%02d" % (s[1], s[2])) for s in SLOTS]}


def watch(interval=300):
    log("dispatcher online (every %.0f min). slots: %s"
        % (interval / 60, ", ".join("%s %02d:%02d" % (s[0], s[1], s[2]) for s in SLOTS)))
    while True:
        try:
            run_once()
        except Exception as e:
            log("pass failed (non-fatal): %s" % str(e)[:140])
        time.sleep(interval)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        i = sys.argv[sys.argv.index("--watch") + 1]
        watch(int(i) if i.isdigit() else 300)
    else:
        print(json.dumps(run_once(), indent=2, default=str))