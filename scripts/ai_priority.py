# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""AI PRIORITY - the agent doing the real job always gets the local AI first.

There is one 8 GB GPU. A chain step (MAYA writing the brief, SOPHIE the script, NOAH rendering
video ...) must never queue behind background curiosity such as scoring scraped topics. So every
BACKGROUND caller of the local model asks `background_ok()` first and, if a job step is running,
simply skips its optional AI work (using its plain, no-AI fallback) - no waiting, no competing.

  chain_busy()      True while any department step is running (fresh heartbeat/start)
  background_ok()   True only when no job step needs the AI right now
"""
import sqlite3
import time
from pathlib import Path

DB = (Path(__file__).resolve().parents[1] / "data" / "brain.db")
_CACHE = {"t": 0.0, "v": False}


def chain_busy(max_age=5400):
    """Cached for 3 s so tight loops do not hammer the database."""
    now = time.time()
    if now - _CACHE["t"] < 3:
        return _CACHE["v"]
    v = False
    try:
        c = sqlite3.connect("file:%s?mode=ro" % DB.as_posix(), uri=True, timeout=3)
        n = c.execute("SELECT COUNT(*) FROM department_steps WHERE status='running' AND "
                      "MAX(COALESCE(heartbeat,0), COALESCE(started,0)) > ?", (now - max_age,)).fetchone()[0]
        c.close()
        v = n > 0
    except Exception:
        v = False
    _CACHE.update(t=now, v=v)
    return v


def background_ok():
    return not chain_busy()
