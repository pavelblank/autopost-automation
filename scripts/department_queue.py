# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""AutoPost - Social Media Department v3.1
TRIGGER-BASED QUEUE ENGINE + the anti-stuck system.

the owner's two hard requirements (2026-10-10):
  1. The agents must NOT run all the time. An agent wakes on its trigger, does one
     job, files the result and hands off. Idle costs nothing.
  2. The system must NEVER get stuck - not on a slow agent, not on a timeout, not
     on a crash, not on a dead process, not on a rejected step.

HOW IT CANNOT GET STUCK
  * Hard timeout on EVERY step (videogen/AGENT_SPEC.json). A watchdog thread kills the
    step's child processes at the limit; if the step still does not return, it records
    the failure and ends the engine process (the supervisor restarts it).
  * A heartbeat thread stamps a running step every 20s. A step with no heartbeat for 3
    intervals is declared dead by ANY engine process and its owner process is killed
    (only after confirming that pid really is a department engine - no pid-reuse kills).
  * Finite retry budget everywhere, with spec backoff (30s/120s/600s) via `not_before`.
    No sleeping, no busy loop: a retry just becomes READY later.
  * Budget spent -> the step returns work to the EARLIEST responsible specialist. That
    specialist's own budget is spent every round, so the whole loop is finite. When that
    is spent too -> DEAD-LETTER: waits for a human, never retries itself again.
  * A step only ever waits on a step that is DONE or SKIPPED.
  * When a job dies mid-chain, HELENA is armed so the failure is reported, not silent.
  * The single-row gen_lease is taken around the real generation and auto-stolen if dead.
  * On startup the engine reaps anything left 'running' by a previous crash.
  * One step per tick. If nothing is ready it returns immediately: zero CPU/GPU.

  python scripts/department_queue.py --enqueue "topic" --kind text [--dry-run]
  python scripts/department_queue.py --run            # work until idle, then exit
  python scripts/department_queue.py --watch          # poller: sleep, wake on triggers
  python scripts/department_queue.py --status | --doctor
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

import db        # noqa: E402
import paths     # noqa: E402

# ---------------------------------------------------------------- the chain
# step_key, agent_key, stage, depends_on
STEPS = [
    # ARJUN (research) is NOT a chain step: he runs on his own clock and keeps the ready-topic
    # list full; NADIA picks from it. The job chain starts when NADIA opens the job.
    ("intake",   "intake",   1, None),        # NADIA - Production Scheduling Executive
    ("maya",     "maya",     2, "intake"),
    ("sophie",   "sophie",   2, "maya"),
    ("daniel",   "daniel",   2, "sophie"),    # + AISHA's characters/backgrounds
    ("leo",      "leo",      2, "daniel"),
    ("oliver",   "oliver",   3, "leo"),
    ("isabella", "isabella", 3, "oliver"),
    ("noah",     "noah",     4, "isabella"),
    ("grace",    "grace",    4, "noah"),      # + ETHAN's content audit
    ("ella",     "ella",     5, "grace"),     # + CHLOE's packages + MARCUS's release gate
    ("victoria", "victoria", 6, "ella"),
    ("helena",   "helena",   7, "victoria"),  # + JAMES's fix-up when something failed
]
CHAIN = [s[0] for s in STEPS]
STEP_BY_KEY = {s[0]: s for s in STEPS}
VISUAL_ONLY = {"daniel"}

# who fixes it when an agent rejects -> the EARLIEST responsible specialist
FIRST_RESPONSIBLE = {
    "leo": "maya", "oliver": "maya", "isabella": "maya",
    "grace": "sophie", "ethan": "sophie", "marcus": "chloe",
    "ella": "sophie",                       # the text variants / packages come from the writer
    "victoria": "ella", "james": "ella", "helena": "ella",
}

HEARTBEAT_SEC = 20          # a running step writes this often
HEARTBEAT_DEAD = 3          # 3 missed heartbeats (~60s) = declared dead
WATCHDOG_GRACE = 30         # seconds after the timeout before the engine ends itself
BACKOFF_SCALE = 1.0         # tests set 0 to skip the waits


from dept_errors import Fatal, Reject, Wait   # noqa: E402  (shared so __main__ and imports agree)


def log(m):
    try:
        print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode("ascii"), flush=True)


# ---------------------------------------------------------------- processes
def _pid_alive(pid):
    return nowindow.pid_alive(pid)


def _is_engine(pid):
    """True only if pid is really a department engine process (guards against pid reuse)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine" % int(pid)],
            capture_output=True, text=True, timeout=30)
        return "department_queue" in (r.stdout or "")
    except Exception:
        return False


def kill_tree(pid):
    """Kill a process AND every child. An orphan must never hold the GPU or a lock."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def kill_owner(pid):
    """Kill a stale step owner - but never ourselves and never an unrelated process."""
    if not pid or int(pid) == os.getpid():
        return False
    if not _pid_alive(pid):
        return False
    if not _is_engine(pid):
        log("  owner pid %s is alive but is not a department engine - NOT killing it" % pid)
        return False
    kill_tree(pid)
    return True


# ---------------------------------------------------------------- the contract
SPEC_PATH = ROOT / "videogen" / "AGENT_SPEC.json"


def load_spec():
    try:
        return json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


SPEC = load_spec()
DEFAULTS = SPEC.get("defaults", {}) or {}
AGENTS = {a["key"]: a for a in SPEC.get("agents", [])}


def spec_for(key):
    a = AGENTS.get(key, {})
    d = DEFAULTS
    return {
        "timeout": int(a.get("timeout_sec", d.get("timeout_sec", 900))),
        "attempts": int(a.get("max_attempts", d.get("max_attempts", 3))),
        "name": a.get("name", key.upper()),
        "role": a.get("role", ""),
        "errors": a.get("errors", {}) or {},
    }


def backoff_for(attempt_no):
    lst = DEFAULTS.get("backoff") or [30, 120, 600]
    return lst[min(max(attempt_no, 1) - 1, len(lst) - 1)] * BACKOFF_SCALE


# ---------------------------------------------------------------- schema
def schema():
    c = db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS department_steps (
        job_id     TEXT NOT NULL,
        step_key   TEXT NOT NULL,
        agent_key  TEXT NOT NULL,
        stage      INTEGER NOT NULL,
        depends_on TEXT,
        status     TEXT NOT NULL DEFAULT 'pending',
        version    INTEGER DEFAULT 0,
        reason     TEXT,
        started    REAL,
        heartbeat  REAL,
        finished   REAL,
        owner_pid  INTEGER,
        not_before REAL,
        PRIMARY KEY(job_id, step_key))""")
    # migrate tables created by an OLDER schema (db.py created it without these columns)
    cols = [r[1] for r in c.execute("PRAGMA table_info(department_steps)")]
    for col, typ in (("heartbeat", "REAL"), ("owner_pid", "INTEGER"), ("not_before", "REAL")):
        if col not in cols:
            c.execute("ALTER TABLE department_steps ADD COLUMN %s %s" % (col, typ))
    c.execute("CREATE INDEX IF NOT EXISTS idx_steps_status ON department_steps(status)")
    return c


def _row(job_id, key):
    return schema().execute("SELECT * FROM department_steps WHERE job_id=? AND step_key=?",
                            (job_id, key)).fetchone()


def _rows(job_id):
    return schema().execute("SELECT * FROM department_steps WHERE job_id=?", (job_id,)).fetchall()


def _set(job_id, key, status, reason="", owner=None, not_before=None):
    c = schema()
    now = time.time()
    if status == "running":
        c.execute("UPDATE department_steps SET status='running', reason=?, started=?, heartbeat=?,"
                  " owner_pid=?, finished=NULL WHERE job_id=? AND step_key=?",
                  (reason, now, now, owner or os.getpid(), job_id, key))
    elif status in ("ready", "pending"):
        c.execute("UPDATE department_steps SET status=?, reason=?, not_before=?, owner_pid=NULL"
                  " WHERE job_id=? AND step_key=?", (status, reason, not_before, job_id, key))
    else:
        c.execute("UPDATE department_steps SET status=?, reason=?, finished=?, owner_pid=NULL"
                  " WHERE job_id=? AND step_key=?", (status, reason, now, job_id, key))


def claim(job_id, key, reason="working"):
    """ATOMIC claim of a READY step: exactly one engine can win it. Two engines (a poller and a
    manual --run) can therefore never run the same step twice."""
    now = time.time()
    cur = schema().execute(
        "UPDATE department_steps SET status='running', reason=?, started=?, heartbeat=?, owner_pid=?, finished=NULL "
        "WHERE job_id=? AND step_key=? AND status='ready' AND (not_before IS NULL OR not_before<=?)",
        (reason, now, now, os.getpid(), job_id, key, now))
    return cur.rowcount == 1


def _beat(job_id, key):
    schema().execute("UPDATE department_steps SET heartbeat=? WHERE job_id=? AND step_key=? "
                     "AND status='running'", (time.time(), job_id, key))


def audit(job_id, agent_key, status, reason=""):
    s = spec_for(agent_key)
    # SUPERVISOR / RETENTION / DISPATCHER are sidecars, not chain steps - they have no
    # entry in STEP_BY_KEY, so a chain lookup here used to raise KeyError and swallow the
    # audit trail exactly when something had gone wrong.
    step = STEP_BY_KEY.get(agent_key)
    stage = step[2] if step else 0
    schema().execute(
        "INSERT INTO department_jobs(job_id,agent,stage,role,status,reason,version,ts)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (job_id, s["name"], stage,
         "%s - %s" % (s["name"], s["role"]), status, (reason or "")[:400], 0, time.time()))
    if step is not None:
        _tg_progress(job_id, s, stage, status, reason)


_TG_STATUS = {"done": "DONE", "skipped": "SKIPPED", "dead": "STOPPED", "failed": "FAILED",
              "retry": "RETRY"}


def _tg_progress(job_id, s, stage, status, reason):
    """One short Telegram line per agent result, so the owner can follow the department live.
    Best effort in a background thread - a Telegram outage must never slow or break a job."""
    label = _TG_STATUS.get(status)
    if not label:
        return
    try:                                 # OFF by default (2026-10-11): ~11 technical lines per post were "much info, no meaning".
        import json as _j                # HELENA sends ONE clear report per post; the Watchdog sends the problems.
        _r = _j.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig"))
        if not (_r.get("department") or {}).get("step_alerts", False):
            return
    except Exception:
        return
    try:
        m_ = read_job(job_id) or {}
        if m_.get("dry_run") or (m_.get("stock") and not m_.get("stock_released")):
            return                      # a post being pre-made for stock stays quiet until it is released
    except Exception:
        pass

    def _send():
        try:
            import publish as pub
            tg = (pub.load_conn() or {}).get("telegram", {})
            if not (tg.get("token") and tg.get("chat_id")):
                return
            who = "%s (stage %d, %s)" % (s["name"], stage, s["role"])
            txt = "\n".join(["[%s] %s %s" % (time.strftime("%H:%M"), label, who),
                             (reason or "")[:300], "job " + job_id])
            pub.telegram_send(tg["token"], tg["chat_id"], txt)
        except Exception:
            pass
    import threading
    threading.Thread(target=_send, daemon=True).start()


# ---------------------------------------------------------------- job metadata
def job_meta_path(job_id):
    return paths.job_dir(job_id, "intake") / "job.json"


def read_job(job_id):
    try:
        return json.loads(job_meta_path(job_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_job(job_id, meta):
    job_meta_path(job_id).write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                     encoding="utf-8")


def _kind_of(job_id):
    return read_job(job_id).get("kind", "video")


# ---------------------------------------------------------------- triggers
def trigger_next(job_id, done_key):
    """THE TRIGGER. Exactly one successor becomes READY - never two.

    Guarded by the successor's own dependency, so a chain can never fork into
    two runnable steps no matter what order triggers arrive in.
    """
    c = schema()
    if done_key == "grace":
        try:
            import stock
            if stock.should_hold(job_id):
                stock.register(job_id)          # made + quality-checked: wait in STOCK for its scheduled slot
                log("  STOCK: %s is finished and waits for its slot (ELLA not triggered)" % job_id)
                return None
        except Exception as e:
            log("  stock hold failed (%s) - publishing normally" % str(e)[:80])
    row = c.execute("SELECT * FROM department_steps WHERE job_id=? AND depends_on=?",
                    (job_id, done_key)).fetchone()
    if not row or row["status"] in ("done", "skipped", "dead", "running"):
        return None
    dep = _row(job_id, done_key)
    if not dep or dep["status"] not in ("done", "skipped"):
        return None                      # the predecessor is not finished: do not fire
    c.execute("UPDATE department_steps SET status='ready', not_before=NULL "
              "WHERE job_id=? AND step_key=? AND status IN ('pending','ready')",
              (job_id, row["step_key"]))
    return row["step_key"]


def close_dead_job(job_id):
    """A job that can make no more progress is REPORTED, never silent: arm HELENA."""
    rows = {r["step_key"]: r for r in _rows(job_id)}
    if any(r["status"] in ("ready", "running") for r in rows.values()):
        return False
    if not any(r["status"] == "dead" for r in rows.values()):
        return False
    h = rows.get("helena")
    if not h or h["status"] in ("done", "skipped", "dead", "running", "ready"):
        return False
    _set(job_id, "helena", "ready", "final report: job stopped - the SUPERVISOR retries or closes it by itself")
    log("  job %s is stopped (dead-lettered step) - HELENA armed to report it" % job_id)
    return True


def sweep_dead_jobs():
    """Self-healing: any job that has a dead-lettered step and nothing left to run gets its
    final report armed. Runs whenever the engine is idle, so no stopped job is ever silent."""
    n = 0
    rows = schema().execute("SELECT DISTINCT job_id FROM department_steps WHERE status='dead'").fetchall()
    for r in rows:
        if close_dead_job(r["job_id"]):
            n += 1
    return n


def route_back(job_id, key, reason, fatal=False, reroute=True, owner=None):
    """A failed/rejected step: retry within budget, else return the work to the EARLIEST
    responsible specialist, else dead-letter. Every path is finite."""
    c = schema()
    c.execute("UPDATE department_steps SET version=version+1 WHERE job_id=? AND step_key=?",
              (job_id, key))
    used = _row(job_id, key)["version"]
    budget = spec_for(key)["attempts"]
    reason = (reason or "unspecified failure")[:200]

    if fatal:
        _set(job_id, key, "dead", "rejected, no retry possible: " + reason)
        audit(job_id, key, "dead", "fatal rejection: " + reason)
        log("  DEAD-LETTERED (deterministic rejection): %s" % reason)
        close_dead_job(job_id)
        return "dead"

    if used < budget:
        wait = backoff_for(used)
        _set(job_id, key, "ready", "attempt %d/%d failed - %s (retry in %ds)"
             % (used, budget, reason[:140], wait), not_before=time.time() + wait)
        audit(job_id, key, "failed", reason)
        log("  retry %d/%d - will be READY again in %ds" % (used, budget, wait))
        return "retry"

    # Only a REJECTION (the work itself is wrong) goes back to a creative owner. A technical failure
    # (network, timeout, crash) is never answered by rewriting the script: it is retried, then
    # dead-lettered with ERROR + ACTION + RETRY.
    back = (owner or FIRST_RESPONSIBLE.get(key)) if reroute else None
    if back not in CHAIN:
        back = None
    if not back or back == key:
        _set(job_id, key, "dead", reason)
        audit(job_id, key, "dead", "retry budget spent, no earlier owner: " + reason)
        log("  DEAD-LETTERED - no earlier specialist can fix it; the SUPERVISOR retries or closes it by itself")
        close_dead_job(job_id)
        return "dead"

    brow = _row(job_id, back)
    if brow["version"] >= spec_for(back)["attempts"]:
        _set(job_id, key, "dead", "%s correction budget spent: %s" % (back.upper(), reason))
        audit(job_id, key, "dead", "%s already used its attempts: %s" % (back.upper(), reason))
        log("  DEAD-LETTERED - %s has no correction attempts left" % back.upper())
        close_dead_job(job_id)
        return "dead"

    # correction round: the failing step waits (fresh attempts), the specialist re-does
    # the work (its attempt counter is spent), everything after the specialist re-arms.
    c.execute("UPDATE department_steps SET version=0 WHERE job_id=? AND step_key=?",
              (job_id, key))
    idx = CHAIN.index(back)
    for later in CHAIN[idx + 1:]:
        c.execute("UPDATE department_steps SET status='pending', not_before=NULL, owner_pid=NULL "
                  "WHERE job_id=? AND step_key=? AND status IN ('ready','running','done','skipped','failed')",
                  (job_id, later))
    c.execute("UPDATE department_steps SET status='ready', version=version+1, not_before=NULL, "
              "reason=?, owner_pid=NULL WHERE job_id=? AND step_key=?",
              ("correction requested by %s: %s" % (key.upper(), reason[:120]), job_id, back))
    audit(job_id, key, "rejected", "returned to %s: %s" % (back.upper(), reason))
    log("  ROUTED BACK to %s - %d later step(s) re-armed (chain keeps moving)"
        % (back.upper(), len(CHAIN) - idx - 1))
    return "routed"


def finish(job_id, key, status, reason=""):
    _set(job_id, key, status, reason)
    audit(job_id, key, status, reason)
    nxt = trigger_next(job_id, key)
    if nxt:
        log("  TRIGGER -> %s is READY" % nxt)
    else:
        log("  chain complete - nothing further triggered")


# ---------------------------------------------------------------- the reaper
def reap(job_id=None):
    """Kill anything hung, stale or orphaned. This is why the department cannot hang."""
    c = schema()
    q = "SELECT * FROM department_steps WHERE status='running'"
    args = ()
    if job_id:
        q += " AND job_id=?"
        args = (job_id,)
    killed = 0
    for r in c.execute(q, args).fetchall():
        s = spec_for(r["step_key"])
        age = time.time() - (r["started"] or time.time())
        beat = time.time() - (r["heartbeat"] or r["started"] or time.time())
        why = None
        if age > s["timeout"] + WATCHDOG_GRACE:
            why = "hard timeout %ds exceeded (ran %ds)" % (s["timeout"], int(age))
        elif beat > HEARTBEAT_SEC * HEARTBEAT_DEAD:
            why = "no heartbeat for %ds - declared dead" % int(beat)
        if not why:
            continue
        log("  REAP %s: %s" % (r["step_key"], why))
        kill_owner(r["owner_pid"])
        route_back(r["job_id"], r["step_key"], why)
        killed += 1
    return killed


def recover_orphans():
    """Startup: nothing may stay 'running' from a previous crash (a dead owner)."""
    c = schema()
    n = 0
    for r in c.execute("SELECT * FROM department_steps WHERE status='running'").fetchall():
        if r["owner_pid"] and int(r["owner_pid"]) != os.getpid() and _pid_alive(r["owner_pid"]) \
                and (time.time() - (r["heartbeat"] or 0)) < HEARTBEAT_SEC * HEARTBEAT_DEAD:
            continue                      # a live engine is still working on it
        log("  startup recovery: %s was left running - owner pid %s is gone"
            % (r["step_key"], r["owner_pid"]))
        kill_owner(r["owner_pid"])
        route_back(r["job_id"], r["step_key"], "interrupted by a restart")
        n += 1
    try:
        db.clear_stale_runs()
    except Exception:
        pass
    return n


# ---------------------------------------------------------------- the loop
def next_ready(job_id=None):
    c = schema()
    now = time.time()
    q = ("SELECT * FROM department_steps WHERE status='ready' "
         "AND (not_before IS NULL OR not_before<=?)")
    args = [now]
    if job_id:
        q += " AND job_id=?"
        args.append(job_id)
    return c.execute(q + " ORDER BY rowid LIMIT 1", args).fetchone()


_DEPT_MTIME = {"t": 0.0}


def _department():
    """Import the agent module, hot-reloading it when its file changed on disk. Only ever called
    between steps (never while a handler runs), so a fix reaches a long-running poller without
    a restart."""
    import importlib
    import department as D
    try:
        mt = (ROOT / "scripts" / "department.py").stat().st_mtime
        if _DEPT_MTIME["t"] and mt != _DEPT_MTIME["t"]:
            D = importlib.reload(D)
            log("  (department.py changed on disk - agent code reloaded)")
        _DEPT_MTIME["t"] = mt
    except Exception:
        pass
    return D


def _run_handler(jid, key, spec):
    """Run one agent's handler under a heartbeat thread and a hard-timeout watchdog."""
    D = _department()
    stop = threading.Event()
    state = {"timed_out": False}

    def beat():
        while not stop.wait(HEARTBEAT_SEC):
            try:
                _beat(jid, key)
            except Exception:
                pass

    def watchdog():
        if stop.wait(spec["timeout"]):
            return
        state["timed_out"] = True
        log("  WATCHDOG: %s exceeded %ds - killing its child processes" % (key, spec["timeout"]))
        D.kill_children()
        if stop.wait(WATCHDOG_GRACE):
            return
        log("  WATCHDOG: %s still hung - recording failure and ending this engine process" % key)
        try:
            route_back(jid, key, "hard timeout %ds - handler never returned" % spec["timeout"], reroute=False)
        finally:
            os._exit(3)

    threading.Thread(target=beat, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        fn = getattr(D, "run_%s" % key, None)
        if fn is None:
            raise Fatal("no handler registered for %s" % key)
        out = fn(jid)
        if state["timed_out"]:
            raise TimeoutError("hard timeout %ds exceeded" % spec["timeout"])
        return out
    finally:
        stop.set()


def tick(job_id=None):
    """Run AT MOST ONE ready step. Returns its key, or None when idle.

    Idle returns instantly - that is the low-pressure behaviour.
    """
    reap(job_id)
    row = next_ready(job_id)
    if row is None:
        if sweep_dead_jobs():
            row = next_ready(job_id)
        if row is None:
            return None
    key, jid = row["step_key"], row["job_id"]
    s = spec_for(key)
    if not claim(jid, key):
        log("  %s was claimed by another engine - skipping" % key)
        return key
    log("RUN  %-9s %-11s stage %d  try %d/%d  (limit %ds)"
        % (key, s["name"][:11], row["stage"], row["version"] + 1, s["attempts"], s["timeout"]))
    audit(jid, key, "running", "started")

    if key in VISUAL_ONLY and _kind_of(jid) == "text":
        finish(jid, key, "skipped", "text-only post: visual stage not applicable")
        return key

    try:
        out = _run_handler(jid, key, s)
    except Fatal as e:
        log("  FATAL %s: %s" % (key, str(e)[:170]))
        route_back(jid, key, str(e), fatal=True)
        return key
    except Wait as w:
        _set(jid, key, "ready", "waiting: %s" % w.reason[:160], not_before=time.time() + w.seconds)
        log("  WAIT %s: %s (re-checks in %ds, no attempt spent)" % (key, w.reason[:100], w.seconds))
        return key
    except Exception as e:
        log("  ERROR %s: %s: %s" % (key, type(e).__name__, str(e)[:170]))
        route_back(jid, key, "%s: %s" % (type(e).__name__, e), reroute=isinstance(e, Reject), owner=getattr(e, "owner", None))
        return key

    st = _row(jid, key)
    if st and st["status"] == "running":
        if isinstance(out, tuple) and out and out[0] == "skipped":
            finish(jid, key, "skipped", out[1] if len(out) > 1 else "not applicable")
        else:
            finish(jid, key, "done", (out if isinstance(out, str) and out else "completed"))
    return key


def run(job_id=None, max_steps=500):
    n = 0
    while n < max_steps:
        if tick(job_id) is None:
            # something may be waiting on a backoff - wait for it rather than quit early
            c = schema()
            q = "SELECT MIN(not_before) FROM department_steps WHERE status='ready' AND not_before>?"
            nb = c.execute(q, (time.time(),)).fetchone()[0]
            if nb and nb - time.time() < 700:
                time.sleep(min(5, max(0.2, nb - time.time())))
                continue
            break
        n += 1
        time.sleep(0.15)
    log("run finished after %d step(s); idle from here (no polling, no CPU)" % n)
    return n


WATCH_FILE = ROOT / "data" / "department_watch.json"


def _watch_alive():
    try:
        w = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
        # The poller runs ONE step at a time and does not beat while a long step (a 45-minute
        # video render) is executing. A live pid with a stale beat is "busy", not "dead" -
        # calling it dead made the guard keep launching extra pollers and the dashboard
        # report "stopped". The reaper still handles genuinely hung steps.
        return w.get("pid") and w["pid"] != os.getpid() and _pid_alive(w["pid"]) \
            and time.time() - w.get("ts", 0) < 5400
    except Exception:
        return False


SIDECARS = ("supervisor", "retention", "dispatcher")
_SIDECAR_EVERY = 60          # the supervisor's own heartbeat; the poller may be on 3s


def run_sidecars():
    """Run the sidecar agents (19 SUPERVISOR, 20 RETENTION, 21 DISPATCHER).

    They are NOT chain steps and cannot be triggered by a predecessor, so the poller
    drives them. Every one of them is allowed to fail: a broken sidecar must never be
    able to stop the department from running, so each is called in isolation and its
    error is logged, never raised. Each has its own cadence - a 3 s poller must not ask
    the model anything twenty times a minute."""
    now = time.time()
    every_mon = _monitor_every()
    if now - _SIDECAR_STATE.setdefault("auto_heal", 0.0) >= every_mon:
        _SIDECAR_STATE["auto_heal"] = now
        try:
            auto_heal()
        except Exception as e:
            log("auto-heal failed (ignored): %s" % str(e)[:140])
    if now - _SIDECAR_STATE.setdefault("stock", 0.0) >= every_mon:
        _SIDECAR_STATE["stock"] = now
        try:
            import stock
            jid = stock.tick()
            if jid:
                log("STOCK: producing a post for later (%s)" % jid)
        except Exception as e:
            log("stock tick failed (ignored): %s" % str(e)[:140])
    for mod_name, every in (("supervisor", every_mon), ("retention", 3600), ("dispatcher", 300)):
        st = _SIDECAR_STATE.setdefault(mod_name, 0.0)
        if now - st < every:
            continue
        _SIDECAR_STATE[mod_name] = now
        try:
            mod = __import__(mod_name)
            r = mod.run_once(quiet=True) if mod_name == "supervisor" else mod.run_once()
            if isinstance(r, dict) and r.get("found"):
                log("SIDECAR %s: %d stuck, %d action(s)"
                    % (mod_name.upper(), r["found"], r.get("acted", 0)))
            elif isinstance(r, dict) and r.get("enqueued"):
                log("SIDECAR DISPATCHER: enqueued %s"
                    % ", ".join(e["slot"] for e in r["enqueued"]))
        except Exception as e:
            log("sidecar %s failed (ignored): %s" % (mod_name, str(e)[:140]))


_SIDECAR_STATE = {}


def _monitor_every():
    """How often the SUPERVISOR's checks run (stuck-job scan, auto-heal, the idle-time stock producer).
    the owner: every 10-15 minutes is enough - not every second. rules.json -> supervisor.interval_min (default 10)."""
    try:
        r = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig"))
        return max(60, int(float((r.get("supervisor") or {}).get("interval_min", 10)) * 60))
    except Exception:
        return 600


def _side_desk_loop():
    """Everyone who is NOT running a chain step: the 3 sidecars (supervisor, retention,
    dispatcher) and the light side-desk tasks (desk_work.py). Own thread, so they keep working
    while NOAH holds the main loop for a long render."""
    while True:
        try:
            run_sidecars()
            import desk_work
            desk_work.run_due()
        except Exception as e:
            log("side desk error (ignored): %s" % str(e)[:140])
        time.sleep(10)


def watch(interval=3.0):
    """The poller. Sleeps; wakes every `interval` s; reaps hung steps and runs ONE ready
    step. Idle cost: one tiny DB read per wake. Only one poller may run."""
    if _watch_alive():
        log("another poller is already running - exiting")
        return 2
    schema()
    recover_orphans()
    log("WATCH started (pid %d, every %.0fs). Sidecars online: %s"
        % (os.getpid(), interval, ", ".join(s.upper() for s in SIDECARS)))
    threading.Thread(target=_side_desk_loop, name="side-desk", daemon=True).start()
    while True:
        try:
            WATCH_FILE.write_text(json.dumps({"pid": os.getpid(), "ts": time.time(),
                                              "interval": interval}), encoding="utf-8")
            if tick() is None:
                time.sleep(interval)
        except KeyboardInterrupt:
            log("WATCH stopped")
            try:
                WATCH_FILE.unlink()
            except Exception:
                pass
            return 0
        except Exception as e:
            log("WATCH loop error (continuing): %s" % str(e)[:160])
            time.sleep(interval)


# ---------------------------------------------------------------- status
def show(job_id=None):
    c = schema()
    if not job_id:
        r = c.execute("SELECT job_id FROM department_steps GROUP BY job_id "
                      "ORDER BY MAX(rowid) DESC LIMIT 1").fetchone()
        if not r:
            log("no department jobs yet")
            return
        job_id = r["job_id"]
    rows = {r["step_key"]: r for r in _rows(job_id)}
    log("JOB %s   %d steps" % (job_id, len(rows)))
    for key in CHAIN:
        r = rows.get(key)
        if not r:
            continue
        s = spec_for(key)
        mark = {"ready": ">", "running": "*", "done": "OK", "skipped": "--",
                "failed": "!!", "dead": "DEAD", "pending": "."}.get(r["status"], "?")
        log("  %-4s %d  %-9s %-11s try %d/%-2d %s"
            % (mark, r["stage"], r["step_key"], s["name"][:11],
               r["version"], s["attempts"], (r["reason"] or "")[:50]))
    done = len([r for r in rows.values() if r["status"] in ("done", "skipped")])
    dead = len([r for r in rows.values() if r["status"] == "dead"])
    log("  %d/%d complete, %d dead-lettered" % (done, len(rows), dead))
    return job_id


def _norm_topic(t):
    import re
    return " ".join(re.findall(r"[a-z0-9]+", (t or "").lower()))


def vet_topic(topic, kind="video"):
    """ARJUN's gate - runs BEFORE any job exists. Is the topic good, and is it new?
    Returns (ok, reason). Checks: usable text, not a near-duplicate of anything already used
    (research.check_fresh), not a repeat of anything ever published (post_history), and not the
    same topic as a job that is already queued or was made in the last 48 h."""
    t = (topic or "").strip()
    words = [w for w in _norm_topic(t).split() if len(w) >= 3]
    if len(t) < 8 or len(t) > 240 or len(words) < 2:
        return False, "not a usable topic (needs a real sentence of at least two meaningful words)"
    try:
        # ARJUN's editorial gate (cheap rules, no model): video-channel titles, emoji clickbait, 'viral/best of/prices'...
        # Checked HERE, at the very first stage. Before 2026-10-11 these only failed at the 3.0 audit - after the
        # GPU had spent hours rendering them (3 junk stock videos overnight).
        import topic_editor
        junk = topic_editor.quick_reject(t)
        if junk:
            return False, "not a subject for this page: " + str(junk)
    except Exception:
        pass
    try:
        import fact_grounding
        okj, whyj = fact_grounding.is_subject(t)
        if not okj:
            return False, "not a usable subject: " + whyj
        if kind == "video":
            real, who = fact_grounding.person_in_topic(t)
            if real:
                return False, ("it names a real person (%s): a photoreal video with invented dialogue about a real person "
                               "could misrepresent them - use a general subject, or approve this one by hand" % who)
    except Exception:
        pass
    try:
        import research
        fresh, similar = research.check_fresh(t)
        if not fresh:
            return False, 'near-duplicate of an already used topic: "%s"' % str(similar)[:90]
    except Exception:
        pass
    try:
        import post_history
        rep = post_history.find_repeat(t)
        if rep:
            return False, "repeat of something already published: %s" % str(rep)[:90]
    except Exception:
        pass
    want = _norm_topic(t)
    horizon = time.time() - 48 * 3600
    for d in Path(paths.AREAS["intake"]).glob("job_*"):
        try:
            m = json.loads((d / "job.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if m.get("dry_run") or m.get("created", 0) < horizon:
            continue
        if _norm_topic(m.get("topic")) == want:
            return False, "the same topic is already a job (%s) - retry that job instead of making a duplicate" % m.get("job_id")
    return True, "new and good"


def enqueue(topic, kind="video", platforms=None, dry_run=False, notify=False, source="manual"):
    import hashlib
    if not dry_run and source != "retry":
        ok, why = vet_topic(topic, kind)
        if not ok:
            log("ARJUN REFUSED the topic before any work started: %s  (%s)" % (str(topic)[:70], why))
            try:
                with db.conn() as c:
                    c.execute("CREATE TABLE IF NOT EXISTS desk_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, "
                              "task TEXT NOT NULL, title TEXT, status TEXT NOT NULL, started REAL, finished REAL, reason TEXT)")
                    c.execute("INSERT INTO desk_tasks(agent,task,title,status,started,finished,reason) VALUES(?,?,?,?,?,?,?)",
                              ("arjun", "vet_topic", "vetting a topic", "failed", time.time(), time.time(),
                               "refused: %s | %s" % (str(topic)[:80], why)))
            except Exception:
                pass
            return None
    job_id = "job_%s_%s" % (time.strftime("%Y%m%d"),
                            hashlib.md5(("%s|%s|%s" % (topic, kind, time.time())
                                         ).encode()).hexdigest()[:6])
    platforms = list(platforms or ["socialai"])
    c = schema()
    write_job(job_id, {"job_id": job_id, "topic": topic, "kind": kind, "platforms": platforms,
                       "dry_run": bool(dry_run), "notify": bool(notify), "source": source,
                       "run_id": None, "created": time.time()})
    for key, agent_key, stage, dep in STEPS:
        c.execute("INSERT OR IGNORE INTO department_steps"
                  "(job_id,step_key,agent_key,stage,depends_on,status) VALUES(?,?,?,?,?,?)",
                  (job_id, key, agent_key, stage, dep,
                   "ready" if dep is None else "pending"))
    if not dry_run:
        # the first thing in the job's story: the research agent vetted the topic, then hands it on
        c.execute("INSERT INTO department_jobs(job_id,agent,stage,role,status,reason,version,ts)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (job_id, "ARJUN", 2, "ARJUN - Research", "done",
                   "I checked the history: the topic is new and good. Handing it to INTAKE.", 0, time.time() - 0.01))
    c.execute("INSERT INTO department_jobs(job_id,agent,stage,role,status,reason,version,ts)"
              " VALUES(?,?,?,?,?,?,?,?)",
              (job_id, "INTAKE", 1, "enqueued", "enqueued",
               "%s | %s | %s%s" % (topic, kind, ",".join(platforms), " | dry-run" if dry_run else ""),
               0, time.time()))
    log("ENQUEUED %s  kind=%s  -> %s%s" % (job_id, kind, ",".join(platforms),
                                          "  (dry run)" if dry_run else ""))
    log("  only step 1 is READY. Nothing runs until the engine ticks.")
    return job_id


def retry_job(job_id, step=None, who="owner"):
    """Re-open a stopped job. Same job id, so nothing is duplicated; finished steps stay done; for
    video every clip already rendered with the same prompt is reused (a retry costs seconds).
    Returns (ok, message)."""
    rows = {r["step_key"]: r for r in _rows(job_id)}
    if not rows:
        return False, "no such job: %s" % job_id
    dead = [k for k, r in rows.items() if r["status"] == "dead" and (step is None or k == step)]
    if not dead:
        return False, "job %s has no stopped step%s - nothing to retry" % (job_id, (" named " + step) if step else "")
    for k in dead:
        _set(job_id, k, "ready", "retry (%s): re-run of the same job" % who)
        schema().execute("UPDATE department_steps SET version=0, not_before=NULL, started=NULL, heartbeat=NULL, "
                         "finished=NULL WHERE job_id=? AND step_key=?", (job_id, k))
        audit(job_id, k, "ready", "re-opened (%s) - same job, finished work is reused" % who)
    h = rows.get("helena")
    if h and h["status"] != "pending":
        _set(job_id, "helena", "pending", "")
    return True, "re-opened %s on %s" % (", ".join(dead), job_id)


_HEAL_FILE = ROOT / "data" / "auto_heal.json"
_HEAL_MAX = 2            # automatic retries per stopped step - then the SUPERVISOR closes the job (no human)
_HEAL_GAP = 600          # seconds between automatic retries of the same job


def auto_heal():
    """SELF-HEALING, finite: a stopped step whose failure a plain retry can fix is re-opened
    automatically (max 2 times, 10 min apart, never while another job step is running). After
    that - or for failures a retry cannot fix - the owner gets ERROR + ACTION + RETRY from HELENA."""
    import dept_actions
    try:
        st = json.loads(_HEAL_FILE.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    c = schema()
    if c.execute("SELECT COUNT(*) FROM department_steps WHERE status IN ('running','ready') "
                 "AND job_id NOT IN (SELECT job_id FROM department_steps WHERE status='dead')").fetchone()[0]:
        return 0
    n = 0
    for r in c.execute("SELECT job_id, step_key, reason FROM department_steps WHERE status='dead'").fetchall():
        key = "%s/%s" % (r["job_id"], r["step_key"])
        d = dept_actions.diagnose(r["step_key"], r["reason"])
        rec = st.get(key, {"n": 0, "ts": 0})
        if not d["retry_ok"] or rec["n"] >= _HEAL_MAX or time.time() - rec["ts"] < _HEAL_GAP:
            continue
        ok, msg = retry_job(r["job_id"], r["step_key"], who="auto-heal %d/%d" % (rec["n"] + 1, _HEAL_MAX))
        if ok:
            st[key] = {"n": rec["n"] + 1, "ts": time.time()}
            n += 1
            log("AUTO-HEAL: %s (%s)" % (msg, d["error"]))
    try:
        _HEAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HEAL_FILE.write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass
    return n


def doctor():
    log("== DEPARTMENT DOCTOR ==")
    n = recover_orphans()
    log("startup recovery: %d orphaned step(s)" % n)
    k = reap()
    log("reaped %d hung step(s)" % k)
    try:
        log("generation_busy: %s" % db.generation_busy())
        o = db.gen_lease_owner()
        log("lease owner: %s" % (str(o) if o else "free"))   # o is a tuple: never % it directly
    except Exception as e:
        log("lease check failed: %s" % str(e)[:80])
    c = schema()
    for st in ("ready", "running", "dead"):
        log("%-9s steps: %d" % (st, c.execute(
            "SELECT COUNT(*) FROM department_steps WHERE status=?", (st,)).fetchone()[0]))
    log("spec agents loaded  : %d" % len(AGENTS))
    log("poller alive        : %s" % bool(_watch_alive()))
    log("== healthy ==")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--enqueue")
    ap.add_argument("--kind", choices=("video", "image", "text"), default="video")
    ap.add_argument("--platforms", default="socialai")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--notify", action="store_true", help="allow HELENA to send the Telegram report")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--tick", action="store_true", help="run at most ONE step")
    ap.add_argument("--retry", help="re-open a stopped job: --retry JOB_ID [--step KEY]")
    ap.add_argument("--step")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--job")
    a = ap.parse_args()

    # ONE POLLER. Only the long-running watcher takes the lock - the one-shot modes
    # (--tick, --status, --doctor) are meant to be invoked at any time and must not be
    # blocked by the watcher. Two watchers racing each other is what lets the same
    # scheduled slot get enqueued twice.
    if a.watch:
        import single
        single.guard("Global\\AutoPost_Poller", "AutoPost poller")

    schema()
    if a.watch:
        sys.exit(watch(a.interval))
    if a.doctor:
        doctor()
    if a.retry:
        ok, msg = retry_job(a.retry, a.step, who="owner")
        log(msg)
        if ok:
            a.job = a.retry
            if not (a.watch or a.run):
                log("the poller will pick it up within seconds (or add --run to run it now)")
        else:
            sys.exit(1)
    if a.enqueue:
        a.job = a.job or enqueue(a.enqueue, a.kind, [p.strip() for p in a.platforms.split(",") if p.strip()],
                                 dry_run=a.dry_run, notify=a.notify)
    if a.tick or a.run:
        recover_orphans()            # a previous crash must never leave a step stuck 'running'
    if a.tick:
        tick(a.job)
    if a.run:
        run(a.job)
    if a.status or not (a.run or a.tick or a.doctor or a.enqueue or a.retry):
        show(a.job)
