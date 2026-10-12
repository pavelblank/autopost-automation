# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""WHAT WENT WRONG AND WHAT TO DO - one place that turns a stopped step into plain words.

Every dead-lettered step gets: a short ERROR title, the ACTION the owner should take (or "none -
the system handles it"), and the exact one-line RETRY command. It is written next to the job
(`job_error.json`), shown in HELENA's final report / Telegram message and in the Work chat.
Retrying always re-opens the SAME job (never a duplicate) and, for video, reuses every clip that
was already rendered with the same prompt - a retry costs seconds, not another render.
"""
import json
import re
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])

# (regex on the failure text, error title, action for the owner, can a plain retry fix it?)
PATTERNS = [
    (r"near-duplicate|already used topic|duplicate topic",
     "Topic already used (too close to something posted before)",
     "None - the SUPERVISOR re-checks it with facts: a false alarm is re-opened, a real duplicate is closed and the topic pickers choose another one by themselves.", False),
    (r"image service|solid-color|ALL image sources down|402",
     "The image service is out of credit or down",
     "None needed now - the system waits and retries by itself.", True),
    (r"ffmpeg failed|assembl|concat",
     "Video assembly (ffmpeg) failed",
     "None - the system retries it by itself; finished clips are reused.", True),
    (r"voice|edge.?tts|no voice engine",
     "The voice-over could not be made",
     "None - the system retries it, then falls back to the offline Windows voice.", True),
    (r"lease|another generation",
     "The GPU was busy with another job",
     "None - it retries by itself as soon as the other job ends.", True),
    (r"hard timeout|exceeded .*s and was killed|timed out",
     "A step ran past its time limit",
     "None - the system retries it by itself (finished clips are reused).", True),
    (r"recorded no run|generator failed|generator exited",
     "The video generator did not finish cleanly",
     "None - the system retries it by itself; finished clips are reused.", True),
    (r"technical:",
     "The finished file failed an inspection (file/audio/video problem)",
     "None - the system rebuilds the file by itself. The script is NOT rewritten and the video is NOT re-rendered.", True),
    (r"human rejected",
     "You rejected this post on the dashboard",
     "Nothing to do - it was stopped on purpose.", False),
    (r"no destination|allowed_platforms|not permitted",
     "No allowed destination for this post",
     "Open Settings and allow at least one connected platform for this kind of post.", False),
]


def diagnose(step_key, reason):
    text = str(reason or "")
    for rx, title, action, retry in PATTERNS:
        if re.search(rx, text, re.I):
            return {"error": title, "action": action, "retry_ok": retry}
    return {"error": "Step %s stopped: %s" % (step_key.upper(), text[:140] or "unknown"),
            "action": "None for now - the system retries it. You are told separately only if it cannot recover.",
            "retry_ok": True}


def retry_command(job_id, step_key=None):
    return r"venv\Scripts\python.exe scripts\department_queue.py --retry %s%s" % (
        job_id, (" --step " + step_key) if step_key else "")


def describe(job_id, step_key, reason):
    d = diagnose(step_key, reason)
    d.update(job=job_id, step=step_key, reason=str(reason or "")[:400],
             retry=retry_command(job_id, step_key) if d["retry_ok"] else None, ts=time.time())
    return d


def write_job_error(job_id, step_key, reason):
    """Persist the diagnosis next to the job so the dashboard / chat / Telegram can all show it."""
    d = describe(job_id, step_key, reason)
    try:
        p = ROOT / "videogen" / "WORK" / "10-audits" / job_id
        p.mkdir(parents=True, exist_ok=True)
        (p / "job_error.json").write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass
    return d
