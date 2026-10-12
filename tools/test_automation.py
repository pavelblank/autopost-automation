# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Prove the failure path, the duplicate guards and ARJUN's topic gate - on a SCRATCH database
(the live brain.db is never touched, no GPU, no network, no model).

  venv\Scripts\python.exe -I tools\test_automation.py
"""
import json
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
import dept_actions as A              # noqa: E402
import paths                          # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="auto_test_"))
paths.OFFICE = _TMP / "OFFICE"
paths.AREAS = {k: _TMP / "WORK" / v.name for k, v in paths.AREAS.items()}
paths.ensure_all = lambda: None
Q.BACKOFF_SCALE = 0
Q.WATCHDOG_GRACE = 10 ** 6
Q._HEAL_FILE = _TMP / "auto_heal.json"
A.ROOT = _TMP                                      # job_error.json lands in the scratch area
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %-4s %-66s %s" % ("OK" if ok else "FAIL", name, detail))


def fresh_db():
    p = Path(tempfile.gettempdir()) / "auto_test.db"
    for suf in ("", "-wal", "-shm"):
        f = Path(str(p) + suf)
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass
    DB.DB = p
    Q.schema()
    import research
    with research.conn() as c:                      # same scratch db: the topic bank
        c.executescript(research.SCHEMA) if hasattr(research, "SCHEMA") else None
    return p


def mkjob(topic="a brand new and unusual idea about patient gardening", **kw):
    for d in paths.AREAS.values():
        Path(d).mkdir(parents=True, exist_ok=True)
    return Q.enqueue(topic, "text", ["socialai"], **kw)


print("== A. diagnosis: every stopped step gets an ERROR, an ACTION and (if it helps) a RETRY ==")
cases = [
    ('near-duplicate of an already used topic: "x"', False, "Topic already used"),
    ("technical: video file missing or under 20 KB", True, "failed an inspection"),
    ("RuntimeError: ffmpeg failed: concat", True, "ffmpeg"),
    ("no voice engine produced audio", True, "voice"),
    ("generator exited (rc=0) but recorded no run", True, "generator"),
    ("another generation holds the lease", True, "GPU was busy"),
    ("hard timeout 5280s exceeded", True, "time limit"),
    ("something nobody has seen before", True, "stopped"),
]
for text, retry, word in cases:
    d = A.diagnose("noah", text)
    check("diagnose: " + text[:44], d["retry_ok"] == retry and word.lower() in d["error"].lower(), d["error"][:46])
check("every diagnosis has an action", all(A.diagnose("x", t)["action"] for t, _, _ in cases))
check("retry command names the same job", "--retry job_X --step noah" in A.retry_command("job_X", "noah"))

print("== B. ARJUN vets the topic BEFORE any job exists ==")
fresh_db()
import research
with research.conn() as c:
    c.execute("INSERT OR IGNORE INTO topics(title,source,found,used) VALUES(?,?,?,1)",
              ("why the morning light changes how we heal", "you", time.time()))
ok, why = Q.vet_topic("hi")
check("too-short topic refused", not ok, why[:50])
ok, why = Q.vet_topic("Why the morning light changes how we heal")
check("near-duplicate of a used topic refused", not ok, why[:60])
ok, why = Q.vet_topic("a brand new and unusual idea about patient gardening")
check("a new, good topic passes", ok, why)
check("refused topic creates NO job", Q.enqueue("hi", "text", ["socialai"]) is None)

print("== C. no duplicate work ==")
j1 = mkjob()
check("good topic becomes a job", bool(j1), str(j1))
j2 = Q.enqueue("A brand new and unusual idea about patient gardening!", "text", ["socialai"])
check("same topic (reworded punctuation) is NOT queued twice", j2 is None)
msgs = [dict(r) for r in Q.schema().execute("SELECT agent,status,reason FROM department_jobs WHERE job_id=? ORDER BY ts, id", (j1,))]
check("job story starts with ARJUN's vetting", msgs and msgs[0]["agent"] == "ARJUN" and "new and good" in msgs[0]["reason"], str(msgs[:1])[:70])
check("then INTAKE", len(msgs) > 1 and msgs[1]["agent"] == "INTAKE")

print("== D. a stopped job: ERROR + ACTION + RETRY, same job, finite auto-heal ==")
Q._set(j1, "intake", "dead", "technical: video file missing or under 20 KB")
check("job is stopped (dead)", Q._row(j1, "intake")["status"] == "dead")
ok, msg = Q.retry_job(j1, "intake")
check("--retry re-opens the SAME job", ok and Q._row(j1, "intake")["status"] == "ready", msg)
check("retry resets the attempt budget", Q._row(j1, "intake")["version"] == 0)
ok, msg = Q.retry_job(j1, "intake")
check("retrying a job that is not stopped is refused", not ok, msg)
check("unknown job refused", not Q.retry_job("job_nope")[0])

for n in range(1, 5):                                  # auto-heal must stop after 2 tries
    Q._set(j1, "intake", "dead", "RuntimeError: ffmpeg failed: concat")
    Q._HEAL_GAP = 0
    healed = Q.auto_heal()
    if n <= 2:
        check("auto-heal attempt %d re-opens a retry-able failure" % n, healed == 1 and Q._row(j1, "intake")["status"] == "ready")
    else:
        check("auto-heal attempt %d does NOT (limit 2, then a human)" % n, healed == 0 and Q._row(j1, "intake")["status"] == "dead")
Q._set(j1, "intake", "dead", 'near-duplicate of an already used topic: "x"')
check("a failure a retry cannot fix is never auto-retried", Q.auto_heal() == 0)

print("== E. GRACE: a file problem is TECHNICAL - it must never send the job back to SOPHIE ==")
src = (ROOT / "scripts" / "department.py").read_text(encoding="utf-8")
check("GRACE raises Fatal('technical: ...') for file/audio/track defects", 'raise Fatal("technical: "' in src)
check("final_content finds final.mp4 itself", 'fin = Path(rp) / "final.mp4"' in src)
check("NOAH writes meta.json + caption.txt for Wan videos", "_write_video_meta(job, plan, final.parent)" in src)
check("HELENA prints ERROR / ACTION / RETRY", '"    ACTION: %s"' in src and '"    RETRY : %s' in src)

print("== F. AI priority: background AI yields to the working agent ==")
import ai_priority as P
P.DB = Path(DB.DB)                                   # test the logic on the scratch db, not the live one
Q.schema().execute("INSERT OR IGNORE INTO department_steps(job_id,step_key,agent_key,stage,status,started,heartbeat) VALUES('jx','noah','noah',4,'running',?,?)", (time.time(), time.time()))
P._CACHE["t"] = 0
check("a running job step makes background AI stand down", P.chain_busy() is True and P.background_ok() is False)
Q.schema().execute("DELETE FROM department_steps WHERE job_id='jx'")
P._CACHE["t"] = 0
check("when nothing runs, background AI is allowed", P.background_ok() is True)

print("== G. ARJUN / side desk never hamper the process flow ==")
import desk_work as W
W.STATE = _TMP / "desk_state.json"
ran = []
W.TASKS = [("arjun", "topic_scout", "scouting fresh topics", 300, lambda: (ran.append(1), "ok")[1])]
Q.schema().execute("INSERT OR IGNORE INTO department_steps(job_id,step_key,agent_key,stage,status,started,heartbeat) VALUES('jy','noah','noah',4,'running',?,?)", (time.time(), time.time()))
P._CACHE["t"] = 0
check("a job step is running -> ARJUN does NOT start", W.run_due() is None and not ran)
Q.schema().execute("DELETE FROM department_steps WHERE job_id='jy'")
P._CACHE["t"] = 0
r = W.run_due()
check("everything idle -> ARJUN works", r is not None and len(ran) == 1, str(r))
check("and not again before 5 minutes pass", W.run_due() is None and len(ran) == 1)
check("after 5 minutes he works again", W.run_due(now=time.time() + 301) is not None and len(ran) == 2)

print("== H. FACT GROUNDING: invented facts can never be published again ==")
import fact_grounding as FG
topic = "What is the longest lived single-cell organism?"
none = {"mode": "no_sources", "source_text": "", "verified_claims": []}
bad = FG.grounding_check("The longest-lived single-cell organism is a bacterium called Thiocapsa roseus, living about 150 years.", none, topic)
check("the real bug text is flagged (invented name + number)", "Thiocapsa" in bad and "150" in bad, str(bad))
bad2 = FG.grounding_check("Methuselah, a tiny marine microbe, survives over 1,000 years.", none, topic)
check("the first invented version is flagged too", bool(bad2), str(bad2))
check("no-source mode: a general reflective post passes", FG.grounding_check("Some of the smallest living things quietly outlast almost everything around them.", none, topic) == [])
src = {"mode": "grounded", "source_text": "Great Basin bristlecone pine trees can live more than 5,000 years in the White Mountains of California.",
       "verified_claims": [{"claim": "Great Basin bristlecone pine trees can live more than 5,000 years in the White Mountains of California.", "source": "x"}]}
check("grounded mode: a sentence from the source passes", FG.grounding_check("Bristlecone pine trees can live more than 5,000 years in the White Mountains of California.", src, "oldest trees") == [])
check("grounded mode: a number NOT in the source is flagged", "9,000" in FG.grounding_check("Bristlecone pine trees can live 9,000 years.", src, "oldest trees"))
check("writers receive the rule (no-source prompt forbids facts)", "NO factual claims" in FG.facts_prompt(none))
src_txt = (ROOT / "scripts" / "department.py").read_text(encoding="utf-8")
check("OLIVER audits grounding and names SOPHIE as the owner", 'owner=("sophie"' in src_txt and "grounding_check" in src_txt)
check("NOAH publishes the approved text, not a fresh rewrite", "_apply_approved_text(job, rp)" in src_txt)
check("GRACE rejects text that is not the approved text", "the generated text is not the approved text" in src_txt)

bad = [r for r in RESULTS if not r[1]]
print("\n%d/%d passed" % (len(RESULTS) - len(bad), len(RESULTS)))
sys.exit(1 if bad else 0)
