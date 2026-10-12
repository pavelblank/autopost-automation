# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""The ONE place the old triggers (scheduler tick, dashboard buttons) hand a job to the
department. rules.json -> department.enabled is the master switch: false (today) means this
module does nothing and the old path runs unchanged."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))
POLLER_BAT = Path(__file__).resolve().parents[1] / "START-POLLER.bat"


def enabled():
    try:
        r = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig"))
        return bool((r.get("department") or {}).get("enabled"))
    except Exception:
        return False


def ensure_poller():
    """Start the trigger poller (detached, hidden) if it is not already running."""
    import department_queue as Q
    if Q._watch_alive():
        return True
    try:
        subprocess.Popen(["cmd.exe", "/c", str(POLLER_BAT)], cwd=str(ROOT),
                         creationflags=0x00000008 | 0x00000200 | 0x08000000)   # detached, new group, no window
        return True
    except Exception:
        return False


def submit(topic, kind, platforms=None, source="trigger", start_poller=True):
    """Queue a job for the department. Returns the job_id, or None if it could not be queued."""
    try:
        import department_queue as Q
        Q.schema()
        jid = Q.enqueue(topic, "video" if kind == "video" else ("image" if kind == "image" else "text"),
                        list(platforms or ["socialai"]), notify=True, source=source)
        if start_poller:
            ensure_poller()
        return jid
    except Exception:
        return None
