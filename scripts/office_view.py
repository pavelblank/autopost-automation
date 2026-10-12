# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""AutoPost - Agent Office live view (READ-ONLY).

Feeds the dashboard page /office. It only READS: brain.db is opened with
mode=ro, the spec / rules / room files are only read. Nothing here can start,
stop, enqueue or change an agent, so viewing the office can never affect the
pipeline. (Deliberately a shared data contract - no code from agents-office.)
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import json
import re
import socket
import sqlite3
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
DB = ROOT / "data" / "brain.db"
SPEC = ROOT / "videogen" / "AGENT_SPEC.json"
RULES = ROOT / "config" / "rules.json"
OFFICE = ROOT / "videogen" / "OFFICE"

STAGES = {1: "Start", 2: "Creative", 3: "Review", 4: "Generation", 5: "Publish",
          6: "Post check", 7: "Final audit"}


CHAIN = ["intake", "maya", "sophie", "daniel", "leo", "oliver", "isabella",
         "noah", "grace", "ella", "victoria", "helena"]
DEP = {k: (CHAIN[i - 1] if i else None) for i, k in enumerate(CHAIN)}
VISUAL_ONLY = ("daniel",)


def _stage_to_agent(stage):
    """Map the REAL pipeline's runs.stage text onto the agent that does that work (derived, read-only)."""
    t = (stage or "").lower()
    if t.startswith("1/6") or "writing" in t:
        return "sophie"
    if "classif" in t or "variety" in t:
        return "oliver"
    if "editorial" in t:
        return "ethan"
    if t.startswith("2/6") or "presenter" in t:
        return "aisha"
    if t[:2] in ("3/", "4/") or "scene" in t or "joining" in t or "image" in t:
        return "noah"
    if t.startswith("5/6") or "mixing" in t or "music" in t:
        return "grace"
    return "noah"


def _derive_from_run(run, kind):
    """-> (per-agent status dict, reason dict, label) from one runs row."""
    st, stage = run["status"], run["stage"] or ""
    status, reason = {}, {}
    if st == "running":
        act = _stage_to_agent(stage)
        i = CHAIN.index(act)
        for j, k in enumerate(CHAIN):
            status[k] = "done" if j < i else "running" if j == i else "pending"
        reason[act] = stage
    elif st == "failed":
        act = _stage_to_agent(stage)
        i = CHAIN.index(act)
        for j, k in enumerate(CHAIN):
            status[k] = "done" if j < i else "failed" if j == i else "pending"
        reason[act] = (run["error"] or stage or "run failed")[:120]
    else:                                   # finished ok
        if stage == "preview_pending" or run["approval"] == "pending":
            i = CHAIN.index("ella")
            for j, k in enumerate(CHAIN):
                status[k] = "done" if j < i else "ready" if j == i else "pending"
            reason["ella"] = "waiting for your approval"
        else:
            for k in CHAIN:
                status[k] = "done"
    if kind == "text":
        for k in VISUAL_ONLY:
            if status.get(k) == "done":
                status[k] = "skipped"
                reason[k] = "text-only post: visual stage not applicable"
    return status, reason


def _ro():
    c = sqlite3.connect("file:%s?mode=ro" % DB.as_posix(), uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _room_status(room):
    """Parse the Field|Value table of a room's STATUS.md plus its last history lines."""
    out = {"status": "?", "current_job": "-", "version": "-", "updated": "-", "waiting_on": "-", "history": []}
    try:
        txt = (OFFICE / room / "STATUS.md").read_text(encoding="utf-8")
    except Exception:
        return out
    keys = {"Status": "status", "Current job": "current_job", "Current version": "version",
            "Last updated": "updated", "Waiting on": "waiting_on"}
    for m in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$", txt, re.M):
        if m.group(1) in keys:
            out[keys[m.group(1)]] = m.group(2)
    hist = txt.split("## History", 1)[-1]
    out["history"] = [l.strip("- ").strip() for l in hist.splitlines()
                      if l.strip() and not l.strip().startswith("_(")][-3:]
    return out


def _port_up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.6):
            return True
    except Exception:
        return False


def _code_has(name, needle):
    try:
        return needle in (ROOT / "scripts" / name).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def _handlers_real():
    """Every agent (including the ones merged into others) has a real handler."""
    try:
        src = (ROOT / "scripts" / "department.py").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    have = set(re.findall(r'^@agent\("(\w+)"\)', src, re.M))
    return len(have) >= 18 and "recorded only" not in src


def _poller():
    try:
        w = json.loads((ROOT / "data" / "department_watch.json").read_text(encoding="utf-8"))
        age = time.time() - float(w.get("ts", 0))
        pid = w.get("pid")
        alive = age < 30
        if not alive and pid:
            # busy running one long step (e.g. a video render) = alive, just not beating
            try:
                alive = nowindow.pid_alive(pid) and age < 5400
            except Exception:
                pass
        return {"alive": alive, "age": round(age, 1), "pid": pid}
    except Exception:
        return {"alive": False, "age": None, "pid": None}


def _ago(sec):
    sec = max(0, int(sec))
    if sec < 90:
        return "%ds" % sec
    if sec < 5400:
        return "%dm" % (sec // 60)
    if sec < 172800:
        return "%dh" % (sec // 3600)
    return "%dd" % (sec // 86400)


def snapshot():
    spec = _json(SPEC, {})
    rules = _json(RULES, {})
    roster = spec.get("roster") or []
    all_spec = spec.get("agents", [])
    # the office shows the ROSTER (14); agents merged into another keep their spec only for timeouts
    agents_spec = ([next(a for a in all_spec if a["key"] == k) for k in roster if any(a["key"] == k for a in all_spec)]
                   if roster else all_spec)
    now = time.time()
    data = {"ts": now, "error": None}
    steps, events, lease = [], [], None
    counts, last_report, live = {}, None, None
    try:
        with _ro() as c:
            steps = [dict(r) for r in c.execute("SELECT rowid AS rid, * FROM department_steps")]
            events = [dict(r) for r in c.execute(
                "SELECT job_id, agent, stage, role, status, reason, version, ts FROM department_jobs ORDER BY id DESC LIMIT 60")]
            row = c.execute("SELECT * FROM gen_lease WHERE id=1").fetchone()
            lease = dict(row) if row else None
            for t in ("department_packages", "live_audits", "remediation", "final_reports"):
                counts[t] = c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            lr = c.execute("SELECT job_id, status, ts FROM final_reports ORDER BY ts DESC LIMIT 1").fetchone()
            last_report = dict(lr) if lr else None
            last_run = c.execute("SELECT id, topic, status, started, kind FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            live_row = c.execute("SELECT id, topic, status, stage, kind, approval, error, started, finished FROM runs "
                                 "WHERE status='running' OR (started > ? AND status IN ('ok','failed')) "
                                 "ORDER BY (status='running') DESC, id DESC LIMIT 1", (now - 600,)).fetchone()
            live = dict(live_row) if live_row else None
            data["last_run"] = dict(last_run) if last_run else None
            data["runs_total"] = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    except Exception as e:
        data["error"] = repr(e)

    # the newest department job (by creation order) is the one the cards describe
    newest = {}
    for s_ in steps:
        newest[s_["job_id"]] = max(newest.get(s_["job_id"], 0), s_["rid"])
    cur_job = max(newest, key=newest.get) if newest else None
    by_agent = {s_["agent_key"]: s_ for s_ in steps if s_["job_id"] == cur_job}
    cur_steps = list(by_agent.values())
    activity = max([max(x.get("started") or 0, x.get("finished") or 0, x.get("heartbeat") or 0)
                    for x in cur_steps] or [0])
    q_recent = any(x["status"] == "running" for x in cur_steps) or (now - activity) < 600

    agents = []
    for a in agents_spec:
        s_ = by_agent.get(a["key"])
        nb = (s_ or {}).get("not_before") or 0
        reason = (s_ or {}).get("reason")
        agents.append({
            "key": a["key"], "name": a["name"], "room": a.get("room"), "stage": a.get("stage"),
            "title": a.get("title"), "goal": a.get("goal"), "duties": a.get("duties", []),
            "job": a["job"], "role": a.get("role", ""), "trigger": a["trigger"], "timeout_sec": a.get("timeout_sec"),
            "max_attempts": a.get("max_attempts"), "outputs": a.get("outputs", []),
            "status": (s_ or {}).get("status", "idle"), "reason": reason,
            "version": (s_ or {}).get("version"),
            "retry_in": round(nb - now) if s_ and s_["status"] == "ready" and nb > now else 0,
            "waiting": bool(reason and str(reason).startswith("waiting:")),
            "started": (s_ or {}).get("started"), "finished": (s_ or {}).get("finished"),
            "depends_on": (s_ or {}).get("depends_on"), "room_status": _room_status(a["room"]),
        })

    # The four OPERATIONS managers have no job steps: show what they are really doing / waiting for.
    def _mins(path):
        try:
            return max(0, int((now - Path(path).stat().st_mtime) // 60))
        except Exception:
            return None
    for a in agents:
        try:
            if a["key"] == "supervisor":
                m = _mins(ROOT / "data" / "supervisor_state.json")
                a["status"] = "done" if (m is not None and m < 15) else "idle"
                a["reason"] = ("last system check %d min ago - next check within 10 min" % m) if m is not None else "waiting for its first check"
            elif a["key"] == "dispatcher":
                import stock
                mins, slot = stock.next_slot_minutes()
                a["status"] = "run" if mins == 0 else "done"
                a["reason"] = ("a post slot (%s) is due now - starting it" % slot) if mins == 0 else \
                              ("next post: %s in %d min" % (slot, mins))
            elif a["key"] == "idle_manager":
                import stock
                ok, why = stock.can_produce("video")
                a["status"] = "run" if ok else "idle"
                a["reason"] = ("machine is free - making a post ahead of time" if ok else "not now: " + str(why)[:110])
            elif a["key"] == "retention":
                m = _mins(ROOT / "data" / "retention_state.json")
                a["status"] = "done" if m is not None else "idle"
                a["reason"] = ("last clean-up check %s ago - runs every hour; deletes only files 7+ days after publishing" % (("%d min" % m) if m < 120 else ("%d h" % (m // 60)))) if m is not None else "waiting for its first hourly check"
        except Exception as e:
            a["reason"] = a.get("reason") or ("state unavailable (%s)" % str(e)[:40])

    source = "queue"
    if q_recent:
        # live detail for NOAH: what the real generator is doing right now
        if live and live["status"] == "running":
            for a in agents:
                if a["key"] == "noah" and a["status"] == "running":
                    a["reason"] = "generating: %s" % (live.get("stage") or "starting")
        status_n = {}
        for x in cur_steps:
            status_n[x["status"]] = status_n.get(x["status"], 0) + 1
    elif live and (live["status"] == "running" or now - (live.get("finished") or live["started"] or 0) < 600):
        # a legacy (non-department) AutoPost run is live: mirror it, derived from runs.stage
        stt, rsn = _derive_from_run(live, live.get("kind") or "video")
        cur_job, source = "run_%s" % live["id"], "pipeline"
        for a in agents:
            if a["key"] in ("supervisor", "dispatcher", "idle_manager", "retention"):
                continue
            a["status"] = stt.get(a["key"], "pending")
            a["reason"] = rsn.get(a["key"])
            a["depends_on"] = DEP.get(a["key"])
            a["retry_in"], a["waiting"] = 0, False
        events = [{"job_id": cur_job, "agent": "PIPELINE", "stage": live.get("stage"), "role": "",
                   "status": live["status"], "version": 0, "ts": live.get("started") or now,
                   "reason": "%s | %s" % ((live.get("topic") or "")[:80], live.get("stage"))}] + events
        status_n = {}
        for v in stt.values():
            status_n[v] = status_n.get(v, 0) + 1
    else:
        # nothing active: the office is quiet (the last job stays in the details tab)
        source = "idle"
        status_n = {}
        for a in agents:
            if a["key"] in ("supervisor", "dispatcher", "idle_manager", "retention"):
                continue            # the operations managers keep their real status line
            a["last_status"] = a["status"]
            a["status"], a["reason"], a["retry_in"], a["waiting"] = "idle", None, 0, False
        data["idle_job"] = cur_job
        cur_job = None

    # side-desk tasks (desk_work.py): who is doing light background work right now
    try:
        with _ro() as c2:
            for d_ in c2.execute("SELECT agent, title, started FROM desk_tasks WHERE status='running' "
                                 "AND started > ?", (now - 1800,)):
                for a in agents:
                    if a["key"] == d_["agent"] and a["status"] not in ("running", "ready", "dead"):
                        a["status"], a["reason"] = "running", "side task: " + (d_["title"] or "")
                        a["desk"] = True
    except Exception:
        pass

    poller = _poller()
    dept = rules.get("department", {}) or {}
    n_dead = sum(1 for x in cur_steps if x["status"] == "dead")
    ok_reports = ("PUBLISHED AND VERIFIED", "DRY RUN COMPLETE (nothing generated or published)")
    checks = [
        {"name": "Department switch (department.enabled)", "ok": bool(dept.get("enabled")),
         "note": "ON: every scheduled and idle-time post goes through the agent team automatically."
                 if dept.get("enabled") else "OFF: the agent team does not start by itself."},
        {"name": "Autopilot not paused (rules.paused)", "ok": not rules.get("paused", False),
         "note": "Paused = nothing posts on its own (manual only)."},
        {"name": "17 agents on the roster (13 post team + 4 operations managers)", "ok": len(agents_spec) == 17,
         "note": "%d found" % len(agents_spec)},
        {"name": "agent rooms exist",
         "ok": sum(1 for a in agents_spec if a.get("room") and (OFFICE / a["room"] / "STATUS.md").exists()) >= len(agents_spec) - 5,
         "note": "ARJUN and the 4 operations managers work in the background (no room)"},
        {"name": "Every agent has a real handler", "ok": _handlers_real(),
         "note": "department.py: every handler is real; merged agents run their absorbed code in order."},
        {"name": "Model failover wired (ollama_fallbacks read by code)",
         "ok": _code_has("department.py", "ollama_fallbacks"),
         "note": "Primary %s -> backup %s (primary unloaded first, never two models at once)."
                 % (rules.get("ollama_model"), rules.get("ollama_fallbacks"))},
        {"name": "Auto-trigger poller running", "ok": poller["alive"],
         "note": ("alive, last beat %ss ago" % poller["age"]) if poller["alive"]
         else "Not running. Start START-POLLER.bat (or turn automation on from Settings)."},
        {"name": "Last department job result",
         "ok": bool(last_report and last_report["status"] in ok_reports),
         "note": ("%s  (%s, %s ago)" % (last_report["status"], last_report["job_id"], _ago(now - last_report["ts"])))
         if last_report else "no job has finished yet"},
        {"name": "Safety rail: publishes only to the allowed platforms",
         "ok": bool(dept.get("allowed_platforms")),
         "note": "allowed: %s. Live publishing is %s."
                 % (", ".join(dept.get("allowed_platforms") or ["(none)"]), "ON" if dept.get("live_publish") else "OFF (local blog only)")},
        {"name": "No dead-lettered step in the current job", "ok": n_dead == 0,
         "note": ("%d dead step(s) - the SUPERVISOR is resolving them" % n_dead) if n_dead else ""},
    ]
    services = [{"name": n, "port": p, "up": _port_up(p)} for n, p in
                (("AutoPost dashboard", 8600), ("Ollama", 11434))]

    data.update({
        "job": cur_job, "source": source, "agents": agents, "stages": STAGES,
        "step_counts": status_n, "events": events[:40], "lease": lease, "tables": counts,
        "checks": checks, "services": services, "poller": poller, "last_report": last_report,
        "model": rules.get("ollama_model"), "fallbacks": rules.get("ollama_fallbacks"),
        "paused": rules.get("paused"), "department_enabled": bool(dept.get("enabled")),
        "gates": ["NOAH cannot start without ISABELLA's approval file",
                  "ELLA cannot publish without the release decision"],
    })
    return data
