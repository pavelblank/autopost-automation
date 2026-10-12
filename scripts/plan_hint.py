# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Optional bridge: when the Social Media Department drives a generation it sets the env var
AUTOPOST_PLAN_FILE to the approved plan (brief + script + storyboard ...). The generators
append block() to their writing prompts so the approved plan really steers the output.
With the env var unset (every normal run) block() returns '' and nothing changes."""
import json
import os


def block(max_chars=2200):
    p = os.environ.get("AUTOPOST_PLAN_FILE")
    if not p:
        return ""
    try:
        plan = json.load(open(p, encoding="utf-8"))
    except Exception:
        return ""
    sc, br, em = plan.get("script") or {}, plan.get("brief") or {}, plan.get("emotion") or {}
    parts = []
    if br:
        parts.append("Creative brief: " + json.dumps(br, ensure_ascii=False))
    if sc.get("title"):
        parts.append("Approved title: " + str(sc["title"]))
    if sc.get("narration"):
        parts.append("Approved script (keep its message, hook and tone; you may polish the wording): "
                     + str(sc["narration"]))
    if sc.get("story_arc"):
        parts.append("Story arc: " + json.dumps(sc["story_arc"], ensure_ascii=False))
    if sc.get("characters"):
        parts.append("Characters (keep age and dress): " + json.dumps(sc["characters"], ensure_ascii=False))
    if em:
        parts.append("Emotion/performance: " + json.dumps(em, ensure_ascii=False))
    if not parts:
        return ""
    return ("\n\nAPPROVED PLAN FROM THE PLANNING TEAM (follow it):\n" + "\n".join(parts))[:max_chars]
