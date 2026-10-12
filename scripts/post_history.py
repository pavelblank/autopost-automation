# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""POST HISTORY - one file that remembers what was already posted, and what to ignore.

File: videogen/AGENT_MEMORY/post_history.json   (agent memory - never cleaned)
  entries : every PUBLISHED topic (topic, date, platforms, source machine)
  ignore  : every topic ever used/picked (do not choose again)
Also: videogen/AGENT_MEMORY/audit_memory.jsonl - one line per audit decision, appended.
It is self-updating: refresh() folds newly published runs from brain.db into the file
(at most once per REFRESH_SECS). The audit gate (publish_guard DUPLICATE step) calls
find_repeat(); research already skips topics_used, which is seeded from the same list.
Fail-open: any error here returns "no repeat" so a bug in this file can never stall a job.
"""
import json, os, time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
# The history is AGENT MEMORY, not a draft: it must not live in videogen/WORK/, because
# agent 20 RETENTION deletes finished jobs from there and the clean-slate sweep empties it.
# It sits next to the agents' rooms so they can see it, and no cleanup path can reach it
# (retention._guard only ever allows videogen/WORK/ and out/).
MEMORY = ROOT / "videogen" / "AGENT_MEMORY"
FILE = MEMORY / "post_history.json"
LEGACY = ROOT / "data" / "post_history.json"      # old location, still honoured
AUDIT_LOG = MEMORY / "audit_memory.jsonl"          # one line per ETHAN audit

REFRESH_SECS = 600
THRESHOLD = 0.7        # keyword overlap; research uses 0.6 for picking, 0.7 here avoids false blocks


def _path():
    """Where the file really is: new location first, then the old one, so a half-finished
    move can never lose the history."""
    if FILE.exists():
        return FILE
    if LEGACY.exists():
        return LEGACY
    return FILE


def note_audit(topic, verdict, reason="", agent="ETHAN", job_id=None):
    """Append one line. Cheap by design: it never reads or rewrites the whole history, so
    an agent can call it on every audit without slowing anything down."""
    try:
        MEMORY.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "agent": agent,
               "topic": (topic or "")[:160], "verdict": verdict,
               "reason": (reason or "")[:200]}
        if job_id:
            rec["job"] = job_id
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def recent_audits(n=20):
    """The last n audit decisions, newest first. This is what makes the NEXT audit cheap:
    the agent sees what previous audits concluded instead of re-deriving it."""
    try:
        if not AUDIT_LOG.exists():
            return []
        out = []
        for ln in AUDIT_LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-n:][::-1]:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
        return out
    except Exception:
        return []


def _kw(text):
    try:
        import research
        return research._keywords(text)
    except Exception:
        return {w for w in (text or "").lower().split() if len(w) > 3}


def load():
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "entries": [], "ignore": []}


def _save(d):
    d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    f = _path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def refresh(force=False):
    """Add published runs (and used topics) from the live DB that the file does not know yet."""
    try:
        if not force and _path().exists() and time.time() - _path().stat().st_mtime < REFRESH_SECS:
            return
        import db
        d = load()
        seen = {e["topic"].strip().lower() for e in d["entries"]}
        ign = {t.strip().lower() for t in d["ignore"]}
        with db.conn() as c:
            runs = c.execute("SELECT id, topic, started FROM runs WHERE published=1 AND topic IS NOT NULL").fetchall()
            plat = {}
            for r in c.execute("SELECT run_id, platform FROM posts WHERE status='ok'").fetchall():
                plat.setdefault(r["run_id"], set()).add(r["platform"])
            used = [r["topic"] for r in c.execute("SELECT topic FROM topics_used").fetchall()]
        for r in runs:
            k = r["topic"].strip().lower()
            if k in seen:
                continue
            seen.add(k)
            d["entries"].append({"topic": r["topic"], "date": time.strftime("%Y-%m-%d", time.localtime(r["started"] or 0)),
                                 "platforms": sorted(plat.get(r["id"], [])), "src": "100", "run": r["id"]})
        for t in used:
            if t and t.strip().lower() not in ign:
                ign.add(t.strip().lower()); d["ignore"].append(t)
        _save(d)
    except Exception:
        pass


def record(topic, platforms, run_id=None):
    """HELENA calls this when a job is published: the topic goes into the publication history so
    ARJUN (topic scout) and NADIA (second audit) never pick it again. Idempotent."""
    try:
        if not topic:
            return False
        d = load()
        k = topic.strip().lower()
        if any(e["topic"].strip().lower() == k for e in d["entries"]):
            for e in d["entries"]:
                if e["topic"].strip().lower() == k:
                    e["platforms"] = sorted(set(e.get("platforms", [])) | set(platforms or []))
        else:
            d["entries"].append({"topic": topic, "date": time.strftime("%Y-%m-%d"),
                                 "platforms": sorted(set(platforms or [])), "src": "helena", "run": run_id})
        if k not in {t.strip().lower() for t in d["ignore"]}:
            d["ignore"].append(topic)
        _save(d)
        return True
    except Exception:
        return False


def find_repeat(topic, exclude_run_id=None):
    """Return the earlier posted topic this one repeats, else None."""
    try:
        if not topic:
            return None
        refresh()
        kw = _kw(topic)
        if len(kw) < 2:
            return None
        for e in load()["entries"]:
            if exclude_run_id is not None and e.get("run") == exclude_run_id:
                continue
            okw = _kw(e["topic"])
            if okw and len(kw & okw) / max(len(kw), 1) >= THRESHOLD:
                return e["topic"]
    except Exception:
        return None
    return None
