# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""publish_guard - ONE ordered, step-by-step safety gate for every post.

the owner's requirement: a smart step-by-step agent that makes sure nothing
incomplete, duplicated, over-limit or malformed ever reaches a real platform,
and that blog.your-domain.example (his blog) always gets the COMPLETE post - never
a shortened or cut-down version.

Every check returns a plain-English reason. Nothing here is advisory-only: a
failure means DO NOT PUBLISH, and the caller is expected to rotate to a new
topic and try again rather than shipping something broken.

Order matters. Cheap deterministic checks run first (milliseconds), the LLM
review runs last because it is the only slow and fallible one. That way a bad
post is rejected before we spend 10+ seconds asking a small model about it.
"""

import re
import time
from pathlib import Path

import content_format as cf

try:
    import textsan
except Exception:                                    # pragma: no cover
    textsan = None

ROOT = (Path(__file__).resolve().parents[1])

# Platforms where losing the tail of the text is ACCEPTABLE because the cap is
# genuinely tiny (a 295-char Bluesky post cannot hold an essay). Trimming there
# is correct behaviour, done word-safe. socialai is deliberately absent: it is a
# blog and must carry the whole post.
TRUNCATION_TOLERANT = {"bluesky", "mastodon", "telegram", "telegram_text",
                       "discord", "tiktok", "youtube", "facebook"}

# Per-platform safety rails. Kept here (not scattered) so there is ONE place to
# look when a platform rejects something. Conservative minimum cooldown between
# two posts to the same destination, in seconds.
PLATFORM_RAILS = {
    "bluesky":  {"cooldown": 90,  "max_per_day": 60},
    "mastodon": {"cooldown": 120, "max_per_day": 40},
    "telegram": {"cooldown": 30,  "max_per_day": 120},
    "discord":  {"cooldown": 60,  "max_per_day": 60},
    "tiktok":   {"cooldown": 600, "max_per_day": 3},
    "youtube":  {"cooldown": 900, "max_per_day": 3},
    "facebook": {"cooldown": 300, "max_per_day": 20},
    "socialai": {"cooldown": 20,  "max_per_day": 200},
}

# Words that cannot end a finished sentence. If a caption ends on one of these
# without final punctuation it was cut off. Kept deliberately broad - including
# pronouns like "it" - because the earlier short list let
# "...thousands of kilometres and then it" through as if it were complete.
DANGLING_WORDS = {
    "a", "an", "the", "and", "but", "or", "so", "because", "as", "if", "when",
    "while", "since", "until", "though", "although", "of", "in", "on", "at",
    "to", "from", "with", "without", "by", "for", "about", "into", "onto",
    "over", "under", "between", "through", "during", "after", "before",
    "that", "which", "who", "whom", "whose", "what", "where", "when", "why",
    "how", "than", "then", "there", "here", "it", "its", "he", "she", "they",
    "we", "you", "i", "this", "these", "those", "his", "her", "their", "our",
    "your", "my", "me", "him", "them", "us", "is", "are", "was", "were", "be",
    "been", "being", "am", "has", "have", "had", "do", "does", "did", "can",
    "could", "will", "would", "should", "may", "might", "must", "shall", "not",
    "no", "very", "more", "most", "some", "any", "each", "every", "both",
    "such", "own", "same", "other", "another", "also", "just", "very", "into",
}
DANGLING_END = re.compile(r"(" + "|".join(sorted(DANGLING_WORDS)) + r")[\s,.]*$", re.I)
SENTENCE_END = tuple(".!?\"')]}")


class Step(object):
    """One gate result, written so a human can read it in the dashboard.

    severity decides what happens on failure:
      "content"   - the post itself is wrong. Reject the whole job so a new
                    topic gets generated instead.
      "platform"  - only this destination can't take it right now (rate limit,
                    daily cap). Skip that ONE platform and still post to the
                    rest, rather than throwing the whole post away.
    """

    def __init__(self, name, ok, reason="", detail="", severity="content"):
        self.name = name
        self.ok = bool(ok)
        self.reason = reason
        self.detail = detail
        self.severity = severity

    def __repr__(self):
        return "<Step %s %s %s>" % (self.name, "PASS" if self.ok else "FAIL", self.reason[:40])


class Report(object):
    def __init__(self, platform):
        self.platform = platform
        self.steps = []
        self.blocking = None        # name of the first failing step

    def add(self, name, ok, reason="", detail=""):
        s = Step(name, ok, reason, detail)
        self.steps.append(s)
        if not s.ok and not self.blocking:
            self.blocking = name
            self.blocking_reason = reason
        return s

    @property
    def ok(self):
        return self.blocking is None

    @property
    def content_failed(self):
        """True when a CONTENT problem means the whole post must be regenerated."""
        for s in self.steps:
            if not s.ok and s.severity == "content":
                return True
        return False

    def platform_skips(self):
        """Destinations that are merely busy right now, not bad content."""
        return [s for s in self.steps if not s.ok and s.severity == "platform"]

    def first_problem(self):
        for s in self.steps:
            if not s.ok:
                return s
        return None

    def to_dict(self):
        return {
            "platform": self.platform,
            "approved": self.ok,
            "gate": self.blocking or "",
            "reason": getattr(self, "blocking_reason", "") if not self.ok else "",
            "steps": [{"name": s.name, "ok": s.ok, "reason": s.reason} for s in self.steps],
        }

    def summary(self):
        if self.ok:
            return "all %d checks passed" % len(self.steps)
        s = self.first_problem()
        return "%s: %s" % (s.name, s.reason)


# --------------------------------------------------------------------------
# individual checks
# --------------------------------------------------------------------------

def check_complete(formatted, source, platform):
    """The post must be FINISHED, not cut off mid-sentence or mid-word.

    This is the check that stops an incomplete post ever going live. For
    truncation-tolerant platforms a word-safe trim to a genuinely tiny cap is
    fine (a 295-char Bluesky post cannot hold an essay) - what is never fine is
    a caption that stops on a dangling word like "and then it", which is the
    signature of text that lost its ending."""
    text = (formatted or "").strip()
    if not text:
        return Step("COMPLETE", False, "The post text is empty.")

    # A caption normally ends with its hashtags - that is correct formatting, not
    # a cut-off. Strip trailing hashtag tokens before judging completeness, or
    # every Bluesky/Mastodon caption was rejected for "ending on #SpaceWeather".
    core = re.sub(r"(?:\s*#[\w\-]+)+\s*$", "", text).strip() or text

    if not core.endswith(SENTENCE_END):
        last = re.split(r"[\s(]", core)[-1] if core.split() else ""
        if DANGLING_END.search(core) or last.lower().strip(".,;:!?\"')]}") in DANGLING_WORDS:
            return Step("COMPLETE", False,
                        "The post stops mid-sentence - it ends on \"%s\" instead of "
                        "finishing its thought. It was held back rather than "
                        "publishing half a sentence." % (last or "?")[:30])

    return Step("COMPLETE", True, "finishes cleanly (%d chars)" % len(text))


def check_socialai_full(formatted, source, platform):
    """blog.your-domain.example is a BLOG: it must carry the complete post.

    Never silently shorten it. If the text is over the blog budget the post is
    held back with a clear reason instead of publishing a cut-down version."""
    if platform != "socialai":
        return Step("BLOG-COMPLETE", True, "not the blog destination")
    src = (source or "").strip()
    out = (formatted or "").strip()
    if not out:
        return Step("BLOG-COMPLETE", False, "The blog post is empty.")
    limit = cf.PROFILES["socialai"]["limit"]
    if len(out) < len(src):
        return Step("BLOG-COMPLETE", False,
                    "The blog post would have been cut from %d characters down to "
                    "%d, and this is a blog so it must carry the complete post. "
                    "Held back instead of publishing a shortened version."
                    % (len(src), len(out)))
    if len(out) > limit:
        return Step("BLOG-COMPLETE", False,
                    "The blog post is %d characters, over the %d the site accepts. "
                    "Held back rather than publishing it cut in half."
                    % (len(out), limit))
    return Step("BLOG-COMPLETE", True,
                "complete post, %d/%d characters, nothing dropped" % (len(out), limit))


def check_clean(text, title=""):
    """No HTML entities, no leaked model reasoning, no raw code/JSON."""
    problems = []
    if textsan is not None:
        for label, value in (("post text", text), ("title", title)):
            for p in textsan.problems(value or ""):
                problems.append("%s has %s" % (label, p))
    low = (text or "").lower()
    if "<think>" in low or "</think>" in low:
        problems.append("the AI's private reasoning leaked into the post")
    stripped = (text or "").strip()
    if stripped[:1] in "{[" and stripped[-1:] in "}]" and '"' in stripped:
        problems.append("this looks like raw computer data, not a readable post")
    if problems:
        return Step("CLEAN", False, problems[0], "; ".join(problems))
    return Step("CLEAN", True, "no entities, no leaked reasoning, readable")


# --- the owner's standing publishing rules (AGENTS.md, "Content & publishing rules") ---------
#  1. Public pages use the byline "Mr Blank" and carry no AI attribution.
#  2. Published text must not read like AI output (a real editorial pass).
# Rule 1 is about attribution, not topic: a post ABOUT artificial intelligence is fine, a post
# that says it was written by one is not. Rule 2 is a cheap deterministic screen for the tell-tale
# stock phrases; one stray phrase is tolerated, a stack of them is not.
_AI_ATTRIBUTION = re.compile(
    r"(generated|written|created|produced|made|powered)\s+(by|with|using)\s+(an?\s+)?"
    r"(ai|a\.i\.|artificial intelligence|chatgpt|gpt|claude|gemini|llm|language model|ollama|qwen)"
    r"|\bai[- ](generated|written|created|assisted|powered)\b"
    r"|\bas an ai\b|\blanguage model\b|\bai assistant\b", re.I)
_AI_TELLS = [
    r"in today'?s (fast[- ]paced|digital|modern|hyper[- ]connected)", r"hyper[- ]connected",
    r"\btransformative\b", r"\bdelve\b", r"\btapestry\b", r"\bgame[- ]chang(er|ing)\b",
    r"unlock(ing)? the (power|secrets?|potential)", r"navigate the complexit",
    r"in the realm of", r"it'?s (important|worth) (to note|noting)", r"ever[- ]evolving",
    r"\ba testament to\b", r"\bembark(s|ed)? on\b", r"\bseamless(ly)?\b", r"\bleverag(e|ing)\b",
    r"\bmore than just\b", r"\bbeacon of\b", r"in conclusion,", r"at the end of the day",
]
_AI_TELL_RES = [re.compile(p, re.I) for p in _AI_TELLS]
AI_TELL_LIMIT = 3          # this many DIFFERENT tells in one post = reads like AI output


def check_house_rules(text, title=""):
    """the owner's publishing rules. Deterministic, no model call, never raises."""
    body = "%s\n%s" % (title or "", text or "")
    if re.search(r"\bpavel\b", body, re.I):
        return Step("HOUSE-RULES", False,
                    'the public text names the owner by first name - the rule is to use "Mr Blank"',
                    "rule: use the byline Mr Blank on public pages")
    m = _AI_ATTRIBUTION.search(body)
    if m:
        return Step("HOUSE-RULES", False,
                    'the public text says it was made by AI ("%s")' % m.group(0)[:40],
                    "rule: no 'AI' attribution on any public-facing page")
    hits = sorted({r.pattern for r in _AI_TELL_RES if r.search(body)})
    if len(hits) >= AI_TELL_LIMIT:
        return Step("HOUSE-RULES", False,
                    "reads like AI output: %d stock phrases (%s)" % (len(hits), ", ".join(hits[:3])[:70]),
                    "rule: must pass a real editorial pass; rewrite in plain human wording")
    return Step("HOUSE-RULES", True, 'byline ok, no AI attribution, %d stock phrase(s)' % len(hits))


def check_quality(text, title=""):
    """The deterministic 'would a human be annoyed by this?' gate.

    Lives in editorial_auditor._quality_check - repeated-sentence loops, stuck
    generations, raw model output, near-empty text. Reused rather than
    reimplemented so there is one definition of 'obviously broken'."""
    try:
        import editorial_auditor as ea
        probs = ea._quality_check(text, title)
    except Exception as e:
        return Step("QUALITY", False, "Could not run the quality check: %s" % str(e)[:60])
    if probs:
        return Step("QUALITY", False, probs[0], "; ".join(probs))
    return Step("QUALITY", True, "reads properly, no loops or filler")


def check_duplicate(text, topic="", skip_job=None, exclude_run_id=None):
    """Exact + near-duplicate detection across recent and published work."""
    try:
        import editorial_auditor as ea
    except Exception as e:
        return Step("DUPLICATE", False, "Could not run the duplicate check: %s" % str(e)[:60])
    body = (text or "").strip()
    if len(body) >= 60:
        dup, why = ea.duplicate_check(body, skip_job=skip_job)
        if dup:
            return Step("DUPLICATE", False, why)
    if topic:
        try:
            import publish as pub
            seen = pub._recently_published_duplicate(topic, exclude_run_id=exclude_run_id)
            if seen:
                return Step("DUPLICATE", False,
                            "This subject is too close to one that already went out "
                            "(\"%s\"). Posting it again would look like spam." % seen[:60])
        except Exception:
            pass
    if topic:
        try:
            import post_history
            hist = post_history.find_repeat(topic, exclude_run_id=exclude_run_id)
            if hist:
                return Step("DUPLICATE", False,
                            "This subject was already posted before (\"%s\"). History file says skip it." % hist[:60])
        except Exception:
            pass
    return Step("DUPLICATE", True, "not a repeat of anything already published")


def check_limits(platform, formatted):
    """The text must fit the platform's real cap with room to spare."""
    prof = cf.PROFILES.get(platform, cf.DEFAULT_PROFILE)
    limit = prof["limit"]
    n = len(formatted or "")
    if n > limit:
        return Step("LIMIT", False,
                    "The post is %d characters but %s only allows about %d, so the "
                    "end would be cut off. Held back rather than publishing a "
                    "truncated post." % (n, platform, limit),
                    severity="platform")
    return Step("LIMIT", True, "%d/%d characters" % (n, limit))


def check_tags(tags):
    try:
        import editorial_auditor as ea
        probs = ea.check_hashtags(tags)
    except Exception as e:
        return Step("TAGS", False, "Could not check hashtags: %s" % str(e)[:50])
    if probs:
        return Step("TAGS", False, probs[0])
    return Step("TAGS", True, "hashtags look real")


def check_quota(platform, kind="text"):
    """Per-platform posting limits, so a post is never rejected by the platform.

    TikTok and YouTube are the strict ones (3/day). This reads what already
    succeeded from the local DB - no network needed."""
    rails = PLATFORM_RAILS.get(platform)
    if not rails:
        return Step("POST-LIMIT", True, "no local rail for %s" % platform)
    try:
        import db
        with db.conn() as c:
            since = time.time() - 86400
            today = c.execute(
                "SELECT COUNT(*) FROM posts WHERE platform=? AND status='ok' "
                "AND posted > ?", (platform, since)).fetchone()[0]
            last = c.execute(
                "SELECT posted FROM posts WHERE platform=? AND status='ok' "
                "AND posted IS NOT NULL ORDER BY posted DESC LIMIT 1",
                (platform,)).fetchone()
    except Exception as e:
        return Step("POST-LIMIT", False, "Could not check posting limits: %s" % str(e)[:70])

    if today >= rails["max_per_day"]:
        return Step("POST-LIMIT", False,
                    "%s has had %d posts today and the safe limit is %d per day. "
                    "Held back so it is not rejected by the platform."
                    % (platform, today, rails["max_per_day"]),
                    severity="platform")
    if last and last["posted"]:
        gap = time.time() - float(last["posted"])
        if gap < rails["cooldown"]:
            return Step("POST-LIMIT", False,
                        "Only %d seconds since the last %s post and it needs %d "
                        "seconds of space, so posting now would be rate limited."
                        % (int(gap), platform, rails["cooldown"]),
                        severity="platform")
    return Step("POST-LIMIT", True,
                "%d/%d today, spacing ok" % (today, rails["max_per_day"]))


def check_ai_review(text, platform, title="", timeout=120):
    """The model's own quality opinion - and it FAILS CLOSED.

    The old auditor returned approved=True whenever Ollama was unreachable,
    which meant a dead model silently waved every post through. the owner was
    explicit: audit failing is not good, and nothing incomplete should ever be
    posted. So an unreachable or unparseable auditor now BLOCKS, and the caller
    rotates to a new topic instead of shipping unchecked text."""
    try:
        import editorial_auditor as ea
        r = ea._llm_review(text, platform, title, timeout=timeout)
    except Exception as e:
        return Step("AI-REVIEW", False,
                    "The AI reviewer could not be reached (%s). Nothing is being "
                    "published unchecked - a new topic will be tried instead."
                    % str(e)[:70])
    if r is None:
        return Step("AI-REVIEW", False,
                    "The AI reviewer did not return a usable answer. Nothing is "
                    "published unchecked - a new topic will be tried instead.")
    if isinstance(r, dict):
        if "approved" not in r:
            return Step("AI-REVIEW", False,
                        "The AI reviewer gave an unclear answer, so the post was held back.")
        ok = bool(r.get("approved"))
        reason = (r.get("reason") or "").strip()
    else:
        ok, reason = bool(r), ""
    if not ok:
        return Step("AI-REVIEW", False,
                    reason or "The AI reviewer did not think this post is good enough.")
    return Step("AI-REVIEW", True, reason or "reviewed and approved")


# --------------------------------------------------------------------------
# the full ordered gate
# --------------------------------------------------------------------------

def run_all(text, platform, title="", tags=None, blog_body=None,
            topic="", kind="text", skip_job=None, exclude_run_id=None,
            use_ai=True, ai_timeout=120):
    """Run every check in order. Returns a Report.

    text        - the exact string that will be sent to `platform`
    blog_body   - the full/long version, used to prove nothing was dropped
    use_ai      - set False to run only the fast deterministic checks
    """
    rep = Report(platform)
    source = blog_body or text

    rep.add("COMPLETE", *self_args(check_complete(text, source, platform)))
    rep.add("BLOG-COMPLETE", *self_args(check_socialai_full(text, source, platform)))
    rep.add("CLEAN", *self_args(check_clean(text, title)))
    rep.add("QUALITY", *self_args(check_quality(text, title)))
    rep.add("HOUSE-RULES", *self_args(check_house_rules(text, title)))
    rep.add("DUPLICATE", *self_args(check_duplicate(text, topic, skip_job, exclude_run_id)))
    rep.add("LIMIT", *self_args(check_limits(platform, text)))
    rep.add("TAGS", *self_args(check_tags(tags)))
    rep.add("POST-LIMIT", *self_args(check_quota(platform, kind)))
    if use_ai:
        rep.add("AI-REVIEW", *self_args(check_ai_review(text, platform, title, ai_timeout)))

    return rep


def self_args(step):
    return (step.ok, step.reason, step.detail)
