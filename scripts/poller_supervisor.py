# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Keeps the department poller (department_queue.py --watch) running. HIDDEN, one instance only.

Replaces START-POLLER.bat for automatic starts: that batch file ran in a
cmd.exe, and every child it started (python, timeout) opened its own visible console window - a window
flashing every few seconds. This runs under pythonw (no console) and starts the poller with
CREATE_NO_WINDOW, so nothing is ever visible.

 * single instance: data/poller_supervisor.pid (a live pid means "already supervised" -> exit)
 * poller exits 0 or 2 (stopped on purpose / another copy already running) -> supervisor ends
 * poller crashes (any other code) -> wait, then start it again; the wait grows to 60 s so a
   permanently broken poller can never become a busy loop
 * automation paused or department off -> supervisor ends (the guard starts it again when it is switched on)
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nowindow  # noqa: E402,F401
import subprocess  # noqa: E402

ROOT = (Path(__file__).resolve().parents[1])
PY = ROOT / "venv" / "Scripts" / "python.exe"
POLLER = ROOT / "scripts" / "department_queue.py"
PIDFILE = ROOT / "data" / "poller_supervisor.pid"
LOGF = Path(__file__).resolve().parents[1] / "logs" / "poller.log"


def log(m):
    try:
        with LOGF.open("a", encoding="utf-8") as f:
            f.write("[%s] supervisor: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), m))
    except Exception:
        pass


def automation_on():
    try:
        R = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig"))
        return (not R.get("paused")) and bool((R.get("department") or {}).get("enabled"))
    except Exception:
        return True


def main():
    try:
        old = int(PIDFILE.read_text().strip())
        if old != os.getpid() and nowindow.pid_alive(old):
            return 0
    except Exception:
        pass
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))
    log("started (pid %d)" % os.getpid())
    wait = 5
    while True:
        if not automation_on():
            log("automation is paused / department off - supervisor ends")
            return 0
        t0 = time.time()
        with LOGF.open("a", encoding="utf-8") as out:
            rc = subprocess.call([str(PY), "-I", str(POLLER), "--watch", "--interval", "3"],
                                 cwd=str(ROOT), stdout=out, stderr=out)
        log("poller exited rc=%s after %ds" % (rc, time.time() - t0))
        if rc in (0, 2):
            return 0
        wait = 5 if time.time() - t0 > 120 else min(60, wait * 2)      # a healthy long run resets the back-off
        time.sleep(wait)


if __name__ == "__main__":
    sys.exit(main())
