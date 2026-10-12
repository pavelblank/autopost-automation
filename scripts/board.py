# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""JOBS BOARD - one row per job: what it is, who is working on it now, who has finished, and where it stands.
Read-only; feeds the Conversations page (left box)."""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import json
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
CHAIN = ["intake", "maya", "sophie", "daniel", "leo", "oliver", "isabella", "noah", "grace", "ella", "victoria", "helena"]


def _names():
    try:
        d = json.loads((ROOT / "videogen" / "AGENT_SPEC.json").read_text(encoding="utf-8"))
        return {a["key"]: a["name"] for a in d.get("agents", [])}
    except Exception:
        return {}


def board(limit=12):
    import department_queue as Q
    names = _names()
    c = Q.schema()
    jobs = [r["job_id"] for r in c.execute(
        "SELECT job_id, MAX(rowid) m FROM department_steps GROUP BY job_id ORDER BY m DESC LIMIT ?", (limit * 3,))]
    out = []
    try:
        import stock
        held = {i["id"]: i for i in stock.items()}
    except Exception:
        held = {}
    for jid in jobs:
        m = Q.read_job(jid) or {}
        if m.get("dry_run"):
            continue
        rows = {r["step_key"]: r for r in Q._rows(jid)}
        if not rows or all((r["reason"] or "").startswith("ARCHIVED") for r in rows.values()):
            continue
        chain = [k for k in CHAIN if k in rows]
        st = {k: rows[k]["status"] for k in chain}
        done = sum(1 for k in chain if st[k] in ("done", "skipped"))
        running = [k for k in chain if st[k] == "running"]
        ready = [k for k in chain if st[k] == "ready"]
        dead = [k for k in chain if st[k] == "dead"]
        it = held.get(jid)
        if any((r["reason"] or "").startswith(("cancelled", "ARCHIVED")) for r in rows.values()):
            state, now = "cancelled", "closed - not used"
        elif dead:
            state, now = "stopped", names.get(dead[0], dead[0].upper()) + " stopped - action needed"
        elif running:
            state, now = "working", "%s is working" % names.get(running[0], running[0].upper())
        elif it and it["status"] == "ready":
            state, now = "stock", "finished - waiting in stock for its scheduled slot"
        elif ready:
            state, now = "waiting", "%s is next" % names.get(ready[0], ready[0].upper())
        elif done == len(chain):
            state, now = "done", "finished and published"
        else:
            state, now = "waiting", "waiting"
        last = c.execute("SELECT agent, status, reason, ts FROM department_jobs WHERE job_id=? AND status IN ('done','skipped') "
                         "ORDER BY id DESC LIMIT 1", (jid,)).fetchone()
        out.append({"job_id": jid, "topic": (m.get("topic") or "")[:90], "kind": m.get("kind"), "style": m.get("style"),
                    "stock": bool(m.get("stock")) and not m.get("stock_released"), "state": state, "now": now,
                    "progress": "%d/%d" % (done, len(chain)),
                    "steps": [{"key": k, "name": names.get(k, k.upper()).split()[0], "status": st[k]} for k in chain],
                    "last_done": (last["agent"] + " finished: " + (last["reason"] or "")[:70]) if last else "",
                    "ts": (last["ts"] if last else 0)})
        if len(out) >= limit:
            break
    return out
