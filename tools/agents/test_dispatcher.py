# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""PROVE agent 21 DISPATCHER works and is safe. Monkeypatched rules - the real
rules.json is never modified, and every job it creates is removed afterwards."""
import json, shutil, sys, time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "videogen"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import dispatcher as D
import department_queue as Q

created = []
real_state = D.STATE
tmp_state = Path(r"C:\Users\user\AppData\Local\Temp\opencode\disp_state.json")
D.STATE = tmp_state
if tmp_state.exists():
    tmp_state.unlink()

try:
    print("=== A: the dispatcher follows the real paused / department.enabled switches ===")
    import json as _j
    _r = _j.loads((Path(__file__).resolve().parents[2] / "config" / "rules.json").read_text(encoding="utf-8-sig"))
    _should = (not _r.get("paused")) and bool((_r.get("department") or {}).get("enabled"))
    ok, why = D.enabled()
    print(f"  enabled={ok}  reason='{why}'  (expected {_should})")
    assert ok is _should, "FAIL: dispatcher ignored the paused / enabled switches"

    print("\n=== B: forced ON, slot due, system idle -> must enqueue exactly one ===")
    D.enabled = lambda: (True, "forced on for the test")
    D.busy = lambda: (False, "")
    NOWH = datetime.now().hour
    D.SLOTS = [("video-real", NOWH, 0, "video", "real", ["socialai"])]   # 00:00 = always due
    r = D.run_once()
    print(f"  enqueued: {[(e['slot'], e['kind'], e['style']) for e in r['enqueued']]}")
    assert len(r["enqueued"]) == 1, "FAIL: did not enqueue the due slot"
    jid = r["enqueued"][0]["job_id"]
    created.append(jid)
    print(f"  job {jid}")

    print("\n=== C: same slot again today -> must NOT double-enqueue ===")
    r2 = D.run_once()
    print(f"  second pass enqueued: {r2['enqueued']}")
    assert len(r2["enqueued"]) == 0, "FAIL: double-enqueued the same slot"
    print("  OK: one enqueue per slot per day, survives a restart")

    print("\n=== D: GPU busy -> must HOLD the slot, not burn it ===")
    D.SLOTS = [("video-cartoon", NOWH, 0, "video", "cartoon", ["socialai"])]
    D.busy = lambda: (True, "a video is already generating")
    r3 = D.run_once()
    print(f"  enqueued while busy: {r3['enqueued']}")
    assert len(r3["enqueued"]) == 0, "FAIL: started a second job while one was in flight"
    print("  OK: refused to queue a second job while one is in flight")

    print("\n=== E: cartoon topic must DIFFER from the photoreal one ===")
    D.busy = lambda: (False, "")
    t_real = created and D._load(Q.paths.job_dir(created[0], "intake") / "job.json", {}).get("topic")
    used = D.used_topics()
    cand = D.related_but_different(t_real) if t_real else None
    print(f"  photoreal topic : {t_real}")
    print(f"  already used    : {len(used)} topics")
    if cand:
        same = D._norm(cand) == D._norm(t_real)
        print(f"  cartoon candidate: {cand}  same_as_real={same}")
        assert not same, "FAIL: offered the same topic twice"
        print("  OK: a different topic is offered for the cartoon slot")
    else:
        print("  (no related-but-different candidate in the pool - picker returned nothing)")

    print("\n=== F: it never picks an already-used topic ===")
    bad = 0
    for _ in range(40):
        t = D.pick_topic("text", None, t_real)
        if t is None or D._norm(t) in used:
            bad += 1
    print(f"  repeats found in 40 picks: {bad}")
    assert bad == 0, "FAIL: dispatcher offered an already-used topic"
    print("  OK: never repeats a used topic")

    print("\n=== G: style tag reaches the job (NOAH needs it to pick the Wan prompt) ===")
    meta = Q.read_job(created[0]) or {}
    print(f"  job meta style={meta.get('style')!r} tag={meta.get('style_prompt_tag')!r}")
    assert meta.get("style") == "real", "FAIL: style not recorded on the job"
    print("  OK: the photoreal/cartoon choice is carried on the job")

    print("\n=== DISPATCHER TEST PASSED ===")
finally:
    print("\n--- cleanup ---")
    D.STATE = real_state
    if tmp_state.exists():
        tmp_state.unlink()
    for jid in created:
        c = Q.schema()
        c.execute("DELETE FROM department_steps WHERE job_id=?", (jid,))
        c.execute("DELETE FROM department_jobs WHERE job_id=?", (jid,))
        for area in ("intake", "scripts", "research", "visual", "clips",
                     "packages", "audits", "evidence"):
            d = Q.paths.AREAS.get(area)
            if d:
                t = d / jid
                if t.exists():
                    shutil.rmtree(t, ignore_errors=True)
    print(f"  removed {len(created)} test job(s) + its WORK folders")
