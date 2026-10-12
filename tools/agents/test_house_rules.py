# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""the owner's publishing rules: no 'the owner', no AI attribution, no stack of AI-sounding clichés."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import publish_guard as PG

cases = [
    ("clean human post",            "Bees can recognise human faces. A short note from Mr Blank.", False),
    ("topic about AI is fine",      "Why artificial intelligence still struggles with common sense.", False),
    ("one stock phrase tolerated",  "A seamless commute starts with a good alarm.", False),
    ("names the owner",                 "Thanks for reading, the owner", True),
    ("names the owner in title",        "ok", True),
    ("written by AI",               "This article was written by AI for you.", True),
    ("AI-generated",                "An AI-generated summary of the week.", True),
    ("as an AI",                    "As an AI I cannot say.", True),
    ("stack of tells",              "In today's fast-paced world this transformative tool lets you leverage a seamless workflow.", True),
]
bad = 0
for name, text, want_fail in cases:
    title = "the owner's notes" if name == "names the owner in title" else ""
    s = PG.check_house_rules(text, title)
    ok = (not s.ok) == want_fail
    bad += (not ok)
    print("%-6s %-28s -> %s %s" % ("OK" if ok else "WRONG", name, "BLOCK" if not s.ok else "pass", s.reason[:60]))
print("PASSED" if not bad else "FAILED %d" % bad)
sys.exit(1 if bad else 0)
