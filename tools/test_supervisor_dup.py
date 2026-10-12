# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Regression test: duplicate rejections are settled by the team itself (no model, no human).
Runs against a SCRATCH copy of the DB - never the live one. Run:
    venv\Scripts\python.exe -I tools\test_supervisor_dup.py
"""
import os, shutil, sqlite3, sys, tempfile, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS", name)
    else:
        fail += 1
        print("  FAIL", name, extra)


import db
tmp = Path(tempfile.mkdtemp(prefix="sup_dup_"))
scratch = tmp / "brain.db"
shutil.copy(ROOT / "data" / "brain.db", scratch)
for ext in ("-wal", "-shm"):
    if (ROOT / "data" / ("brain.db" + ext)).exists():
        shutil.copy(ROOT / "data" / ("brain.db" + ext), tmp / ("brain.db" + ext))
db.DB = scratch                      # every module reads db.DB: the live brain.db is never touched
import paths
paths.OFFICE = tmp / "OFFICE"        # nor the live agent rooms / WORK folders
paths.AREAS = {k: tmp / "WORK" / v.name for k, v in paths.AREAS.items()}
paths.ensure_all = lambda: None

import department_queue as Q
import supervisor as S
S.LOG = tmp / "supervisor.log"            # never pollute the live log
S.STATE = tmp / "supervisor_state.json"
import research

# ---- 1. the self-reservation false alarm is revived --------------------------------
topic = "Why a quiet evening walk helps you sleep deeper tonight"
with db.conn() as c:
    c.execute("INSERT OR IGNORE INTO topics(title,source,score,hook,found,used) VALUES(?,?,?,?,?,1)",
              (topic, "test", 5, "FACT", time.time()))
check("topic collides with ITSELF without exclude_self (the old bug)", research.check_fresh(topic)[0] is False)
check("topic is fresh WITH exclude_self (the fix)", research.check_fresh(topic, exclude_self=True)[0] is True)

jid = Q.enqueue(topic, "text", ["socialai"], source="retry")      # source=retry skips vet (we test the supervisor)
Q._set(jid, "intake", "dead", "rejected, no retry possible: near-duplicate of an already used topic: \"%s\"" % topic)
p = {"kind": "dead", "job_id": jid, "step_key": "intake", "agent": "intake", "version": 1, "age_sec": 0,
     "reason": Q._row(jid, "intake")["reason"], "owner_pid": None}
v = S.triage_duplicate(p)
check("young false-alarm job is revived", v == "revived", v)
check("intake is READY again", Q._row(jid, "intake")["status"] == "ready")

# ---- 2. stale false alarm -> archived + topic released ------------------------------
meta = Q.read_job(jid)
meta["created"] = time.time() - 9 * 3600
Q.write_job(jid, meta)
Q._set(jid, "intake", "dead", p["reason"])
v = S.triage_duplicate(dict(p, reason=p["reason"]))
check("stale false-alarm job is archived", v == "archived_released", v)
check("its steps are skipped, not pending", all(r["status"] in ("done", "skipped") for r in Q._rows(jid)))
with db.conn() as c:
    used = c.execute("SELECT used FROM topics WHERE title=?", (topic,)).fetchone()[0]
check("its topic was released back to the bank", used == 0, used)
check("it is now ARCHIVED so the supervisor never nags again", S._archived(jid))

# ---- 3. a REAL duplicate is closed, not escalated -----------------------------------
real = "Why a quiet evening walk helps you sleep deeper tonight and tomorrow"
jid2 = Q.enqueue(real, "text", ["socialai"], source="retry")
with db.conn() as c:                                   # an OTHER used topic that overlaps >= 60 %
    c.execute("UPDATE topics SET used=1 WHERE title=?", (topic,))
Q._set(jid2, "intake", "dead", "rejected, no retry possible: near-duplicate of an already used topic: \"x\"")
p2 = dict(p, job_id=jid2, reason=Q._row(jid2, "intake")["reason"])
v = S.triage_duplicate(p2)
check("real duplicate is archived by the team", v == "archived_duplicate", v)

# ---- 4. non-duplicate failures are NOT touched by this path --------------------------
p3 = dict(p, reason="ffmpeg failed: concat")
check("other failures fall through to the normal diagnosis", S.triage_duplicate(p3) is None)

# ---- 4b. junk topics are refused at the FIRST stage, before any GPU time is spent ----------
for junk in ("TRENDING NOW \U0001F525l IYOG-YOG MO PARE l BEST DISCO CHACHA l SUBSCRIBE l @PinoyDubReggaePH",
             "Viral Crash-outs and Chaos | Best of the Week",
             "best tv prices"):
    okv, why = Q.vet_topic(junk, "video")
    check("junk refused at vet_topic: %s" % junk[:34], okv is False, why)
okv, why = Q.vet_topic("What matters most for children in their family relationships?", "video")
check("a real subject still passes vet_topic", okv is True, why)

# ---- 5. escalation is once per cooldown ---------------------------------------------
sent = []
import publish
publish.load_conn = lambda: {"telegram": {"token": "t", "chat_id": "c"}}
publish.telegram_send = lambda tok, chat, text="", **k: sent.append(text)
st = {}
S.escalate(p3, {"cause": "x", "fix": "y"}, st)
S.escalate(p3, {"cause": "x", "fix": "y"}, st)
check("escalation sent exactly once inside the cooldown", len(sent) == 1, len(sent))

# ---- 6. NO HUMAN: an unfixable dead job is closed by the team itself -------------------
def dead_job(topic_text, reason, kind="text"):
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO topics(title,source,score,hook,found,used,skipped) VALUES(?,?,?,?,?,1,0)",
                  (topic_text, "test", 5, "FACT", time.time()))
    j = Q.enqueue(topic_text, kind, ["socialai"], source="retry")
    Q._set(j, "noah", "dead", reason)
    return j, {"kind": "dead", "job_id": j, "step_key": "noah", "agent": "noah", "version": 2,
               "age_sec": 40 * 60, "reason": reason, "owner_pid": None}

j1, p1 = dead_job("A calm walk by the sea can settle a restless mind", "TimeoutError: child process exceeded 5280s and was killed")
check("inside the grace period nothing is closed yet", S.resolve_unfixable(dict(p1, age_sec=60), {"verdict": "FATAL", "cause": "x"}, {}) is False)
check("after the grace period the team closes an infra failure itself",
      S.resolve_unfixable(p1, {"verdict": "FATAL", "cause": "render timed out", "fix": "x"}, {}) is True)
check("   its steps are skipped (nothing left for a human)", all(r["status"] in ("done", "skipped") for r in Q._rows(j1)))
with db.conn() as c:
    u1 = c.execute("SELECT used FROM topics WHERE title=?", ("A calm walk by the sea can settle a restless mind",)).fetchone()[0]
check("   an infrastructure failure gives the topic back", u1 == 0, u1)
j2, p2 = dead_job("Why slow mornings make the whole day feel kinder", "quality gate: script contradicts its own sources")
check("a content failure is closed too", S.resolve_unfixable(p2, {"verdict": "FATAL", "cause": "bad script", "fix": "x"}, {}) is True)
with db.conn() as c:
    s2 = c.execute("SELECT skipped FROM topics WHERE title=?", ("Why slow mornings make the whole day feel kinder",)).fetchone()[0]
check("   a content failure skips the topic for good", s2 == 1, s2)
check("   the supervisor does not report it again (archived)", S._archived(j2))

# ---- 7. SOPHIE's last-resort fix for a capitalised common word ------------------------------
import department as D
import fact_grounding
sc = {"title": "Space weather", "narration": "A storm can unsettle us because Earth's field bends solar wind and Earth stays calm.",
      "story_arc": {"problem": "when Earth shakes"}}
res_none = {"mode": "no_sources", "verified_claims": [], "source_text": ""}
before = fact_grounding.grounding_check(fact_grounding.post_text(sc), res_none, "How geomagnetic storms affect sleep", "")
check("the old failure reproduces (Earth flagged)", any("Earth" in b for b in before), before)
fixed = D._lowercase_common(sc, before)
after = fact_grounding.grounding_check(fact_grounding.post_text(fixed), res_none, "How geomagnetic storms affect sleep", "")
check("lower-casing clears it with no model call", after == [], after)
check("nested fields are fixed too", fixed["story_arc"]["problem"] == "when earth shakes", fixed["story_arc"])
check("a real name or number is NOT touched", D._lowercase_common({"a": "Marie Curie 1903"}, ["Marie Curie", "1903"]) == {"a": "Marie Curie 1903"})

print("\n%d passed, %d failed" % (ok, fail))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fail else 0)
