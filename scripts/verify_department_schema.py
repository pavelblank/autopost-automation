# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Verify the department schema applies cleanly to a scratch DB, and report."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import db as real_db

scratch = Path(__file__).resolve().parent.parent / "data" / "_schema_check.db"
if scratch.exists():
    scratch.unlink()

c = sqlite3.connect(scratch, timeout=30)
c.row_factory = sqlite3.Row
c.execute("PRAGMA journal_mode=WAL")
c.executescript(real_db.SCHEMA)

want = ["runs", "posts", "topics_used", "audit_logs", "gen_lease",
        "department_jobs", "department_packages", "live_audits",
        "remediation", "final_reports"]
have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}

print("schema applied OK")
ok = True
for t in want:
    mark = "ok " if t in have else "MISSING"
    if t not in have:
        ok = False
    print("  %-8s %s" % (mark, t))

print("\ndepartment_jobs columns:", [r[1] for r in c.execute("PRAGMA table_info(department_jobs)")])
print("pre-existing runs columns still present:",
      [r[1] for r in c.execute("PRAGMA table_info(runs)")])
c.close()
for suffix in ("", "-wal", "-shm"):
    p = Path(str(scratch) + suffix)
    if p.exists():
        p.unlink()
print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
