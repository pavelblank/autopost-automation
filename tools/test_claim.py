# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Prove the slot-claim fix: a slot can only be claimed once, and a broken state
file refuses to post rather than re-firing every slot.

Also reports what is actually in flight, because a missing claim file plus a running
video is how a pile of backlog posts gets published at once.
"""
import os
import sys
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
import db            # noqa: E402
import dispatcher as D  # noqa: E402

ok = True


def check(label, cond, extra=""):
    global ok
    if not cond:
        ok = False
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label, ("  " + extra) if extra else ""))


print("=== only one definition of each helper ===")
import inspect
src = inspect.getsource(D)
for fn in ("def claim", "def done_today", "def _save"):
    n = src.count("\n" + fn)
    check("%s defined once" % fn.strip(), n == 1, "(found %d)" % n)

print("\n=== claim is exclusive ===")
# NEVER touch the LIVE claim file (data/dispatcher.json): the running scheduler reads it every few seconds, and
# a corrupt / missing file there made it re-fire a slot (2026-10-11 13:53). Work on a private temp copy.
import tempfile
from pathlib import Path as _P
D.STATE = _P(tempfile.mkdtemp(prefix="claimtest_")) / "dispatcher.json"
STATE = D.STATE
backup = None

first = D.claim("unit-test-slot")
check("first claim succeeds", first is True)
check("claim file now exists", STATE.exists())
check("second claim of the same slot is REFUSED", D.claim("unit-test-slot") is False)
check("done_today sees it", D.done_today("unit-test-slot") is True)
check("a different slot can still claim", D.claim("unit-test-slot-2") is True)

print("\n=== a corrupt state file fails CLOSED (no re-fire) ===")
STATE.write_text("{ this is not json", encoding="utf-8")
check("done_today returns True (do not repost)", D.done_today("unit-test-slot") is True)
check("claim refuses", D.claim("unit-test-slot") is False)
os.remove(STATE)
# A MISSING file legitimately means "nothing has fired today", so claiming IS allowed.
# Failing closed here would mean the dispatcher could never start again after a clean.
check("missing file still allows a claim", D.claim("unit-test-slot") is True)
check("  and it is then remembered", D.done_today("unit-test-slot") is True)

# restore
if STATE.exists():
    os.remove(STATE)
if backup is not None:
    STATE.write_text(backup, encoding="utf-8")
    print("\n  previous claim state restored")
else:
    print("\n  no previous claim state existed (slots are unclaimed today)")

print("\n=== what is actually in flight right now ===")
c = db.conn()
r = c.execute("SELECT COUNT(*) n FROM runs WHERE status='running' "
              "OR (finished IS NULL AND started > ?)", (time.time() - 3 * 3600,)).fetchone()
print("  runs in flight:", r["n"])
try:
    import department_queue as Q
    s = Q.schema()
    for row in s.execute("SELECT job_id, step_key, status FROM department_steps "
                         "WHERE status IN ('running','ready') LIMIT 6"):
        print("  step: %s / %s / %s" % (row["job_id"], row["step_key"], row["status"]))
    for row in s.execute("SELECT job_id, kind, status, source, created FROM department_jobs "
                         "ORDER BY created DESC LIMIT 8"):
        print("  job: %-28s %-6s %-9s %-11s %s"
              % (row["job_id"], row["kind"], row["status"], row["source"], row["created"]))
except Exception as e:
    print("  department:", e)

b, why = D.busy()
print("\n  dispatcher busy():", b, "|", why)
print("\nRESULT:", "ALL PASS" if ok else "SOMETHING FAILED")