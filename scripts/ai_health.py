# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""AutoPost - ai_health.py
Shared Ollama liveness helper. The old check was "is port 11434 open" - that
misses a wedged-but-listening server (answers /api/tags, hangs generations).
This module probes an actual tiny generation so both the scheduler and the
guard can trust that Ollama really WORKS before any job is fired.

    ollama_ok(timeout=25)  True only if a real generation completes in time
    ollama_models()        list of installed model names, or []
"""
import json, socket, time, urllib.request
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434/api/generate"
TAGS   = "http://127.0.0.1:11434/api/tags"


def _active_model():
    try:
        R = json.loads(((Path(__file__).resolve().parents[1] / "config" / "rules.json")).read_text(encoding="utf-8"))
        return R.get("ollama_model") or "llama3.2:3b"
    except Exception:
        return "llama3.2:3b"


def port_up(port=11434):
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def ollama_models():
    try:
        with urllib.request.urlopen(TAGS, timeout=6) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return []


def ollama_ok(timeout=25, model=None):
    """Real generation probe - the only check worth trusting. Returns OK text
    (or True) on success, None on any failure/hang. The probe prompt is tiny
    and keep_alive short so it never loads a second model: it only confirms
    the ALREADY-LOADED model can still complete an inference."""
    if not port_up():
        return None
    try:
        body = json.dumps({
            "model": model or _active_model(),
            "prompt": "Reply with exactly: OK",
            "stream": False,
            "options": {"num_predict": 2},
            "keep_alive": "2m",
        }).encode()
        req = urllib.request.Request(OLLAMA, data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)["response"].strip()
        if "OK" in resp or len(resp) < 50:
            return resp
        return None
    except Exception:
        return None


def human_status():
    m = ollama_models()
    if not port_up():
        return "OLLAMA DOWN (no listener on 11434)"
    if not m:
        return "OLLAMA PORT OPEN but /api/tags failed - server responding erratically"
    ok = ollama_ok(timeout=10)
    if not ok:
        return "OLLAMA WEDGED - port open, %d model(s) listed, but generation hangs" % len(m)
    return "OLLAMA OK (%d models, probe '%s' in test)" % (len(m), ok)