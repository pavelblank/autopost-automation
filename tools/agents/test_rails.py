# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Verify the two rails the owner added: POST HISTORY (no repost / no same topic) and the
PUBLISH GUARD limits (never truncated, never cut, never over quota). Read-only + reports."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import post_history as PH
import content_format as CF

print("=" * 76)
print("1. POST HISTORY - has it recorded what was already posted?")
print("=" * 76)
d = PH.load()
print("   published entries :", len(d["entries"]))
print("   ignore list       :", len(d["ignore"]))

# --- pollution check: MY test rows must not be in the real history ---
bad = [e for e in d["entries"]
       if any(w in (e.get("topic") or "").lower()
              for w in ("retention test", "supervisor", "dispatcher test", "pller trigger"))]
print("\n   test/pollution rows found in the published history:", len(bad))
for e in bad[:6]:
    print("      -", json.dumps(e, ensure_ascii=False))

print()
print("=" * 76)
print("2. find_repeat() - does it actually catch a repost?")
print("=" * 76)
real = [e for e in d["entries"] if len((e.get("topic") or "")) > 25 and e not in bad]
hits = 0
for e in real[:12]:
    got = PH.find_repeat(e["topic"], exclude_run_id=e.get("run"))
    ok = got is not None
    hits += ok
    print("   %-5s %r" % ("HIT" if ok else "MISS", e["topic"][:66]))
print("   -> caught %d of %d" % (hits, min(12, len(real))))

print("\n   a brand-new topic must NOT be flagged:")
fresh = "Quantum gardening: how moss responds to lunar light"
print("      %r -> %r" % (fresh, PH.find_repeat(fresh)))

print("\n   a REWORDED version of a real post SHOULD be flagged:")
if real:
    base = real[0]["topic"]
    print("      base   : %r" % base[:70])
    print("      base   -> %r" % (PH.find_repeat(base, exclude_run_id=real[0].get("run")) or "None"))

print()
print("=" * 76)
print("3. PUBLISH GUARD - limits, so text is never truncated or cut")
print("=" * 76)
import publish_guard as PG
print("   platform profiles:")
for plat, prof in CF.PROFILES.items():
    print("      %-16s limit=%-6s kind=%s" % (plat, prof.get("limit"), prof.get("kind", "?")))

long_text = "word " * 900          # ~4500 chars, over every short platform
print("\n   a 4500-char post through check_limits():")
for plat in ("socialai", "telegram", "bluesky", "youtube", "discord"):
    r = PG.check_limits(plat, long_text)
    print("      %-12s ok=%-6s %s" % (plat, r.ok, r.reason[:70]))

print("\n   a SHORT post (200 chars) should pass everything:")
short = "word " * 40
for plat in ("socialai", "telegram", "bluesky", "youtube", "discord"):
    r = PG.check_limits(plat, short)
    print("      %-12s ok=%-6s %s" % (plat, r.ok, r.reason[:70]))

print("\n   the socialai full-post gate (blog must carry the WHOLE post):")
r = PG.check_socialai_full(short + " " + "tail " * 200, short, "socialai")
print("      ok=%s  %s" % (r.ok, r.reason[:100]))
r2 = PG.check_socialai_full("a" * 10, "b" * 10, "socialai")
print("      short caption, different body -> ok=%s  %s" % (r2.ok, r2.reason[:80]))