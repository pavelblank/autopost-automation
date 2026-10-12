# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Prove the 18-agent department works - and cannot get stuck - by running it for real
against a SCRATCH database (the live brain.db is never touched).

  venv\Scripts\python.exe -I tools\test_department.py

Every scenario uses a fresh database. Work files land in videogen/WORK/<job_id>/ and are
removed at the end.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

import db as DB                       # noqa: E402
import department_queue as Q          # noqa: E402
import department as D                # noqa: E402
import paths                          # noqa: E402

# never touch the live agent rooms or the live WORK folders
_TMP = Path(tempfile.mkdtemp(prefix="dept_test_"))
paths.OFFICE = _TMP / "OFFICE"
paths.AREAS = {k: _TMP / "WORK" / v.name for k, v in paths.AREAS.items()}
paths.ensure_all = lambda: None
Q.BACKOFF_SCALE = 0
Q.vet_topic = lambda t, k="video": (True, "ok")     # the topic gate has its own tests (test_automation.py)
Q.WATCHDOG_GRACE = 10 ** 6            # never let a test end this process
RESULTS = []
JOBS = []
SCRATCH_DIR = Path(tempfile.gettempdir())


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %-4s %-62s %s" % ("OK" if ok else "FAIL", name, detail))


def fresh_db():
    p = SCRATCH_DIR / "dept_test.db"
    for suf in ("", "-wal", "-shm"):
        f = Path(str(p) + suf)
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass
    DB.DB = p
    Q.schema()
    return p


def states(jid):
    return {r["step_key"]: r["status"] for r in Q._rows(jid)}


def job(topic="dry probe", kind="text", **kw):
    jid = Q.enqueue(topic, kind, ["socialai"], **kw)
    JOBS.append(jid)
    return jid


def report(jid):
    r = DB.conn().execute("SELECT status FROM final_reports WHERE job_id=?", (jid,)).fetchone()
    return r["status"] if r else None


def quiet(fn, *a, **k):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


def scenario(title):
    print("\n%s" % title)
    fresh_db()


# ---------------------------------------------------------------------------------------
scenario("1. dry run, TEXT job: all 12 chain agents, daniel skipped, final report")
j = job(kind="text", dry_run=True)
quiet(Q.run, j)
st = states(j)
check("11 done + 1 skipped (daniel), none stuck", sum(v == "done" for v in st.values()) == 11 and
      st["daniel"] == "skipped",
      str({k: v for k, v in st.items() if v not in ("done",)}))
check("helena reported DRY RUN COMPLETE", report(j) == D.FINAL_STATUS["dry"], str(report(j)))
for key in ("maya", "sophie", "oliver", "isabella", "noah", "grace", "ethan", "chloe", "marcus", "ella"):
    pass
check("artifacts exist (brief, script, audit, approval, package, decision)",
      all(any((paths.job_dir(j, a) / n).exists() for a in ("scripts", "audits", "packages", "research", "visual", "clips", "evidence"))
          for n in ("creative_brief.json", "script.json", "planning_audit.json", "generation_approval.json",
                    "release_decision.json")))
room = (paths.room("ella") / "STATUS.md").read_text(encoding="utf-8")
check("room STATUS.md updated by the agent itself", j in room and "done" in room)

scenario("2. dry run, VIDEO job: daniel (+aisha inside) DOES run")
j = job(kind="video", dry_run=True)
quiet(Q.run, j)
st = states(j)
check("video: all 12 done", st["daniel"] == "done" and sum(v == "done" for v in st.values()) == 12, str(st))

scenario("3. one step ready at a time + idle is instant")
j = job(kind="text", dry_run=True)
mx = 0
while True:
    r, _ = quiet(Q.tick, j)
    n = DB.conn().execute("SELECT COUNT(*) FROM department_steps WHERE status IN ('ready','running')").fetchone()[0]
    mx = max(mx, n)
    if r is None:
        break
check("never more than one ready/running step", mx <= 1, "max=%d" % mx)
t0 = time.time()
quiet(Q.tick, j)
check("idle tick returns instantly", time.time() - t0 < 0.5, "%.3fs" % (time.time() - t0))

scenario("4. retry within budget (fails twice, third succeeds) - chain continues")
orig = D.run_maya
calls = {"n": 0}


def flaky(job_id):
    calls["n"] += 1
    if calls["n"] < 3:
        raise D.Reject("simulated model hiccup %d" % calls["n"])
    return orig(job_id)


D.run_maya = flaky
j = job(kind="text", dry_run=True)
quiet(Q.run, j)
D.run_maya = orig
check("maya succeeded on attempt 3 and the job completed", calls["n"] == 3 and report(j) == D.FINAL_STATUS["dry"], "calls=%d %s" % (calls["n"], report(j)))

scenario("5. backoff is honoured (retry not ready until not_before)")
Q.BACKOFF_SCALE = 1.0
calls = {"n": 0}


def once_bad(job_id):
    calls["n"] += 1
    raise D.Reject("always")


D.run_maya = once_bad
j = job(kind="text", dry_run=True)
quiet(Q.tick, j)                      # intake
quiet(Q.tick, j)                      # maya fails -> backoff 30s
row = Q._row(j, "maya")
nxt = Q.next_ready(j)
check("failed step is READY but waits (not_before in the future)", row["status"] == "ready" and row["not_before"] > time.time() + 20 and nxt is None,
      "wait=%.0fs" % (row["not_before"] - time.time()))
D.run_maya = orig
Q.BACKOFF_SCALE = 0

scenario("6. rejection loop is FINITE: routes back, then dead-letters, helena reports")
D.run_oliver_orig = D.run_oliver


def always_reject(job_id):
    raise D.Reject("simulated: plan always incomplete")


D.run_oliver = always_reject
j = job(kind="text", dry_run=True)
n, _ = quiet(Q.run, j, 400)
st = states(j)
D.run_oliver = D.run_oliver_orig
check("run ended (no infinite loop)", n < 400, "steps=%d" % n)
check("some step is dead-lettered", "dead" in st.values(), str({k: v for k, v in st.items() if v != "done"}))
check("nothing left running/ready", not any(v in ("running", "ready") for k, v in st.items() if k != "helena" or v != "done"), str(st))
check("HELENA reported AWAITING HUMAN ACTION", report(j) == D.FINAL_STATUS["human"], str(report(j)))

scenario("7. deterministic rejection (empty topic) dead-letters at once, no retries")
j = job(topic="", kind="text", dry_run=True)
quiet(Q.run, j)
r = Q._row(j, "intake")
check("intake dead after exactly 1 attempt", r["status"] == "dead" and r["version"] == 1, "%s v%d" % (r["status"], r["version"]))
check("job reported to a human", report(j) == D.FINAL_STATUS["human"], str(report(j)))

scenario("8. hard timeout: a step that overruns is failed, not waited on")
spec_orig = Q.spec_for


def tiny(key):
    s = spec_orig(key)
    if key == "maya":
        s = dict(s, timeout=1)
    return s


Q.spec_for = tiny
D.run_maya = lambda job_id: (time.sleep(2.5), "late")[1]
j = job(kind="text", dry_run=True)
quiet(Q.tick, j)
quiet(Q.tick, j)
r = Q._row(j, "maya")
Q.spec_for = spec_orig
D.run_maya = orig
check("overrunning maya was failed (retry armed), not marked done", r["status"] == "ready" and "hard timeout" in (r["reason"] or ""), "%s | %s" % (r["status"], r["reason"]))

scenario("9. dead heartbeat: a 'running' step with no heartbeat is reaped")
j = job(kind="text", dry_run=True)
c = DB.conn()
c.execute("UPDATE department_steps SET status='running', started=?, heartbeat=?, owner_pid=? WHERE job_id=? AND step_key='intake'",
          (time.time() - 500, time.time() - 500, 999999, j))
k = Q.reap(j)
r = Q._row(j, "intake")
check("stale step reaped and re-armed", k == 1 and r["status"] == "ready", "%s v%d" % (r["status"], r["version"]))

scenario("10. pid-reuse guard: reaper never kills an unrelated live process")
innocent = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
j = job(kind="text", dry_run=True)
DB.conn().execute("UPDATE department_steps SET status='running', started=?, heartbeat=?, owner_pid=? WHERE job_id=? AND step_key='intake'",
                  (time.time() - 500, time.time() - 500, innocent.pid, j))
quiet(Q.reap, j)
alive = innocent.poll() is None
innocent.kill()
check("unrelated process survived the reaper", alive)

scenario("11. startup recovery: orphan from a dead engine is recovered")
j = job(kind="text", dry_run=True)
DB.conn().execute("UPDATE department_steps SET status='running', started=?, heartbeat=?, owner_pid=? WHERE job_id=? AND step_key='intake'",
                  (time.time() - 30, time.time() - 30, 999999, j))
n, _ = quiet(Q.recover_orphans)
check("orphan recovered", n == 1 and Q._row(j, "intake")["status"] == "ready")

scenario("12. hard gates re-checked from FILES")
j = job(kind="text", dry_run=True)
try:
    D.run_noah(j)
    ok = False
except D.Fatal:
    ok = True
check("NOAH refuses without ISABELLA's approval file", ok)
try:
    D.run_ella(j)
    ok = False
except D.Fatal:
    ok = True
check("ELLA refuses without MARCUS's release file", ok)
d = D.Job.load(j)
d.write("isabella", "audits", "generation_approval.json", {"authorised": True, "job_id": "someone_else"})
try:
    D.run_noah(j)
    ok = False
except D.Fatal:
    ok = True
check("a forged approval for ANOTHER job is refused", ok)

scenario("13. near-duplicate topic is a hard stop at ARJUN")
import research
_orig_fresh = research.check_fresh
research.check_fresh = lambda t, exclude_self=False: (False, "an already used topic")
j = job(topic="dup topic", kind="text")          # NOT dry-run so ARJUN really checks
for k in ("intake", "maya"):
    pass
D_orig_llm = D.llm
D.llm = lambda *a, **k: {"objective": "x", "target_audience": "x", "central_message": "x", "intended_outcome": "x", "tone": "x"}
quiet(Q.tick, j); quiet(Q.tick, j); quiet(Q.tick, j)
r = Q._row(j, "intake")                  # NADIA runs ARJUN's duplicate check inside her step
research.check_fresh = _orig_fresh
D.llm = D_orig_llm
check("ARJUN dead-lettered the duplicate (no retry)", r["status"] == "dead" and "near-duplicate" in (r["reason"] or ""), "%s | %s" % (r["status"], (r["reason"] or "")[:60]))

scenario("14. local AI: invalid JSON retried, then FAILOVER to the backup model, primary unloaded")
seen = []
unloaded = []
orig_call, orig_unload, orig_models = D._ollama, D.unload_model, D.models
D.models = lambda: ["primary-x", "backup-y"]


def fake_call(model, prompt, system, as_json, timeout):
    seen.append(model)
    if model == "primary-x":
        raise OSError("connection refused")
    return json.dumps({"a": "1", "b": "2"})


D._ollama = fake_call
D.unload_model = lambda m: unloaded.append(m)
got = D.llm("p", as_json=True, required=["a", "b"])
check("backup model answered after the primary failed twice", got == {"a": "1", "b": "2"} and seen[:2] == ["primary-x", "primary-x"] and seen[2] == "backup-y", str(seen))
check("failed primary was UNLOADED before the backup was used", unloaded == ["primary-x"], str(unloaded))
D._ollama = lambda *a, **k: "not json at all"
try:
    D.llm("p", as_json=True, required=["a"])
    ok = False
except D.LLMError:
    ok = True
check("every model failing raises LLMError (engine will retry/dead-letter)", ok)
D._ollama = lambda *a, **k: json.dumps({"a": ""})
try:
    D.llm("p", as_json=True, required=["a"])
    ok = False
except D.LLMError:
    ok = True
check("a reply with an EMPTY required field is never accepted", ok)
D._ollama, D.unload_model, D.models = orig_call, orig_unload, orig_models

scenario("15. generation lease: NOAH waits (retry) when another generation holds it")
j = job(kind="text")
d = D.Job.load(j)
d.write("isabella", "audits", "generation_approval.json", {"authorised": True, "job_id": j})
d.write("sophie", "scripts", "script.json", {"title": "t", "narration": "n" * 60})
DB.conn().execute("INSERT INTO gen_lease(id,pid,started,source) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET pid=excluded.pid,started=excluded.started",
                  (os.getppid(), time.time(), "someone"))
try:
    D.run_noah(j)
    msg = "no error?"
except RuntimeError as e:
    msg = str(e)
except Exception as e:
    msg = "wrong: %r" % e
check("NOAH raises a retryable error, does not generate", "lease" in msg, msg[:70])

scenario("16. manual approval mode: MARCUS waits (no attempt spent), never self-approves")
j = job(kind="text")
d = D.Job.load(j)
for k, a, n, o in (("oliver", "audits", "planning_audit.json", {"verdict": "approved"}),
                   ("isabella", "audits", "generation_approval.json", {"authorised": True, "job_id": j}),
                   ("grace", "audits", "quality_inspection.json", {"verdict": "pass"}),
                   ("ethan", "audits", "final_content_audit.json", {"verdict": "approved"}),
                   ("chloe", "packages", "_summary.json", {"packaged": ["socialai"]})):
    d.write(k, a, n, o)
c = DB.conn()
rid = c.execute("INSERT INTO runs(topic,kind,started,status,approval) VALUES('x','text',?,'ok','pending')", (time.time(),)).lastrowid
d.set_meta(run_id=rid)
R_orig = D.rules
D.rules = lambda: dict(R_orig(), approval_mode="manual")
try:
    D.run_marcus(j)
    msg = "authorised?!"
except D.Wait as w:
    msg = "wait"
D.rules = R_orig
check("MARCUS waits for the human instead of approving", msg == "wait")
c.execute("UPDATE runs SET approval='approved' WHERE id=?", (rid,))
D.rules = lambda: dict(R_orig(), approval_mode="manual")
out = D.run_marcus(j)
D.rules = R_orig
check("after the human approves, release is AUTHORISED", "AUTHORISED" in out, out)

scenario("17. safety rail: a non-allowed live platform is refused")
j = Q.enqueue("rail", "text", ["telegram"], dry_run=True)
JOBS.append(j)
d = D.Job.load(j)
for k, a, n, o in (("oliver", "audits", "planning_audit.json", {"verdict": "approved"}),
                   ("isabella", "audits", "generation_approval.json", {"authorised": True, "job_id": j}),
                   ("grace", "audits", "quality_inspection.json", {"verdict": "pass"}),
                   ("ethan", "audits", "final_content_audit.json", {"verdict": "approved"}),
                   ("chloe", "packages", "_summary.json", {"packaged": ["telegram"]})):
    d.write(k, a, n, o)
_orig_rules = D.dept_rules
D.dept_rules = lambda: {"allowed_platforms": ["socialai"], "live_publish": False}     # the DEFAULT closed rails (live settings are the owner's, not the test's)
try:
    D.run_marcus(j)
    ok = False
except D.Fatal:
    ok = True
D.dept_rules = _orig_rules
check("MARCUS refuses 'telegram' (only the local blog is allowed by default)", ok)


scenario("18. an OLD-schema department_steps table (no heartbeat/owner_pid) is migrated")
p = fresh_db()
c = DB.conn()
c.execute("DROP TABLE IF EXISTS department_steps")
c.execute("CREATE TABLE department_steps (job_id TEXT NOT NULL, step_key TEXT NOT NULL, agent_key TEXT NOT NULL, "
          "stage INTEGER NOT NULL, depends_on TEXT, status TEXT NOT NULL DEFAULT 'pending', version INTEGER DEFAULT 0, "
          "reason TEXT, started REAL, finished REAL, PRIMARY KEY(job_id, step_key))")
Q.schema()
cols = [r[1] for r in DB.conn().execute("PRAGMA table_info(department_steps)")]
check("heartbeat, owner_pid, not_before were added", all(x in cols for x in ("heartbeat", "owner_pid", "not_before")), str(cols[-3:]))
j = job(kind="text", dry_run=True)
quiet(Q.run, j)
check("a job runs to the end on the migrated table", report(j) == D.FINAL_STATUS["dry"])

scenario("19. GRACE fails a video whose images were all solid-colour fallbacks")
j = job(kind="video")
d = D.Job.load(j)
rp = _TMP / "outfolder"
rp.mkdir()
(rp / "meta.json").write_text(json.dumps({"title": "t", "hook": "A decent sized hook sentence for the check.", "hashtags": []}), encoding="utf-8")
d.set_meta(run_path=str(rp), run_id=1)
(d.out("noah", "clips") / "noah_generation.log").write_text("ALL image sources down -> solid-color frame (last resort)\n", encoding="utf-8")
try:
    D.run_grace(j)
    msg = "passed?!"
except (D.Fatal, D.Reject) as e:
    msg = str(e)
check("GRACE dead-letters at once (image outage cannot be fixed by re-planning)", "solid-color" in msg and "SUPERVISOR retries" in msg, msg[:100])

scenario("20. VICTORIA's blog read-back: one copy passes; zero, duplicates and cut text FAIL")
import requests
j = job(kind="text")
d = D.Job.load(j)
text = "Stepping away from the constant hum of notifications can be a transformative experience for a busy mind."
d.write("chloe", "packages", "socialai.json", {"caption": text})
d.set_meta(created=time.time() - 5)


class FakeSession:
    posts = []
    verify = True

    def post(self, url, **k):
        class R: status_code = 200
        return R()

    def get(self, url, **k):
        posts = FakeSession.posts

        class R:
            status_code = 200

            def json(self_):
                return {"posts": posts}
        return R()


_S = requests.Session
requests.Session = FakeSession
now = time.time()
FakeSession.posts = []
r0 = D._verify_socialai(D.Job.load(j))[0]
FakeSession.posts = [{"id": "a", "created": now, "caption": text}]
r1 = D._verify_socialai(D.Job.load(j))[0]
FakeSession.posts = [{"id": "a", "created": now, "caption": text}, {"id": "b", "created": now + 1, "caption": text}]
r2 = D._verify_socialai(D.Job.load(j))
FakeSession.posts = [{"id": "a", "created": now, "caption": text[:50]}]
r3 = D._verify_socialai(D.Job.load(j))[0]
requests.Session = _S
check("0 copies -> fail", r0 == "fail", r0)
check("1 full copy -> pass", r1 == "pass", r1)
check("2 copies -> fail naming the duplicates (the 3x publish bug)", r2[0] == "fail" and "duplicate" in r2[1], r2[1][:60])
check("cut-off text -> fail", r3 == "fail", r3)

scenario("21. publish idempotency guard + hard process timeout")
import publish as pub
c = DB.conn()
check("no record -> not recorded", pub._socialai_already_recorded(777) is False)
c.execute("INSERT INTO posts(run_id,platform,status,posted,url,remote_id) VALUES(777,'socialai','ok',?,'u','r')", (time.time(),))
check("a successful push is recorded -> never pushed again", pub._socialai_already_recorded(777) is True)
t0 = time.time()
try:
    D.run_cmd([sys.executable, "-I", "-c", "import time; time.sleep(30)"], timeout=2)
    ok = False
except TimeoutError:
    ok = True
check("a child that overruns is killed at the timeout", ok and time.time() - t0 < 12, "%.1fs" % (time.time() - t0))

scenario("22. WAIT is not a failure: no attempt spent, re-armed later")
j = job(kind="text", dry_run=True)
D.run_oliver_orig = D.run_oliver


def waits(job_id):
    raise D.Wait("waiting for a human", 45)


D.run_intake_orig = D.run_intake
D.run_intake = waits
quiet(Q.tick, j)
r = Q._row(j, "intake")
D.run_intake = D.run_intake_orig
check("step is READY again later, version unchanged", r["status"] == "ready" and r["version"] == 0 and r["not_before"] > time.time() + 30,
      "%s v%d wait=%.0fs" % (r["status"], r["version"], r["not_before"] - time.time()))

scenario("23. approved plan reaches the generators (plan_hint); off by default")
import plan_hint
os.environ.pop("AUTOPOST_PLAN_FILE", None)
check("no env var -> nothing appended", plan_hint.block() == "")
pf = _TMP / "plan.json"
pf.write_text(json.dumps({"script": {"title": "T", "narration": "Approved narration text", "story_arc": {"problem": "p"}},
                          "brief": {"tone": "warm"}}), encoding="utf-8")
os.environ["AUTOPOST_PLAN_FILE"] = str(pf)
blk = plan_hint.block()
os.environ.pop("AUTOPOST_PLAN_FILE", None)
check("plan file -> prompt carries the approved script and brief", "Approved narration text" in blk and "warm" in blk)

scenario("24. trigger gate: off by default; submit() queues exactly one job")
import department_gate as G
import tempfile as _tf
_real_root = G.ROOT
_fake = Path(_tf.mkdtemp(prefix="gate_")); (_fake / "config").mkdir()
(_fake / "config" / "rules.json").write_text(json.dumps({"department": {"enabled": False}}), encoding="utf-8")
G.ROOT = _fake                                    # the test must not depend on the LIVE switch (the system is live now)
check("department.enabled is false -> gate does nothing", G.enabled() is False)
G.ROOT = _real_root
fresh_db()
jid = G.submit("gate probe topic", "video", ["socialai"], source="test", start_poller=False)
rows = Q._rows(jid)
check("job queued with only step 1 ready", len(rows) == 12 and [r["step_key"] for r in rows if r["status"] == "ready"] == ["intake"])
check("job.json records the source", Q.read_job(jid).get("source") == "test")

scenario("25. the engine hot-reloads changed agent code between steps")
check("_department() returns the module", Q._department() is not None)

scenario("26. a step has ONE total time budget: model calls never start past it")
calls = []
_oc = D._ollama
D._ollama = lambda *a, **k: (calls.append(1), json.dumps({"a": "1"}))[1]
D.DEADLINE["t"] = time.time() + 10          # less than the 25s minimum a call needs
try:
    D.llm("p", as_json=True, required=["a"])
    ok = False
except D.LLMError as e:
    ok = "budget" in str(e)
D.DEADLINE["t"] = None
D._ollama = _oc
check("no model call is started once the step budget is nearly spent", ok and not calls, "calls=%d" % len(calls))

scenario("27. two engines can never claim the same step (atomic claim)")
j = job(kind="text", dry_run=True)
first = Q.claim(j, "intake")
second = Q.claim(j, "intake")
check("first engine wins, second is refused", first is True and second is False, "%s/%s" % (first, second))

scenario("28. a stopped job is never silent: idle sweep arms HELENA even if the dead step died earlier")
j = job(kind="text", dry_run=True)
c = DB.conn()
for k in ("intake", "maya", "sophie", "daniel", "leo", "oliver", "isabella", "noah"):
    c.execute("UPDATE department_steps SET status=? WHERE job_id=? AND step_key=?", ("skipped" if k == "daniel" else "done", j, k))
c.execute("UPDATE department_steps SET status='dead', reason='simulated defect' WHERE job_id=? AND step_key='grace'", (j,))
c.execute("UPDATE department_steps SET status='pending' WHERE job_id=? AND step_key NOT IN ('intake','maya','sophie','daniel','leo','oliver','isabella','noah','grace')", (j,))
quiet(Q.run, j)
check("HELENA reported the stopped job to a human", report(j) == D.FINAL_STATUS["human"], str(report(j)))

# ---------------------------------------------------------------------------------------
shutil.rmtree(_TMP, ignore_errors=True)
for jid in []:
    for a in paths.AREAS.values():
        shutil.rmtree(a / jid, ignore_errors=True)
print("\n" + "=" * 78)
passed = sum(1 for r in RESULTS if r[1])
print("DEPARTMENT TEST SUITE: %d/%d passed" % (passed, len(RESULTS)))
print("=" * 78)
for n, ok, d in RESULTS:
    if not ok:
        print("  FAILED: %s  %s" % (n, d))
sys.exit(0 if passed == len(RESULTS) else 1)
