# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Prove the agent-memory rails: new location, fallback, append-only audit memory,
duplicate detection, and that NOTHING in the cleanup path can delete it."""
import json, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import post_history as PH
import retention as R

print("=" * 74)
print("1. LOCATION")
print("=" * 74)
print("   MEMORY     :", PH.MEMORY)
print("   FILE       :", PH.FILE, "exists =", PH.FILE.exists())
print("   LEGACY     :", PH.LEGACY, "exists =", PH.LEGACY.exists())
print("   _path() ->", PH._path().name)
assert PH.FILE.exists(), "FAIL: history is not in AGENT_MEMORY"

print()
print("=" * 74)
print("2. HISTORY STILL COMPLETE after the move")
print("=" * 74)
PH.refresh(force=True)
d = PH.load()
print("   entries:", len(d["entries"]), " ignore:", len(d["ignore"]))
real = [e for e in d["entries"] if len(e.get("topic") or "") > 25]
hits = sum(1 for e in real[:15] if PH.find_repeat(e["topic"], exclude_run_id=e.get("run")))
print("   duplicates caught: %d of %d" % (hits, min(15, len(real))))
fresh = "Oxytocin release when a dog is adopted from a shelter"
print("   fresh topic flagged? %r (must be None)" % PH.find_repeat(fresh))
assert hits >= 10, "FAIL: duplicate detection degraded after the move"
assert PH.find_repeat(fresh) is None, "FAIL: false positive"

print()
print("=" * 74)
print("3. AUDIT MEMORY - append-only, cheap, readable back")
print("=" * 74)
n0 = len(PH.recent_audits(100000))
for i, (t, v, why) in enumerate([
        ("why the ocean glows at night", "approved", ""),
        ("a brand new topic about moss", "rejected", "already published"),
        ("quantum gardening lunar moss", "approved", "")], 1):
    ok = PH.note_audit(t, v, why, job_id="job_TEST_%d" % i)
    print("   appended #%d ok=%s  %r -> %s" % (i, ok, t[:38], v))
back = PH.recent_audits(3)
print("   recent_audits(3) newest first:")
for r in back:
    print("      %s  %-9s %s" % (r["ts"], r["verdict"], r["topic"][:44]))
n1 = len(PH.recent_audits(100000))
print("   grew %d -> %d (append, nothing rewritten)" % (n0, n1))
assert n1 == n0 + 3, "FAIL: audit memory did not append exactly 3 lines"
assert back[0]["topic"].startswith("quantum"), "FAIL: not newest-first"

print()
print("=" * 74)
print("4. NO CLEANUP PATH CAN DELETE AGENT_MEMORY")
print("=" * 74)
print("   retention only allows deletion inside videogen/WORK/ and out/")
for probe in (PH.MEMORY, PH.FILE, PH.AUDIT_LOG):
    allowed = R._guard(probe)
    print("   guard(%-22s) -> allowed_to_delete=%s   %s"
          % (probe.name, allowed, "OK" if not allowed else "*** DANGEROUS ***"))
    assert not allowed, "FAIL: a cleanup guard would allow deleting agent memory"

# retention forced on, must still refuse
R.cfg = lambda: {"enabled": True, "days": 0, "delete_drafts": True, "sweep_hours": 1}
freed = R.purge_expired(0) + R.purge_drafts(0)
print("   with retention FORCED on and 0-day window: freed %.2f MB" % freed)
print("   AGENT_MEMORY still there:", PH.FILE.exists(), PH.AUDIT_LOG.exists())
assert PH.FILE.exists() and PH.AUDIT_LOG.exists(), "FAIL: cleanup deleted agent memory"

print()
print("=== AGENT MEMORY IS PERSISTENT, VISIBLE AND SAFE ===")
print("files:")
for p in sorted(PH.MEMORY.iterdir()):
    print("   %-24s %8d bytes" % (p.name, p.stat().st_size))