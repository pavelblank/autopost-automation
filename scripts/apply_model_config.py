# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Apply the owner's model decisions to config/rules.json (UTF-8, no BOM).

- primary  : qwen2.5:14b   (cartoon winner 39s AND real-human winner 51s)
- fallback : ministral-3:14b (backup if qwen is unavailable/errors)
- adds the generation schedule but LEAVES paused=true, so nothing fires.
- also adds the safe sequential-failover switch the code will read.
"""
import json
from pathlib import Path

RULES = (Path(__file__).resolve().parents[1] / "config" / "rules.json")

PRIMARY = "qwen2.5:14b"
FALLBACKS = ["ministral-3:14b"]

data = json.loads(RULES.read_text(encoding="utf-8-sig"))

data["ollama_model"] = PRIMARY
data["ollama_fallbacks"] = FALLBACKS

# Safe failover: only one model resident at a time. The old cascade loaded several
# big models at once and nearly exhausted RAM (removed 2026-09-03 at the owner's request).
# This form is the safe version: on failure, unload the dead model BEFORE the next try.
data["ollama_failover"] = {
    "enabled": True,
    "unload_before_fallback": True,
    "unload_keep_alive": 0,
    "note": ("Sequential failover only. Never concurrent. The failed model is explicitly "
             "unloaded (keep_alive=0) before the next one is tried, so RAM never holds two "
             "large models at once - the exact failure that removed the old cascade on 2026-09-03."),
}

# ---- schedule: PRESENT but dormant (paused stays true) ----
data["schedule_generation"] = [
    {"id": "gen-video-1", "enabled": True, "time": "10:00", "days": "mon", "type": "video",
     "scenes": 4, "platforms": ["socialai"],
     "note": "Local-only video post. SocialBlog (https://localhost:8543) is the ONLY destination - never live."},
    {"id": "gen-image-1", "enabled": True, "time": "15:00", "days": "wed", "type": "image",
     "platforms": ["socialai"],
     "note": "Local-only image post."},
    {"id": "gen-text-1", "enabled": True, "time": "19:00", "days": "fri", "type": "text",
     "platforms": ["socialai"],
     "note": "Local-only text post."},
]
data["department"] = {
    "enabled": False,
    "note": ("Social Media Department v3.0 (17 agents, 7 stages). False = the department "
             "tables and code exist and are safe, but the normal pipeline still runs the old "
             "path. Switch to true only when the owner says, after a full dry run."),
}

RULES.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")

back = json.loads(RULES.read_text(encoding="utf-8-sig"))
print("ollama_model      :", back.get("ollama_model"))
print("ollama_fallbacks  :", back.get("ollama_fallbacks"))
print("failover enabled  :", back.get("ollama_failover", {}).get("enabled"))
print("paused            :", back.get("paused"))
print("approval_mode     :", back.get("approval_mode"))
print("department.enabled:", back.get("department", {}).get("enabled"))
print("schedule_generation jobs:", [j["id"] for j in back.get("schedule_generation", [])])
