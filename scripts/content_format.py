# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - content_format.py
Per-platform caption formatting. Every platform used to get the exact same
title+hook+hashtag string, hard-sliced with e.g. text[:295] - which cuts
words/hashtags mid-string once a caption runs long, and wastes platforms
that allow much more (TikTok's caption cap is 4,000 chars, this pipeline
was only ever using ~148 of them). This module fixes both: word-safe
trimming, and a real per-platform limit/hashtag/long-form profile.

Does not change what gets POSTED WHERE or any platform module's own
send() code - only how the caption text handed to each send() is built.
Every platform module still has its own internal length guard too
(now effectively a redundant safety net, harmless).
"""

# limit      = real-world safe character budget for this platform/context
#              (researched hard caps, with a small safety margin where the
#              platform itself doesn't already guarantee headroom).
# hashtags   = "none" (skip entirely - e.g. Discord has no hashtag discovery
#              feature, so tags there are just wasted characters, not broken
#              text), "few" (2-4, platforms where dense tags read as noise
#              or eat too much of a tight budget), "many" (up to 10).
# long_form  = True -> prefer the richer blog_body paragraph over the short
#              1-2 sentence hook when one was generated for this job and it
#              fits the platform's budget better than a stub caption would.
PROFILES = {
    "telegram":       {"limit": 1024, "hashtags": "many", "long_form": True},   # photo/video caption cap
    "telegram_text":  {"limit": 4096, "hashtags": "many", "long_form": True},   # text-only post, no media
    "youtube":        {"limit": 4500, "hashtags": "many", "long_form": True},   # description field (real cap 5000)
    "tiktok":         {"limit": 3000, "hashtags": "many", "long_form": True},   # real cap 4000, kept margin
    "bluesky":        {"limit": 295,  "hashtags": "few",  "long_form": False},  # real hard cap 300 graphemes
    "mastodon":       {"limit": 490,  "hashtags": "few",  "long_form": False},  # default instance cap 500
    "discord":        {"limit": 1900, "hashtags": "none",  "long_form": True},   # real cap 2000, no hashtag feature
    "facebook":       {"limit": 4000, "hashtags": "few",  "long_form": True},
    "threads":        {"limit": 480,  "hashtags": "few",  "long_form": False},  # real cap 500
    "linkedin":       {"limit": 2900, "hashtags": "few",  "long_form": True},   # real cap 3000
    "wordpress":      {"limit": 25000, "hashtags": "none", "long_form": True},  # a full article
    "webhook":        {"limit": 4000, "hashtags": "many", "long_form": True},   # not connected yet - ready when it is
    # Own website (blog.your-domain.example) - the owner's BLOG, not a social network.
    # It was the only destination still getting the short 1-2 sentence hook
    # while Telegram got the full blog_body, which made every post look
    # truncated on the site. It is given a generous budget because a blog must
    # carry the COMPLETE post - publish_guard.BLOG-COMPLETE hard-blocks rather
    # than letting a long article be quietly cut in half.
    "socialai":       {"limit": 25000, "hashtags": "many", "long_form": True},
}

DEFAULT_PROFILE = {"limit": 1024, "hashtags": "many", "long_form": False}


def trim_to_limit(text, limit):
    """Trim to the last whole word/line break at or before `limit` - never
    cuts a word or hashtag in half. Falls back to a hard slice only if
    nothing under `limit` is a safe break point (e.g. one giant token)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("\n\n", "\n", " "):
        idx = cut.rfind(sep)
        if idx > limit * 0.5:          # don't discard more than half the budget
            return cut[:idx].rstrip()
    return cut.rstrip()


def hashtags_for(tags, policy):
    tags = list(tags or [])
    if policy == "none":
        return []
    if policy == "few":
        return tags[:4]
    return tags[:10]


def format_text(platform_key, title="", hook="", tags=None, blog_body=None):
    """Build the final caption for one platform: pick long-form body when
    the platform supports it and one was generated, apply that platform's
    hashtag policy, then trim word-safe to its real character budget."""
    prof = PROFILES.get(platform_key, DEFAULT_PROFILE)
    body = (hook or title or "").strip()
    if prof["long_form"] and blog_body and blog_body.strip():
        body = blog_body.strip()
    picked = hashtags_for(tags, prof["hashtags"])
    text = body
    if picked:
        text = (text + "\n\n" + " ".join(picked)).strip()
    return trim_to_limit(text, prof["limit"])
