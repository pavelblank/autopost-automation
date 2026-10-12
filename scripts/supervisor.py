# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""SUPERVISOR - agent 19. The department's doctor.

WHY THIS EXISTS
The engine is already safe against hanging: every step has a hard timeout, a heartbeat,
a finite retry budget and a route-back rule. What the engine CANNOT do is explain itself.
When a job stops, the engine records "dead" and waits for a human. SUPERVISOR answers the
three questions a human would ask: WHERE is it stuck, WHY, and WHAT should happen now -
and it uses the LOCAL model to read the evidence and decide.

WHAT IT IS NOT
It is not a chain step (a chain step cannot watch while it is queued). It is a sidecar that
runs on the poller tick, so it sees every job at once.

SAFETY RULES (hard-coded, not configurable)
  1. It NEVER publishes and never touches the ISABELLA / MARCUS authorisation gates.
  2. The model may only CHOOSE a verdict from a fixed list; the action is chosen by
     SUPERVISOR's own whitelist, so a bad model reply can never escalate its own powers.
  3. It cannot un-dead-letter. It cannot raise a retry budget above the spec.
  4. Bounded: at most MAX_ACTIONS interventions per job per window, so it cannot loop.
  5. Every single action is written to department_jobs before it happens.

Usage:
    python scripts/supervisor.py --once        # one pass, print the report
    python scripts/supervisor.py --watch 60    # every 60s (the poller calls this itself)
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import json, os, re, sys, time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

import department_queue as Q          # noqa: E402
import db                              # noqa: E402

LOG = ROOT / "data" / "supervisor.log"
STATE = ROOT / "data" / "supervisor_state.json"

STALL_SEC = Q.HEARTBEAT_SEC * Q.HEARTBEAT_DEAD * 2      # 2x the engine's own kill point
WAIT_SLA_SEC = 45 * 60                                   # a READY step waiting 45 min is stuck
LOOP_VERSION = 3                                          # this many attempts = looping
MAX_ACTIONS_PER_JOB = 3          # per window
ACTION_WINDOW_SEC = 30 * 60
DIAG_COOLDOWN = 2 * 3600         # a parked job is re-diagnosed by the model at most this often (it used to be every
                                 # ~8 min: two 120 s model calls per job per pass that starved the real work of the GPU)
ESCALATE_COOLDOWN = 6 * 3600     # the same job is reported at most this often
FRESH_MAX_AGE = 6 * 3600         # a false-alarm job older than this is closed, not resumed
DEAD_GRACE = 20 * 60             # a dead step gets this long (auto-heal retries) before the team closes the job itself
DUP_RX = re.compile(r"near-duplicate|already used topic|duplicate topic|repeat of something|already published", re.I)

# verdict -> what SUPERVISOR is allowed to do. The model picks the verdict only.
VERDICT_ACTION = {
    "TRANSIENT":        "retry_now",     # clear the backoff, let it try again immediately
    "BLOCKED_EXTERNAL": "wait_and_retry",# an outside service is down; hold, do NOT burn attempts
    "CONFIG":           "escalate",      # a file/path/key is wrong - only a human should edit code
    "BUG":              "retry_now",     # probably one-off; if it recurs the budget still ends it
    "FATAL":            "leave_dead",    # do not touch it
}


def log(m):
    line = "[SUP %s] %s" % (time.strftime("%H:%M:%S"), m)
    try:
        print(line, flush=True)
    except Exception:        # cp1252 console + emoji must never crash the watchdog
        pass
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------- state
def _state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
    except Exception:
        pass


def _budget_ok(st, job_id):
    """Bounded: a job can only be nudged MAX_ACTIONS_PER_JOB times per window."""
    now = time.time()
    hist = [t for t in st.get(job_id, []) if now - t < ACTION_WINDOW_SEC]
    st[job_id] = hist
    return len(hist) < MAX_ACTIONS_PER_JOB


def _spent(st, job_id):
    st.setdefault(job_id, []).append(time.time())


# ------------------------------------------------------------------- finding
def _archived(job_id):
    """True if a human deliberately closed this job (an ARCHIVE row exists)."""
    try:
        r = Q.schema().execute(
            "SELECT 1 FROM department_jobs WHERE job_id=? AND agent='ARCHIVE' LIMIT 1",
            (job_id,)).fetchone()
        return r is not None
    except Exception:
        return False


def find_problems():
    """Everything that could stop the department, each with the evidence to explain it."""
    c = Q.schema()
    now = time.time()
    found = []

    # 1. a step stuck 'running' - the engine should have reaped it, so this is itself a fault
    for r in c.execute("SELECT * FROM department_steps WHERE status='running'"):
        age = now - (r["heartbeat"] or r["started"] or now)
        if age > STALL_SEC:
            found.append({"kind": "stalled", "job_id": r["job_id"], "step_key": r["step_key"],
                          "agent": r["agent_key"], "version": r["version"] or 0,
                          "age_sec": int(age), "reason": r["reason"] or "",
                          "owner_pid": r["owner_pid"]})

    # 2. a step that spent its retry budget - parked forever until a human
    for r in c.execute("SELECT * FROM department_steps WHERE status='dead'"):
        found.append({"kind": "dead", "job_id": r["job_id"], "step_key": r["step_key"],
                      "agent": r["agent_key"], "version": r["version"] or 0,
                      "age_sec": int(now - (r["finished"] or now)), "reason": r["reason"] or "",
                      "owner_pid": None})

    # 3. READY but not runnable yet for far too long (endless backoff, or nobody ticking)
    for r in c.execute("SELECT * FROM department_steps WHERE status='ready' AND not_before IS NOT NULL"):
        if now - (r["not_before"] or now) > WAIT_SLA_SEC:
            found.append({"kind": "waiting", "job_id": r["job_id"], "step_key": r["step_key"],
                          "agent": r["agent_key"], "version": r["version"] or 0,
                          "age_sec": int(now - (r["not_before"] or now)), "reason": r["reason"] or "",
                          "owner_pid": None})

    # 4. looping: same step retried over and over
    for r in c.execute("SELECT * FROM department_steps WHERE version>=? AND status IN "
                       "('ready','failed','pending')", (LOOP_VERSION,)):
        found.append({"kind": "looping", "job_id": r["job_id"], "step_key": r["step_key"],
                      "agent": r["agent_key"], "version": r["version"] or 0,
                      "age_sec": 0, "reason": r["reason"] or "", "owner_pid": None})

    # 5. a SILENT job: no runnable step, no dead step, nothing reported. The worst kind,
    #    because nothing in the system is complaining - it just stopped.
    jobs = [r[0] for r in c.execute("SELECT DISTINCT job_id FROM department_steps")]
    for jid in jobs:
        rows = Q._rows(jid)
        if not rows:
            continue
        try:
            import stock
            # held on purpose, not stuck: either listed in STOCK, or the JOB ITSELF is a stock job that was never released.
            # (2026-10-11: the check used the stock LIST only; a discarded item made its held video look stuck, the Watchdog
            # "rescued" it, and a video with a broken voice-over was published. The job's own flag is the truth.)
            if stock.is_stocked(jid) or stock.should_hold(jid):
                continue
        except Exception:
            pass
        st = {r["status"] for r in rows}
        runnable = any(r["status"] in ("ready", "running") for r in rows)
        dead = any(r["status"] == "dead" for r in rows)
        finished = any(r["step_key"] == "helena" and r["status"] in ("done", "skipped", "dead")
                       for r in rows)
        if not runnable and not dead and not finished:
            stuck = [r for r in rows if r["status"] == "pending"]
            found.append({"kind": "silent", "job_id": jid,
                          "step_key": stuck[0]["step_key"] if stuck else "?",
                          "agent": stuck[0]["agent_key"] if stuck else "?",
                          "version": 0, "age_sec": 0,
                          "reason": "job has no runnable step, no dead step and no report - "
                                    "it stopped silently", "owner_pid": None})

    # One job = ONE problem to solve. A dead chain shows every step as dead, but only the
    # EARLIEST one is the cause; the rest are just its consequences. Diagnosing 25 rows for
    # one stuck job would be 25 model calls and 25 Telegram messages about the same thing.
    by_job = {}
    for p in found:
        # skip jobs a human already closed on purpose - never nag about them again
        if _archived(p["job_id"]):
            continue
        rank = {"silent": 0, "stalled": 1, "dead": 2, "looping": 3, "waiting": 4}[p["kind"]]
        key = p["job_id"]
        cand = (rank, _stage_of(p))
        if key not in by_job or cand < by_job[key][0]:
            by_job[key] = (cand, p)
    return [v[1] for v in by_job.values()]


def _stage_of(p):
    r = Q._row(p["job_id"], p["step_key"])
    return (r["stage"] if r else 99)


# ---------------------------------------------------------------- diagnosing
def evidence(p):
    """Everything a human would look at before deciding, as one block of text."""
    c = Q.schema()
    parts = ["PROBLEM: %s at step %s (agent %s)" % (p["kind"].upper(), p["step_key"], p["agent"]),
             "job id: %s" % p["job_id"], "attempts used: %s" % p["version"],
             "recorded reason: %s" % (p["reason"] or "(none recorded)")]
    if p.get("owner_pid"):
        alive = Q._pid_alive(p["owner_pid"])
        parts.append("owning process %s: %s" % (p["owner_pid"], "ALIVE" if alive else "DEAD"))
    rows = c.execute("SELECT step_key,status,version,reason FROM department_steps "
                     "WHERE job_id=? ORDER BY stage", (p["job_id"],)).fetchall()
    parts.append("chain state:")
    for r in rows:
        parts.append("   %-10s %-8s v%-2s %s" % (r["step_key"], r["status"], r["version"],
                                                 (r["reason"] or "")[:110]))
    # the generator's own log is usually the real answer.
    # NOTE: paths.AREAS keys are SHORT ("clips"), not the folder names ("05-clips").
    for area in ("clips", "audits", "scripts"):
        d = Q.paths.job_dir(p["job_id"], area)
        for logname in ("noah_generation.log", "publish.log", "generation.log"):
            f = d / logname
            if f.exists():
                try:
                    tail = f.read_text(encoding="utf-8", errors="replace")[-1200:]
                    parts.append("tail of %s/%s:\n%s" % (area, logname, tail))
                except Exception:
                    pass
    meta = Q.read_job(p["job_id"]) or {}
    if meta:
        parts.append("job: kind=%s topic=%s dry_run=%s"
                     % (meta.get("kind"), (meta.get("topic") or "")[:90], meta.get("dry_run")))
    return "\n".join(parts)


SYSTEM = (
    "You are the SUPERVISOR of an automated content-production pipeline. "
    "A step in the chain has stopped. Read the evidence and decide what should happen now.\n"
    "Answer with ONE JSON object and nothing else:\n"
    '{"verdict": "<TRANSIENT|BLOCKED_EXTERNAL|CONFIG|BUG|FATAL>", '
    '"cause": "<one sentence, plain>", '
    '"fix": "<one concrete action a human or the system can take, max 25 words>", '
    '"retry_safe": true or false}\n\n'
    "Choose the verdict as:\n"
    "  TRANSIENT        - a blip: timeout, momentary network drop, a race. Safe to retry now.\n"
    "  BLOCKED_EXTERNAL - something outside the pipeline is down (image service, model "
    "server, disk). Retrying now wastes the budget. Say so.\n"
    "  CONFIG           - a file, path, key or setting is wrong. A human must edit it.\n"
    "  BUG              - the pipeline itself looks wrong or inconsistent.\n"
    "  FATAL            - this can never succeed as things stand (empty input, duplicate, "
    "missing authorisation).\n"
    "Be concrete and brief. Never invent errors that are not in the evidence."
)


def diagnose(p):
    """Ask the LOCAL model what is wrong. Falls back to a safe default if it cannot answer."""
    fallback = {"verdict": "BUG" if p["kind"] in ("silent", "stalled") else "FATAL",
                "cause": "local model could not be reached; using the safe default",
                "fix": "a human should inspect this job", "retry_safe": False}
    try:
        import department as D
        d = D.llm("Evidence:\n" + evidence(p), system=SYSTEM, as_json=True, timeout=120,
                  required=["verdict", "cause", "fix"])
        v = str(d.get("verdict", "")).upper().strip()
        if v not in VERDICT_ACTION:
            d["verdict"] = "BUG"
        return d
    except Exception as e:
        # The model was busy (the GPU is shared with the very job that stopped). Do not park the job
        # as dead: a time-out or a restart is a blip by definition. The retry budget still applies.
        why = (p.get("reason") or "").lower()
        if re.search(r"exceeded \d+s|timed out|timeouterror|interrupted by a restart|killed", why):
            return {"verdict": "TRANSIENT", "cause": "time-out / restart (rule-based; the model was busy)",
                    "fix": "retry the step", "retry_safe": True}
        fallback["cause"] = fallback["cause"] + " (%s)" % str(e)[:80]
        return fallback


# ------------------------------------------------------------------ acting
def act(p, d):
    """Apply the whitelist action for the verdict. Never touches publish, never raises budget."""
    verdict = str(d.get("verdict", "")).upper()
    action = VERDICT_ACTION.get(verdict, "leave_dead")
    jid, key = p["job_id"], p["step_key"]
    reason = "SUPERVISOR %s: %s | %s" % (action, (d.get("cause") or "")[:110],
                                         (d.get("fix") or "")[:110])

    if action == "retry_now":
        Q._set(jid, key, "ready", reason, not_before=time.time())
        # a correction round is legitimate: let the failing step try again from scratch
        Q.schema().execute("UPDATE department_steps SET version=0 WHERE job_id=? AND step_key=?",
                           (jid, key))
    elif action == "wait_and_retry":
        hold = 15 * 60
        Q._set(jid, key, "ready", reason, not_before=time.time() + hold)
    else:
        # escalate / leave_dead - change NOTHING, but still record that the supervisor
        # looked and decided, so the decision is reconstructable later.
        try:
            Q.audit(jid, "supervisor", "decision", reason)
        except Exception:
            pass
        return action, False

    Q.audit(jid, "supervisor", "action", reason)   # recorded BEFORE the chain can move
    return action, True


def escalate(p, d, st=None, fyi=False):
    """Last resort only: a problem the team could NOT fix by itself. Said ONCE per job per
    ESCALATE_COOLDOWN (it used to repeat every 8 minutes all night), and sent with the real
    token + chat id (the old call passed only the text and always failed)."""
    jid, key = p["job_id"], p["step_key"]
    if st is not None:
        last = st.setdefault("_esc", {}).get(jid, 0)
        if time.time() - last < ESCALATE_COOLDOWN:
            return False
        st["_esc"][jid] = time.time()
    try:
        import report_format
        meta = Q.read_job(jid) or {}
        text = report_format.notice(
            "info" if fyi else "alert", meta.get("topic"), key,
            d.get("cause") or "a step stopped",
            "closed the job and released the topic; the next scheduled post will use another topic" if fyi
            else "tried to fix it automatically but could not",
            None if fyi else (d.get("fix") or "Please check the dashboard (Posts page)."))
    except Exception:
        head = ("AutoPost SUPERVISOR (information only - nothing for you to do): job %s stopped at %s and was closed automatically."
                if fyi else "AutoPost SUPERVISOR: job %s is stuck at %s (%s) and the team could not fix it.")
        text = ((head % (jid, key.upper())) if fyi else (head % (jid, key.upper(), p["kind"]))) + \
            "\nWhy: %s\nNext: %s\nStatus: python scripts/department_queue.py --status --job %s" % (d.get("cause"), d.get("fix"), jid)
    log("ESCALATE -> " + text.replace("\n", " | ")[:240])
    try:
        import publish as PUB
        conns = PUB.load_conn()
        for name in ("telegram", "telegram_channel"):
            tg = conns.get(name, {})
            if tg.get("token") and tg.get("chat_id"):
                PUB.telegram_send(tg["token"], tg["chat_id"], text)
                log("telegram sent")
                return True
    except Exception as e:
        log("telegram not sent (%s) - logged only" % str(e)[:70])
    return False


# -------------------------------------------------- self-repair of duplicate rejections
def _archive(jid, why):
    """Close a job on purpose (same shape as the clean-up's ARCHIVED rows): every unfinished
    step is skipped, one ARCHIVE row is written, and the supervisor never nags about it again.
    A stock job also frees its stock slot (stock.tick only notices DEAD steps, and these are now skipped)."""
    try:
        import stock
        sd = stock._load()
        it = stock._find(sd, jid)
        if it and it.get("status") == "in_production":
            it["status"], it["note"] = "failed", "closed by SUPERVISOR: " + why[:100]
            stock._save(sd)
    except Exception:
        pass
    c = Q.schema()
    c.execute("UPDATE department_steps SET status='skipped', reason=?, finished=?, owner_pid=NULL "
              "WHERE job_id=? AND status NOT IN ('done','skipped')",
              ("ARCHIVED: " + why, time.time(), jid))
    c.execute("INSERT INTO department_jobs(job_id,agent,stage,role,status,reason,version,ts) "
              "VALUES(?,?,?,?,?,?,?,?)",
              (jid, "ARCHIVE", 0, "Archive of a closed job", "archived",
               "ARCHIVED %s: %s" % (time.strftime("%Y-%m-%d"), why), 0, time.time()))


def _release_topic(topic):
    """Give a topic back to the bank (a false duplicate must not burn a good topic for ever)."""
    try:
        with db.conn() as c:
            c.execute("UPDATE topics SET used=0, skipped=0 WHERE title=?", (topic,))
    except Exception as e:
        log("could not release topic (%s)" % str(e)[:60])


INFRA_RX = re.compile(r"timed? ?out|exceeded|killed|image service|402|429|lease|another generation|connection|"
                      r"unreachable|could not be reached|ffmpeg|voice|disk|memory|vram|out of", re.I)


def resolve_unfixable(p, d, st):
    """NO HUMAN: a job that retries could not fix is CLOSED by the team, never left 'for a human'.
    Waits DEAD_GRACE after the last failure (auto-heal's retries get their chance first), then
      - archives the job (every unfinished step skipped, stock slot freed),
      - gives the topic back to the bank if the cause was infrastructure (a timeout, a service down),
        or skips it for good if the content itself was the problem,
      - the next stock/schedule tick picks another topic by itself (and the stock circuit breaker
        stops a loop of failures),
      - tells the owner once, as information, not as a request.
    Returns True when it closed the job."""
    if p["kind"] != "dead" or p["age_sec"] < DEAD_GRACE:
        return False
    jid = p["job_id"]
    meta = Q.read_job(jid) or {}
    topic = (meta.get("topic") or "").strip()
    text = " ".join([p.get("reason") or "", str(d.get("cause") or "")])
    infra = bool(INFRA_RX.search(text)) or str(d.get("verdict", "")).upper() in ("TRANSIENT", "BLOCKED_EXTERNAL")
    why = "unfixable by retries (%s): %s" % ("infrastructure" if infra else "content", (p.get("reason") or d.get("cause") or "")[:90])
    _archive(jid, why)
    if topic:
        if infra:
            _release_topic(topic)
        else:
            try:
                with db.conn() as c:
                    c.execute("UPDATE topics SET skipped=1 WHERE title=?", (topic,))
            except Exception:
                pass
    Q.audit(jid, "supervisor", "action", "closed automatically - " + why)
    log("RESOLVED %s: %s -> closed, topic %s, next tick picks another (no human)"
        % (jid, why[:80], "released" if infra else "skipped"))
    escalate(p, dict(d, cause=why, fix="nothing - the job was closed and the next topic is picked automatically"), st, fyi=True)
    return True


def triage_duplicate(p):
    """A job died because its topic looked like a duplicate. Decide with FACTS, not a model and
    not a human. Returns a short verdict string, or None when this is not a duplicate case.
      false alarm + job still young  -> re-open the SAME job (it carries on from where it stopped)
      false alarm + job stale        -> close it and release the topic; the next stock/schedule
                                        tick picks it up fresh through the normal checks
      real duplicate                 -> close the job; the topic stays used; the pickers choose
                                        another one on their next tick (they already skip dupes)"""
    if p["kind"] != "dead" or not DUP_RX.search(p.get("reason") or ""):
        return None
    jid = p["job_id"]
    meta = Q.read_job(jid) or {}
    topic = (meta.get("topic") or "").strip()
    if not topic:
        return None
    try:
        import research, post_history
        fresh, similar = research.check_fresh(topic, exclude_self=True)
        repeat = post_history.find_repeat(topic)
    except Exception as e:
        log("duplicate triage could not check %s (%s) - falling back" % (jid, str(e)[:60]))
        return None
    age = time.time() - float(meta.get("created") or time.time())
    if fresh and not repeat:
        if age <= FRESH_MAX_AGE and not meta.get("dry_run"):
            ok, msg = Q.retry_job(jid, p["step_key"], who="supervisor: the duplicate flag was a false alarm")
            Q.audit(jid, "supervisor", "action", "false duplicate alarm - job re-opened: " + msg)
            log("DUPLICATE %s: false alarm (topic only collided with its own reservation) -> re-opened" % jid)
            return "revived"
        _archive(jid, "stale job (%d h old) whose duplicate flag was a false alarm; topic released" % int(age // 3600))
        _release_topic(topic)
        Q.audit(jid, "supervisor", "action", "stale false-alarm job archived, topic released")
        log("DUPLICATE %s: false alarm but stale -> archived, topic released for a fresh run" % jid)
        return "archived_released"
    why = ("repeat of published work: %s" % repeat) if repeat else ("close to used topic: %s" % similar)
    _archive(jid, "real duplicate (%s); the topic pickers choose another one" % str(why)[:90])
    Q.audit(jid, "supervisor", "action", "real duplicate archived: " + str(why)[:100])
    log("DUPLICATE %s: real duplicate (%s) -> archived, no human needed" % (jid, str(why)[:60]))
    return "archived_duplicate"


# ------------------------------------------------------------------- main
def run_once(quiet=False):
    try:
        import authorship                      # keeps the author credit in place (never raises)
        authorship.guard(ROOT, log=log)
    except Exception:
        pass
    st = _state()
    problems = find_problems()
    if not problems:
        _save(st)
        if not quiet:
            log("scan: nothing stuck. %d job(s) tracked." % len(st))
        return {"found": 0, "acted": 0}

    acted = 0
    report = []
    for p in problems:
        # 1. facts first: a duplicate rejection is settled by the team itself (no model, no human)
        try:
            verdict = triage_duplicate(p)
        except Exception as e:
            verdict = None
            log("duplicate triage failed on %s (non-fatal): %s" % (p["job_id"], str(e)[:100]))
        if verdict:
            acted += 1
            report.append({"job_id": p["job_id"], "step": p["step_key"], "kind": p["kind"],
                           "verdict": "DUPLICATE", "action": verdict})
            continue
        # 2. a parked job is not re-read by the model every pass: nothing new would be learned and the
        #    two 120 s calls kept the GPU busy all night ("local model could not be reached")
        diag = st.setdefault("_diag", {})
        if p["kind"] == "dead" and time.time() - diag.get(p["job_id"], 0) < DIAG_COOLDOWN:
            continue
        diag[p["job_id"]] = time.time()
        d = diagnose(p)
        entry = {"job_id": p["job_id"], "step": p["step_key"], "kind": p["kind"],
                 "verdict": d.get("verdict"), "cause": d.get("cause"), "fix": d.get("fix")}
        if _budget_ok(st, p["job_id"]):
            try:
                action, did = act(p, d)
                entry["action"] = action
                if did:
                    _spent(st, p["job_id"])
                    acted += 1
                    log("%s %s/%s -> %s (%s)" % (p["kind"].upper(), p["job_id"],
                                                 p["step_key"], action, d.get("cause")))
                elif action in ("escalate", "leave_dead"):
                    if resolve_unfixable(p, d, st):
                        entry["action"] = "closed_automatically"
                        acted += 1
                    else:
                        escalate(p, d, st)
            except Exception as e:
                entry["error"] = str(e)[:150]
                log("ACTION FAILED on %s/%s: %s" % (p["job_id"], p["step_key"], str(e)[:120]))
        else:
            entry["action"] = "budget_spent"
            if not resolve_unfixable(p, d, st):
                escalate(p, d, st)
            log("budget spent for %s - escalated instead of acting again" % p["job_id"])
        report.append(entry)

    _save(st)
    # a stopped job must never be silent: make sure HELENA is armed for it
    for p in problems:
        try:
            Q.close_dead_job(p["job_id"])
        except Exception:
            pass
    if not quiet:
        log("scan: %d problem(s) found, %d action(s) taken" % (len(problems), acted))
    return {"found": len(problems), "acted": acted, "detail": report}


def watch(interval=60):
    log("supervisor online (every %ds). Ctrl+C to stop." % interval)
    while True:
        try:
            run_once()
        except Exception as e:
            log("pass failed (non-fatal): %s" % str(e)[:150])
        time.sleep(interval)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        i = sys.argv[sys.argv.index("--watch") + 1]
        watch(int(i) if i.isdigit() else 60)
    else:
        print(json.dumps(run_once(), indent=2, default=str))