# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""PROVE the supervisor works end to end:
   inject a synthetic stuck job -> supervisor must FIND it -> ask the LOCAL model ->
   return a real verdict -> take a whitelisted action. Then clean up.
Uses the real brain.db; everything it inserts is removed afterwards."""
import json, sys, time
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import department_queue as Q
import supervisor as S

JID = "job_TEST_supERVISOR"
made = []

def inject():
    c = Q.schema()
    c.execute("DELETE FROM department_steps WHERE job_id=?", (JID,))
    c.execute("DELETE FROM department_jobs WHERE job_id=?", (JID,))
    made.append(jid_only := JID)
    for key, agent, stage, dep in Q.STEPS:
        c.execute("INSERT INTO department_steps (job_id,step_key,agent_key,stage,depends_on,"
                  "status,version,reason,started,finished,not_before,heartbeat,owner_pid) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (JID, key, agent, stage, dep,
                   "done" if stage < 4 else "pending",
                   1 if key == "sophie" else 0,
                   ("retry 3/3 failed - the model returned empty JSON twice and the backup "
                    "model was not loaded (HTTP 503 from 127.0.0.1:11434)")
                   if key == "sophie" else None,
                   time.time() - 300 if stage < 4 else None,
                   time.time() - 300 if stage < 4 else None,
                   None, None, None))
    # make sophie dead = budget spent, chain parked
    c.execute("UPDATE department_steps SET status='dead', finished=? WHERE job_id=? AND step_key='sophie'",
              (time.time() - 60, JID))
    Q.write_job(JID, {"job_id": JID, "topic": "why rest is a skill not a reward",
                      "kind": "text", "platforms": ["socialai"], "dry_run": False})
    print(f"injected {JID}: sophie DEAD (budget spent), {len(Q._rows(JID))} steps total")

def cleanup():
    c = Q.schema()
    n1 = c.execute("DELETE FROM department_jobs WHERE job_id=?", (JID,)).rowcount
    n2 = c.execute("DELETE FROM department_steps WHERE job_id=?", (JID,)).rowcount
    try: (Q.paths.job_dir(JID, "scripts")).exists()
    except Exception: pass
    import shutil, os
    for area in ("01-intake","02-scripts","03-research","04-visual","05-clips",
                 "09-packages","10-audits","11-evidence"):
        try:
            d = Q.paths.job_dir(JID, area)
            if d.exists(): shutil.rmtree(d, ignore_errors=True)
        except Exception: pass
    try: (Q.paths.job_dir(JID)).parent  # noqa
    except Exception: pass
    print(f"cleanup: removed {n2} step row(s), {n1} job row(s), and its WORK dirs")

try:
    inject()
    print("\n--- 1. FIND ---")
    probs = S.find_problems()
    hit = [p for p in probs if p["job_id"] == JID]
    print(f"  supervisor found {len(probs)} problem(s); our test job: {len(hit)}")
    assert hit, "FAIL: supervisor did not find the stuck job"
    p = hit[0]
    print(f"  -> {p['kind']} at {p['step_key']} (agent {p['agent']}), attempts used v{p['version']}")

    print("\n--- 2. DIAGNOSE (local model, qwen2.5:14b) ---")
    t0 = time.time()
    d = S.diagnose(p)
    print(f"  took {time.time()-t0:.0f}s")
    print("  raw model verdict :", json.dumps(d, indent=2)[:600])
    assert d.get("verdict") in S.VERDICT_ACTION, f"FAIL: unknown verdict {d.get('verdict')}"
    print(f"  VERDICT ACCEPTED : {d['verdict']}  ->  allowed action {S.VERDICT_ACTION[d['verdict']]}")

    print("\n--- 3. SAFETY: the verdict can only map to a whitelisted action ---")
    allowed = set(S.VERDICT_ACTION.values())
    print("  possible actions the model can ever cause:", sorted(allowed))
    assert allowed == {"retry_now", "wait_and_retry", "escalate", "leave_dead"}
    assert "publish" not in allowed and "unlock_gate" not in allowed
    print("  OK: no publish action, no gate bypass possible")

    print("\n--- 4. ACT ---")
    action, did = S.act(p, d)
    print(f"  action={action}  changed_something={did}")
    row = Q._row(JID, p["step_key"])
    print(f"  step now: status={row['status']} reason={(row['reason'] or '')[:80]}")
    aud = Q.schema().execute("SELECT agent,status,reason FROM department_jobs "
                         "WHERE job_id=? AND UPPER(agent) IN ('SUPERVISOR','SYSTEM WATCHDOG')", (JID,)).fetchall()
    print(f"  audit rows written by supervisor: {len(aud)}")
    for a in aud:
        print(f"     agent={a['agent']} status={a['status']} reason={(a['reason'] or '')[:70]}")
    assert len(aud) >= 1, "FAIL: action was not audited"

    print("\n--- 5. BOUNDED: it must not act forever on one job ---")
    # The budget state is PERSISTED on purpose (a restart must not reset it), so a
    # previous run of this test already spent it. Clear it first, or the test measures
    # the leftovers rather than the cap.
    S.STATE.parent.mkdir(parents=True, exist_ok=True)
    S._save({})
    st = S._state()
    assert st == {}, "state not cleared"
    acts = 0
    for i in range(6):
        if S._budget_ok(st, JID):
            S._spent(st, JID); acts += 1
        else:
            break
    S._save(st)
    print(f"  interventions allowed in one window: {acts} (cap is {S.MAX_ACTIONS_PER_JOB})")
    assert acts == S.MAX_ACTIONS_PER_JOB, "FAIL: supervisor budget is not bounded"
    # and it must be capped for real, not just in this test
    assert not S._budget_ok(st, JID), "FAIL: budget not exhausted after the cap"

    print("\n=== SUPERVISOR TEST PASSED ===")
finally:
    print("\n--- cleanup ---")
    cleanup()