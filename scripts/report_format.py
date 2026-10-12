# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""REPORT FORMAT - the ONE place that decides how AutoPost talks to the owner on Telegram.

Rules (owner, 2026-10-11): clear, professional, short, understandable by any human.
  * First line = the verdict, in plain words.  * Then: what, where, what went wrong, what the system
    did about it, and whether the OWNER has to do anything (default: nothing).
  * No job ids, step keys, file paths, commands or model names in the message (they stay on the dashboard).
  * HELENA (Head of Department) is the voice: every message ends with her signature line.
"""
import re
import time

SIGN = "- HELENA, Head of Department"

PLATFORM = {"socialai": "Website blog", "telegram": "Telegram (you)", "telegram_channel": "Telegram channel",
            "bluesky": "Bluesky", "discord": "Discord", "youtube": "YouTube", "tiktok": "TikTok",
            "mastodon": "Mastodon", "x": "X", "facebook": "Facebook", "threads": "Threads", "linkedin": "LinkedIn",
            "wordpress": "WordPress site", "webhook": "Webhook (Zapier, Make, n8n)"}
KIND = {"text": "Text post", "image": "Image + text post", "video": "Video post"}
# which part of the team a stopped step belongs to, in plain words
STEP = {"intake": "choosing the topic", "maya": "the creative brief", "sophie": "writing the script",
        "daniel": "planning the scenes", "leo": "the music and mood", "oliver": "the quality check before creation",
        "isabella": "the final go-ahead", "noah": "creating the post", "grace": "the quality check after creation",
        "ella": "publishing", "victoria": "checking the post is live", "helena": "the final report"}


def _when():
    return time.strftime("%d %b, %I:%M %p").lstrip("0")


def _clean(t, n=110):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "..."


def _nothing(action):
    a = str(action or "").strip()
    return (not a) or a.lower().startswith("none") or a.lower().startswith("nothing")


def _who(action, kind="info"):
    """The 'Who acts next' line: always names WHO acts - an agent, nobody, or the owner (and only then what to do)."""
    if _nothing(action):
        if kind == "alert":
            return "YOU (the owner) - open the dashboard (Posts page) and check the post."
        return "SYSTEM WATCHDOG (automatic). You: nothing." if kind == "warn" else "nobody - it was handled automatically."
    return "YOU (the owner) - %s" % str(action).strip()


def post_report(topic, kind, verdict, live, failed, stopped=None, fixes=0):
    """verdict: ok | corrected | partial | failed | human | dry
    live   = [(platform_key, url_or_empty)]   verified live
    failed = [(platform_key, reason)]         could not be published / verified
    stopped = [(step_key, error_title, action_text)]   steps that stopped the job
    """
    kind_n = KIND.get(kind, str(kind or "Post").title())
    total = len(live) + len(failed)
    if verdict == "dry":
        head = "TEST RUN - nothing was created or published"
    elif verdict in ("ok", "corrected"):
        head = "POST PUBLISHED" + (" (small corrections made)" if verdict == "corrected" else "")
    elif verdict == "partial":
        head = "POST PARTLY PUBLISHED - %d of %d places" % (len(live), total)
    elif verdict == "human" or stopped:
        head = "POST STOPPED - not published yet"
    else:
        head = "POST NOT PUBLISHED"
    mark = {"ok": "✅", "corrected": "✅", "partial": "⚠️", "dry": "ℹ️"}.get(verdict, "❌")
    L = ["%s %s" % (mark, head), "", "Topic: %s" % _clean(topic, 140), "Type: %s  |  %s" % (kind_n, _when())]
    if live:
        L += ["", "Live now (%d):" % len(live)]
        for p, url in live:
            L.append("- %s%s" % (PLATFORM.get(p, p), (": " + url) if url else ""))
    if failed:
        L += ["", "Not published (%d):" % len(failed)]
        for p, why in failed:
            L.append("- %s: %s" % (PLATFORM.get(p, p), _clean(why, 90)))
    if stopped:
        step, err, action = stopped[0]
        L += ["", "What happened: it stopped while %s." % STEP.get(step, step),
              "Reason: %s" % _clean(err, 140)]
        if re.search(r"retry|none|nothing", str(action or ""), re.I) or not str(action or "").strip():
            L += ["Who acts next: SYSTEM WATCHDOG (automatic) - retries it or closes it and picks another topic.",
                  "You: nothing. If it keeps failing, a separate ACTION NEEDED message will tell you what to do."]
        else:
            L += ["Who acts next: YOU (the owner) - %s" % str(action).strip()]
    elif failed:
        L += ["", "Who acts next: SYSTEM WATCHDOG (automatic) - retries the missing places. You: nothing unless it repeats."]
    elif verdict in ("ok", "corrected"):
        L += ["", "Problems: none.", "Who acts next: nobody - all done."]
    L += ["", SIGN]
    return "\n".join(L)


def notice(kind, topic, step, what, did, need=None):
    """A system notice (SYSTEM WATCHDOG / guard). kind: info | warn | alert"""
    mark = {"info": "ℹ️", "warn": "⚠️", "alert": "❌"}.get(kind, "ℹ️")
    title = {"info": "SYSTEM NOTICE - handled automatically", "warn": "SYSTEM WARNING - handled automatically",
             "alert": "ACTION NEEDED - from you"}.get(kind, "SYSTEM NOTICE")
    L = ["%s %s" % (mark, title), ""]
    if topic:
        L.append("Topic: %s" % _clean(topic, 140))
    if step:
        L.append("Stopped at: %s" % STEP.get(step, step))
    L += ["What happened: %s" % _clean(what, 160), "What we did: %s" % _clean(did, 160),
          "Who acts next: %s" % _who(need, kind), "", SIGN]
    return "\n".join(L)
