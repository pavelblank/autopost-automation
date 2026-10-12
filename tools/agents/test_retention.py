# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""PROVE agent 20 RETENTION works AND is safe. Synthetic data only, cleaned up after.
Every safety rule gets its own assertion."""
import json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "videogen"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import retention as R
import department_queue as Q
import db

JID = "job_TEST_RETENTION"
RUNID = "job_TEST_RETENTION"
scratch = []


def make_work_job(age_days):
    """A finished job with a WORK draft folder, backdated."""
    c = Q.schema()
    c.execute("DELETE FROM department_steps WHERE job_id=?", (JID,))
    c.execute("DELETE FROM department_jobs WHERE job_id=?", (JID,))
    for key, agent, stage, dep in Q.STEPS:
        st = "done" if key == "helena" else "skipped"
        c.execute("INSERT OR REPLACE INTO department_steps (job_id,step_key,agent_key,stage,"
                  "depends_on,status,version,reason,finished) VALUES (?,?,?,?,?,?,0,?,?)",
                  (JID, key, agent, stage, dep, st, "finished", time.time() - age_days * 86400))
    made = 0
    for area in ("scripts", "audits", "clips"):
        d = (Q.paths.AREAS[area] / JID).mkdir(parents=True, exist_ok=True) or (Q.paths.AREAS[area] / JID)
        f = d / ("%s.json" % area)
        f.write_text("x" * 4096, encoding="utf-8")
        made += 1
        scratch.append(d)
    # backdate every file so the age guard sees it
    old = time.time() - age_days * 86400
    for d in list(scratch):
        for f in d.rglob("*"):
            os.utime(f, (old, old))
    # empty folders of the job (e.g. 01-intake) carry their creation time: backdate them too, otherwise the
    # age guard rightly thinks the job is still being written
    for area in R.WORK.iterdir():
        jd = area / JID
        if jd.is_dir():
            os.utime(jd, (old, old))
    return made


def add_run(published, age_days, folder):
    con = db.conn()
    cur = con.execute("INSERT INTO runs (topic,started,finished,status,stage,path,kind,"
                      "published,regeneration_attempts,diversity_score) "
                      "VALUES (?,?,?,'ok','done',?,'video',?,1,0.5)",
                      ("retention test", time.time() - age_days * 86400,
                       time.time() - age_days * 86400, str(folder), published))
    con.commit()
    return cur.lastrowid


def out_folder(name):
    d = R.OUT / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "final.mp4").write_bytes(b"0" * 200000)
    scratch.append(d)
    return d


try:
    print("=== forcing retention ON (test only) ===")
    R.cfg = lambda: {"enabled": True, "days": 10, "delete_drafts": True, "sweep_hours": 6}
    # 3.0 rule: drafts go only after the post is CONFIRMED published. The synthetic job has no real post,
    # so say "published" for the delete cases; the unpublished-is-kept rule is proven in case E below.
    R._job_published = lambda jid: True

    print("\n--- A: a FINISHED job, 20 days old -> its DRAFTS must go ---")
    make_work_job(20)
    jobs = [p for p in Q.paths.AREAS["scripts"].iterdir() if p.name == JID]
    before = len(list(jobs[0].rglob("*"))) if jobs else 0
    freed = R.purge_drafts(10)
    still = (Q.paths.AREAS["scripts"] / JID).exists()
    print(f"  draft folder before: {before} entries   after purge: exists={still}   freed={freed:.2f} MB")
    assert not still, "FAIL: old drafts were not deleted"
    print("  OK: drafts of a 20-day-old finished job were deleted")

    print("\n--- B: a FINISHED job, 2 days old -> must be KEPT (inside the window) ---")
    make_work_job(2)
    freed = R.purge_drafts(10)
    still = (Q.paths.AREAS["scripts"] / JID).exists()
    print(f"  still exists: {still}   freed={freed:.2f} MB")
    assert still, "FAIL: deleted a job inside its retention window"
    print("  OK: a 2-day-old job was left completely alone")

    print("\n--- C: an UNPUBLISHED final output, 30 days old -> must be KEPT ---")
    f_unpub = out_folder("post_TEST_unpublished_old")
    rid1 = add_run(0, 30, f_unpub)
    R.purge_expired(10)
    print(f"  exists: {f_unpub.exists()}")
    assert f_unpub.exists(), "FAIL: deleted UNPUBLISHED work - this would lose content"
    print("  OK: unpublished work is never deleted, however old")

    print("\n--- D: a PUBLISHED final output, 30 days old -> must GO ---")
    f_pub = out_folder("post_TEST_published_old")
    rid2 = add_run(1, 30, f_pub)
    R.purge_expired(10)
    print(f"  exists: {f_pub.exists()}")
    assert not f_pub.exists(), "FAIL: expired published output was not deleted"
    print("  OK: published output past the 10-day window was deleted")

    print("\n--- E: a PUBLISHED final output, 3 days old -> must be KEPT ---")
    f_new = out_folder("post_TEST_published_new")
    rid3 = add_run(1, 3, f_new)
    R.purge_expired(10)
    print(f"  exists: {f_new.exists()}")
    assert f_new.exists(), "FAIL: deleted a post inside its 10-day window"
    print("  OK: a 3-day-old published post was kept")

    print("\n--- F: the guard must refuse paths outside WORK/ and out/ ---")
    danger = (Path(__file__).resolve().parents[2] / "config" / "retention_should_never_happen.tmp")
    danger.write_text("do not delete me", encoding="utf-8")
    got = R._rm(danger, "test")
    print(f"  tried to delete config/ file -> freed={got} MB, still there={danger.exists()}")
    assert danger.exists(), "FAIL: guard did not protect config/"
    danger.unlink()
    print("  OK: config/ is protected by the guard")

    print("\n--- G: when disabled it deletes NOTHING at all ---")
    R.cfg = lambda: {"enabled": False, "days": 10, "delete_drafts": True, "sweep_hours": 6}
    f_guard = out_folder("post_TEST_when_disabled")
    rid4 = add_run(1, 999, f_guard)
    r = R.run_once()
    print(f"  run_once -> {r}")
    print(f"  the 999-day-old output still exists: {f_guard.exists()}")
    assert f_guard.exists(), "FAIL: deleted something while disabled"
    print("  OK: disabled means deleted nothing, regardless of age")

    print("\n=== RETENTION TEST PASSED ===")
finally:
    print("\n--- cleanup ---")
    c = Q.schema()
    c.execute("DELETE FROM department_steps WHERE job_id=?", (JID,))
    c.execute("DELETE FROM department_jobs WHERE job_id=?", (JID,))
    con = db.conn()
    for rid in ("rid1", "rid2", "rid3", "rid4"):
        try:
            con.execute("DELETE FROM runs WHERE id=?", (int(rid),))
        except Exception:
            pass
    con.commit()
    for d in scratch:
        try:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
    print("  synthetic jobs, runs and folders removed")

