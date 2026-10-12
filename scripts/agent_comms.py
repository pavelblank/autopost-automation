# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""AGENT COMMS - turn the department's audit trail into a readable conversation.

ANSWER TO "can I see what the agents are saying to each other?" - YES, and it is not
simulated. Every agent already writes a row to `department_jobs` before it acts, and
every agent hands the next one ARTEFACT FILES. So the conversation is real data:

    08:41  MAYA      -> SOPHIE   "creative brief written (qwen2.5:14b)"
    08:41  SOPHIE    -> ARJUN    "script written (qwen2.5:14b)"
    08:42  GRACE     -> SOPHIE   REJECTED "quality defects: video has no audio track"
              the work went BACK to SOPHIE, and ARJUN/LEO/... were re-armed

That last shape - a rejection and a route-back - is exactly what you want to see, and
this module renders it as a conversation rather than a table of IDs.

Three views:
  timeline(job_id)  one job, in order, as messages between agents
  live()             what is happening right now across every job
  thread(agent)      everything one agent has said and been told
"""
import json, re, sys, time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

import db            # noqa: E402
import department_queue as Q   # noqa: E402

# Artefact each agent PRODUCES, used to show "handed over X to Y"
PRODUCES = {
    "intake": "job.json", "maya": "creative_brief.json", "arjun": "research_report.json",
    "sophie": "script.json", "daniel": "storyboard.json", "aisha": "visual_spec.json",
    "leo": "emotion_guide.json", "oliver": "planning_audit.json",
    "isabella": "generation_approval.json", "noah": "the generated asset",
    "grace": "quality_inspection.json", "ethan": "final_content_audit.json",
    "chloe": "one package per platform", "marcus": "release_decision.json",
    "ella": "publish.log + posts rows", "victoria": "live_post_audit.json",
    "james": "remediation.json", "helena": "final_report.txt",
}

SIDE = {"SUPERVISOR": "sidecar", "RETENTION": "sidecar", "DISPATCHER": "sidecar", "IDLE_MANAGER": "sidecar"}

# what a status means, in plain words for a human watching
PLAIN = {
    "ok": "done", "done": "done", "created": "opened the job", "working": "started",
    "started": "started", "running": "running", "skipped": "not needed here",
    "rejected": "sent it back", "routed": "sent it back", "retry": "trying again",
    "failed": "failed", "dead": "STOPPED - SUPERVISOR resolving",
    "action": "acted", "decision": "decided", "archived": "closed",
    "waits": "waiting", "waiting": "waiting", "held": "held back",
}


def _ago(ts):
    try:
        s = time.time() - float(ts)
        if s < 60:
            return "%ds ago" % int(s)
        if s < 3600:
            return "%dm ago" % int(s // 60)
        if s < 86400:
            return "%dh ago" % int(s // 3600)
        return "%dd ago" % int(s // 86400)
    except Exception:
        return ""


def _clean(reason):
    r = (reason or "").strip()
    if not r:
        return ""
    # strip the engine's bookkeeping prefix so it reads like speech, not like SQL
    r = re.sub(r"^SUPERVISOR (\w+):\s*", r"", r)
    r = re.sub(r"^(retry \d+/\d+) - ", r"", r)
    return r[:220]


def _next_of(agent):
    for k, a, stage, dep in Q.STEPS:
        if k == agent:
            for k2, a2, s2, d2 in Q.STEPS:
                if d2 == k:
                    return a2
    return None


def timeline(job_id=None, limit=400):
    """One job as a conversation. `None` = the most recent job."""
    try:
        c = db.conn()
        if not job_id:
            r = c.execute("SELECT job_id, MAX(ts) m FROM department_jobs GROUP BY job_id "
                          "ORDER BY m DESC LIMIT 1").fetchone()
            job_id = r["job_id"] if r else None
        if not job_id:
            return {"job_id": None, "messages": []}
        rows = c.execute("SELECT * FROM department_jobs WHERE job_id=? ORDER BY id ASC LIMIT ?",
                         (job_id, limit)).fetchall()
        steps = {r["step_key"]: dict(r) for r in
                 Q.schema().execute("SELECT * FROM department_steps WHERE job_id=?",
                                    (job_id,)).fetchall()}
    except Exception as e:
        return {"job_id": job_id, "error": str(e)[:120], "messages": []}

    msgs = []
    for r in rows:
        agent = (r["agent"] or "?").lower()
        status = (r["status"] or "").lower()
        reason = _clean(r["reason"])
        kind = SIDE.get((r["agent"] or "").upper(), "chain")
        verb = PLAIN.get(status, status)
        # who is this message addressed to?
        if status in ("rejected", "routed", "failed", "retry"):
            back = Q.FIRST_RESPONSIBLE.get(agent)
            to = back.upper() if back else "HUMAN"
        elif status in ("done", "ok", "skipped", "action"):
            to = (_next_of(agent) or "CHAIN END").upper()
        else:
            to = ""
        msgs.append({
            "ts": r["ts"], "time": time.strftime("%H:%M:%S", time.localtime(r["ts"] or 0)),
            "ago": _ago(r["ts"]),
            "agent": (r["agent"] or "?").upper(), "kind": kind,
            "stage": r["stage"], "status": status, "verb": verb,
            "to": to, "message": reason,
            "hands_over": PRODUCES.get(agent, ""),
        })
    # the plan, so the conversation has context
    plan = []
    for key, st in steps.items():
        plan.append({"step": key, "status": st["status"], "reason": _clean(st["reason"]),
                     "version": st["version"],
                     "not_before": st["not_before"]})
    return {"job_id": job_id, "messages": msgs, "steps": plan,
            "counts": {k: sum(1 for m in msgs if m["status"] == k)
                       for k in {m["status"] for m in msgs}}}


def live(limit=40):
    """What is happening right now, newest first, across every job."""
    try:
        rows = db.conn().execute(
            "SELECT job_id, agent, stage, status, reason, ts FROM department_jobs "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except Exception as e:
        return {"error": str(e)[:120], "events": []}
    out = []
    for r in rows:
        status = (r["status"] or "").lower()
        out.append({
            "job_id": r["job_id"], "agent": (r["agent"] or "?").upper(),
            "stage": r["stage"], "status": status,
            "verb": PLAIN.get(status, status),
            "message": _clean(r["reason"]),
            "time": time.strftime("%H:%M:%S", time.localtime(r["ts"] or 0)),
            "ago": _ago(r["ts"]),
            "sidecar": (r["agent"] or "").upper() in SIDE,
        })
    return {"events": out}


def threads():
    """Every agent, what it produced, and how many times it has said something."""
    try:
        rows = db.conn().execute(
            "SELECT agent, status, COUNT(*) n, MAX(ts) m FROM department_jobs "
            "GROUP BY agent, status").fetchall()
    except Exception as e:
        return {"error": str(e)[:120], "agents": []}
    agg = {}
    for r in rows:
        a = (r["agent"] or "?").upper()
        d = agg.setdefault(a, {"agent": a, "says": 0, "last": 0,
                               "rejections": 0, "actions": 0})
        d["says"] += r["n"]
        d["last"] = max(d["last"], r["m"])
        if (r["status"] or "").lower() in ("rejected", "failed", "dead"):
            d["rejections"] += r["n"]
        if (r["status"] or "").lower() == "action":
            d["actions"] += r["n"]
    spec = {a["key"]: a for a in Q.SPEC.get("agents", [])}
    out = []
    for key, a in Q.AGENTS.items():
        d = agg.get(a.get("name", key).upper(), {})
        out.append({
            "key": key, "name": a.get("name", key.upper()), "stage": a.get("stage"),
            "role": a.get("role", ""), "produces": PRODUCES.get(key, ""),
            "says": d.get("says", 0), "rejections": d.get("rejections", 0),
            "last": _ago(d["last"]) if d.get("last") else "-",
            "sidecar": a.get("name", "").upper() in SIDE,
        })
    for k, v in agg.items():
        if k in SIDE:
            out.append({"key": k.lower(), "name": k, "stage": 0, "role": "sidecar",
                        "produces": "", "says": v["says"], "rejections": 0,
                        "last": _ago(v["last"]), "sidecar": True})
    return {"agents": out}


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "live"
    if what == "timeline":
        print(json.dumps(timeline(sys.argv[2] if len(sys.argv) > 2 else None),
                         indent=2, default=str)[:6000])
    elif what == "threads":
        print(json.dumps(threads(), indent=2, default=str)[:4000])
    else:
        print(json.dumps(live(), indent=2, default=str)[:4000])