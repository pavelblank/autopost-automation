# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""DEPARTMENT REGISTRY - so AutoPost can become several departments, not just one.

TODAY there is exactly one: the Social Media Department. That is the default and the
fallback. Adding a second one later must never require editing the engine, so nothing
here reads a department name out of the code - the name comes from
config/departments.json, and if that file is missing or unreadable we fall back to the
built-in default below. The system therefore behaves EXACTLY as before when this module
is absent, which is the important property.

To add a second department later:
  1. copy config/departments.json, add a key under "departments"
  2. give it its own spec file (its own agents + chain) and its own rules block
  3. set "active" to that key
Nothing in scripts/department_queue.py or scripts/department.py needs to change: both
read their chain from a spec, and they are told which spec by this registry.
"""
import json
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
FILE = ROOT / "config" / "departments.json"

# The built-in default. If the config file is gone or broken, this is what the system is,
# so removing the file can never take the department offline.
DEFAULT = {
    "active": "social-media",
    "departments": {
        "social-media": {
            "name": "Social Media Department",
            "short": "AutoPost",
            "tagline": "writes one piece of content, has 18 specialists approve it, "
                       "and will not publish anything it did not check",
            "spec": "videogen/AGENT_SPEC.json",
            "chain": "scripts/department_queue.py",
            "agents": "scripts/department.py",
            "rules_key": "department",
            "home": "/",
            "office": "/office",
            "platforms": ["socialai"],
            "status": "active",
            "note": "The first department. 18 agents in a chain plus 3 sidecars.",
        }
    },
}


def _merge(base, over):
    """Config wins, but never loses a key the default already had."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    try:
        return _merge(DEFAULT, json.loads(FILE.read_text(encoding="utf-8-sig")))
    except Exception:
        return DEFAULT


def active_key():
    return load().get("active") or "social-media"


def get(key=None):
    d = load()
    return d["departments"].get(key or d.get("active"), DEFAULT["departments"]["social-media"])


def name(key=None):
    return get(key).get("name", "Social Media Department")


def short(key=None):
    return get(key).get("short", "AutoPost")


def tagline(key=None):
    return get(key).get("tagline", "")


def spec_path(key=None):
    p = get(key).get("spec") or "videogen/AGENT_SPEC.json"
    f = Path(p)
    return f if f.is_absolute() else ROOT / p


def all_departments():
    return load().get("departments", {})


def summary():
    """For the dashboard: what exists, what is active, how many agents each has."""
    out = []
    for k, v in all_departments().items():
        n = None
        try:
            sp = spec_path(k)
            if sp.exists():
                n = len(json.loads(sp.read_text(encoding="utf-8")).get("agents", []))
        except Exception:
            pass
        out.append({"key": k, "name": v.get("name", k), "short": v.get("short", k),
                    "tagline": v.get("tagline", ""), "agents": n,
                    "platforms": v.get("platforms", []),
                    "status": v.get("status", "active"), "active": k == active_key()})
    return out


if __name__ == "__main__":
    print(json.dumps(summary(), indent=2))