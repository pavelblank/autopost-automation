# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Proves a process started from tools\\ can NEVER message the owner or write the live schedule file.
Safe to run any time: the guards stop both actions before anything is sent or written."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish as P
import dispatcher as D

ok = True
def check(label, cond):
    global ok
    ok = ok and cond
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))

check("this process is recognised as a test", P.started_from_tests() is True)
r = P.telegram_send("fake-token", "0", "guard test - must NOT be sent")      # a fake token would raise if it reached the network
check("telegram_send is blocked (returns 0, no network call)", r == 0)
try:
    D._save({"guard-test": "x"})
    check("writing the LIVE dispatcher.json is refused", False)
except RuntimeError as e:
    check("writing the LIVE dispatcher.json is refused", "refused" in str(e))
print("RESULT:", "ALL PASS" if ok else "SOMETHING FAILED")
