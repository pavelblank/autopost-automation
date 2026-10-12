# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - editorial_auditor.py
Final quality gate before posting to social media. Uses local Ollama (Qwen)
to verify content meets quality standards. Auto-regenerates if rejected.

Checks for:
- No corrupted/database errors
- No wrong symbols/signs/formatting
- Appropriate text quality for social media
- Grammar, clarity, professionalism
"""
import difflib
import json
import re
import time
import urllib.request
from pathlib import Path

import textsan

ROOT = (Path(__file__).resolve().parents[1])
OLLAMA = "http://127.0.0.1:11434/api/generate"
CATEGORY_KEYS = ["facts_mystery_science", "lifestyle_fashion_beauty", "fitness_motivation",
                 "finance_business", "self_healing_spiritual", "default"]


def _job_blog_body(video_path):
    """Longer-form paragraph for video posts - used by audit and publish."""
    mf = Path(video_path).parent / "meta.json"
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8")).get("blog_body")
    except Exception:
        return None


def log(m):
    try:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m, flush=True)
    except UnicodeEncodeError:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m.encode("ascii", "replace").decode("ascii"), flush=True)


def _active_model():
    """Get the configured Ollama model (use llama3.2:3b for quality)."""
    try:
        R = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8"))
        model = R.get("ollama_model") or "llama3.2:3b"
        return model
    except Exception:
        return "llama3.2:3b"


def ollama_call(prompt, timeout=300):
    """Call local Ollama with the active model."""
    model = _active_model()
    payload = {"model": model, "prompt": prompt, "stream": False}
    body = json.dumps(payload).encode()
    try:
        req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)["response"].strip()
        return resp
    except Exception as e:
        log("  auditor call failed: " + str(e)[:100])
        return None


ROOT = (Path(__file__).resolve().parents[1])


def _norm(t):
    """Lowercase, drop everything that isn't a letter/digit/space. Two posts
    that differ only in punctuation, emoji or hashtags are duplicates."""
    t = re.sub(r"https?://\S+", " ", str(t or "").lower())
    t = re.sub(r"#\w+", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(t):
    return set(_norm(t).split())


def _similar(a, b):
    """0..1 similarity. Token Jaccard catches reworded repeats; falls back to
    a character ratio for short texts where token overlap is meaningless."""
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb:
        j = len(ta & tb) / float(len(ta | tb))
        if j > 0.25:
            return j
    sa, sb = _norm(a)[:400], _norm(b)[:400]
    if not sa or not sb:
        return 0.0
    return difflib.SequenceMatcher(None, sa, sb).ratio()


def _recent_captions(limit=40):
    """Pull the caption/blog_body of recently BUILT jobs so a repeat can be
    spotted against what has already gone out, not just inside this run."""
    out_dir = ROOT / "out"
    if not out_dir.is_dir():
        return []
    jobs = sorted((p for p in out_dir.iterdir() if p.is_dir()),
                  key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    found = []
    for j in jobs:
        for fn in ("post.json", "meta.json"):
            f = j / fn
            if not f.exists():
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for key in ("blog_body", "caption", "text", "hook"):
                v = d.get(key)
                if isinstance(v, str) and len(v.strip()) > 80:
                    found.append((j.name, key, v))
                    break
            break
    return found


def duplicate_check(text, skip_job=None, threshold=0.62):
    """Return (True, human_reason) if this text is too close to something
    already published. Deterministic - does not trust the LLM, which is
    currently timing out and waving everything through.

    skip_job MUST be the folder name of the job being published. Without it the
    job folder is already sitting in out/ and the new text matches ITSELF at
    100%, which blocks every single post. Verified happening."""
    if not text or len(text.strip()) < 60:
        return False, ""
    for job_name, field, prev in _recent_captions():
        if skip_job and job_name == skip_job:
            continue
        s = _similar(text, prev)
        if s >= threshold:
            return True, ("This is nearly the same as a post that already went out "
                          "(%d%% the same wording). Duplicate posts get penalised by "
                          "the social platforms, so it was held back." % int(s * 100))
    return False, ""


def _quality_check(text, post_title=""):
    """Deterministic 'would a human be annoyed by this?' gate. Plain-English
    reasons only - these strings are shown in the dashboard and Telegram."""
    probs = []
    body = (text or "").strip()

    if not body or len(body) < 20:
        probs.append("The post text is empty or far too short to post.")
        return probs

    if textsan is not None:
        for label, val in (("The title", post_title), ("The post text", body)):
            for p in textsan.problems(val):
                probs.append("%s: %s" % (label, p))

    # raw model reasoning / unparsed JSON saved as if it were the caption
    for pat, why in ((r"<think>|</think>", "the AI's private thinking notes leaked into the text"),
                     (r'"thoughts"\s*:|"reasoning"\s*:', "raw AI internal data was saved as the post text"),
                     (r"^\s*[\[{]|[\]}]\s*$", "the post text is raw computer code/data, not a readable post")):
        if re.search(pat, body, re.I | re.M):
            probs.append("The post text contains %s." % why)
            break

    words = _norm(body).split()
    if len(words) >= 8:
        # A stuck generation loop repeats text verbatim. Check 4-grams as well as
        # 6-grams: short stock sentences ("Amazing post. Great topic.") repeat
        # at 4-grams but never form a 6-gram, so the 6-gram test alone let
        # obviously padded filler through.
        for n in (6, 4):
            if len(words) < n + 1:
                continue
            grams = [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]
            if grams and len(set(grams)) < len(grams) * 0.55:
                probs.append("The same sentence is repeated over and over - the AI got stuck.")
                break
        # the same complete sentence appearing more than once = padded filler
        sents = [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) > 12]
        if sents and len(set(sents)) < len(sents):
            probs.append("The post says the same thing more than once instead of adding new information.")
        # Semantic filler that has no exact repeated n-gram or sentence, e.g.
        # "This post is about auroras. Auroras are lights in the sky. Lights in
        # the sky are called auroras." - caught by how few DIFFERENT content
        # words it actually uses. Real writing sits near 1.0; filler drops well
        # below 0.6, so 0.55 separates them without touching normal captions.
        content = [w for w in words if len(w) > 3]
        if len(content) >= 12:
            ratio = len(set(content)) / float(len(content))
            if ratio < 0.55:
                probs.append("The post keeps reusing the same few words instead of "
                             "saying something new - it reads as filler.")
        # single token dominating = degenerate loop
        top = max(set(words), key=words.count)
        if words.count(top) > len(words) * 0.25 and len(words) > 15:
            probs.append("The post repeats one word over and over - it did not write properly.")

    if len(set(words)) < 8 and len(words) > 10:
        probs.append("The post has almost no real words in it.")

    return probs


PLACEHOLDER_TAGS = {"#a", "#b", "#c", "#d", "#e", "#f", "#g", "#x", "#y", "#z",
                    "#tag1", "#tag2", "#tag3", "#tag", "#example", "#...",
                    "#hashtag", "#test", "#foo", "#bar"}


def check_hashtags(tags):
    """Weak small models copy the example tags straight out of the prompt and
    post literal '#a #b' to real platforms. Verified happening with
    qwen2.5:0.5b - 3 out of 3 prompts came back with the placeholders intact."""
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [t for t in tags.split() if t.startswith("#")]
    tags = [str(t).strip() for t in tags if str(t).strip()]
    if not tags:
        return []
    bad = [t for t in tags if t.lower().rstrip("!") in PLACEHOLDER_TAGS]
    if bad:
        return ["The hashtags are leftover examples from the instructions (%s) instead of real tags about the post."
                % ", ".join(bad[:4])]
    if len(tags) > 12:
        return ["There are %d hashtags, which is far too many and looks like spam." % len(tags)]
    return []


def audit_content(text, platform, post_title="", hashtags=None, skip_job=None):
    """
    Audit content before posting. Returns dict with:
    - approved (bool): Should this post?
    - reason (str): Simple explanation for user
    - suggestion (str): How to fix if rejected (empty if approved)
    """
    if not text or len(text.strip()) < 5:
        return {
            "approved": False,
            "reason": "Content too short or empty",
            "suggestion": "Regenerate with more text"
        }

    tag_probs = check_hashtags(hashtags)
    if tag_probs:
        return {
            "approved": False,
            "reason": tag_probs[0],
            "suggestion": "Held back - the AI returned example tags instead of real ones.",
            "issues": tag_probs,
            "gate": "hashtags",
        }

    # --- DETERMINISTIC READABILITY GATE (runs BEFORE the LLM) -------------
    # The local model is a small one and it reliably waves through raw HTML
    # entities like &quot; - it reads them as "fine". A post with &quot; in
    # it is unreadable for a human, so this must not depend on the model's
    # opinion. Also auto-cleans anything salvageable instead of rejecting a
    # post that only needed a decode.
    try:
        import textsan
    except Exception:
        textsan = None

    # --- DETERMINISTIC READABILITY GATE (runs BEFORE the LLM) -------------
    # The local model is a small one and it reliably waves through raw HTML
    # entities like &quot; - it reads them as "fine". A post with &quot; in
    # it is unreadable for a human, so this must not depend on the model's
    # opinion. Also auto-cleans anything salvageable instead of rejecting a
    # post that only needed a decode.
    if textsan is not None:
        for field, value in (("Title", post_title), ("Post text", text)):
            probs = textsan.problems(value)
            if not probs:
                continue
            return {
                "approved": False,
                "reason": "%s problem: %s" % (field, probs[0]),
                "suggestion": "Auto-clean will fix this automatically - re-run to publish.",
                "issues": probs,
                "auto_fixable": True,
            }

    # --- DUPLICATE + QUALITY GATE (also BEFORE the LLM) -------------------
    # The LLM auditor is currently timing out on every call and failing OPEN
    # (approve anyway), so anything that must not slip through has to be
    # decided by rule, not by the model. This is the gate that stops a repeat
    # of an already-published post, raw model reasoning, repeated-word loops
    # and empty text from reaching real platforms.
    qprobs = _quality_check(text, post_title)
    if qprobs:
        return {
            "approved": False,
            "reason": qprobs[0],
            "suggestion": "Held back automatically. Next attempt will generate fresh text.",
            "issues": qprobs,
            "gate": "quality",
        }

    is_dup, dup_reason = duplicate_check(text, skip_job=skip_job)
    if is_dup:
        return {
            "approved": False,
            "reason": dup_reason,
            "suggestion": "Held back so the same words are not posted twice.",
            "issues": [dup_reason],
            "gate": "duplicate",
        }

    # --- PER-PLATFORM LENGTH CHECK ---------------------------------------
    # Publishes are trimmed word-safe to each platform's real cap, but a post
    # whose text is longer than the platform allows is silently losing its
    # ending. Say so in plain English instead of letting the tail disappear.
    try:
        import content_format
        prof = content_format.PROFILES.get(platform)
        if prof and text and len(text.strip()) > prof["limit"]:
            return {
                "approved": False,
                "reason": ("The text is %d characters long but %s only allows about %d, "
                           "so the end of it would be cut off. It needs to be shorter "
                           "before posting to %s."
                           % (len(text.strip()), platform, prof["limit"], platform)),
                "suggestion": "Held back so nothing gets cut off mid-sentence.",
                "issues": ["too long for %s" % platform],
                "gate": "length",
            }
    except Exception:
        pass

    review = _llm_review(text, platform, post_title, timeout=120)
    if review.get("approved"):
        return review
    return review


def _as_text(v):
    """Coerce whatever the model returned into one plain string.

    Small models return "issues" as a JSON list, a single string, a dict, or
    nothing - and a .strip() on a list raised AttributeError, which with the
    fail-closed auditor blocked every single post."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (list, tuple)):
        parts = []
        for x in v:
            s = _as_text(x)
            if s:
                parts.append(s)
        return "; ".join(parts)
    if isinstance(v, dict):
        return "; ".join(_as_text(x) for x in v.values() if x)
    return str(v).strip()


def _llm_review(text, platform, post_title="", timeout=120):
    """The model's own quality opinion. FAILS CLOSED - see the note below.

    Split out of audit_content() so publish_guard can run the AI step on its own
    without re-running the deterministic gates, and so there is exactly one
    place that decides what to do when the model cannot be reached."""
    prompt = (
        f'You are a strict social media editor. Verify a finished post before it goes live.\n'
        f'Platform: {platform}\n'
        f'Post title: {post_title[:50]}\n'
        f'Length: {len(text)} characters (this is a normal, complete caption - short '
        f'lengths are normal and expected on some platforms, so NEVER reject a post '
        f'just for being short or for not being long.)\n'
        f'Content: "{text[:600]}"\n\n'
        f'Reject ONLY if you can point to one of these actual problems:\n'
        f'1. It ends mid-sentence or is visibly unfinished\n'
        f'2. It says the same thing over and over with no new information\n'
        f'3. It is padded with meaningless filler ("This post is about X. X is Y. '
        f'Y is X. This post is about X.")\n'
        f'4. It contains stray code, braces, symbols or corrupted characters\n'
        f'5. It contains raw HTML entities like &quot; or &amp;\n'
        f'6. The grammar is broken or the meaning is genuinely unclear\n'
        f'7. It is obviously generic filler that would be true of literally any topic\n\n'
        f'If a real person could read this and get something from it, APPROVE it.\n'
        f'Do not invent problems. Do not reject for style, length, or topic choice.\n\n'
        f'Respond with ONLY JSON:\n'
        f'{{"approved": true/false, "reason": "one short plain sentence if rejected, else empty", "issues": []}}\n'
        f'Keep the reason simple and non-technical.'
    )

    # FAIL CLOSED. The auditor used to return approved=True whenever Ollama was
    # unreachable or returned junk, which meant a dead model silently waved
    # every post through - that is exactly how bad posts reached real accounts.
    # the owner's requirement: audit failing is not good, and nothing incomplete or
    # unchecked should ever be published. So every "could not judge" path now
    # BLOCKS, and the caller rotates to a fresh topic rather than shipping it.
    unreachable = {
        "approved": False,
        "reason": ("The AI reviewer could not be reached, so this post was held "
                   "back rather than published unchecked. A new topic will be "
                   "tried instead."),
        "suggestion": "Check that Ollama is running and the model is loaded.",
        "gate": "ai_unreachable",
    }
    try:
        response = ollama_call(prompt, timeout=120)
        if not response:
            log("  audit: model unreachable - holding post back (fail closed)")
            return unreachable

        response = response.strip()
        if response.startswith("{"):
            try:
                data = json.loads(response)
            except json.JSONDecodeError:
                log("  audit: unparseable model reply - holding post back")
                return {
                    "approved": False,
                    "reason": ("The AI reviewer sent back something unreadable, so "
                               "the post was held back instead of guessing."),
                    "suggestion": "",
                    "gate": "ai_unparseable",
                }
            if "approved" not in data:
                return {
                    "approved": False,
                    "reason": ("The AI reviewer did not clearly say yes or no, so "
                               "the post was held back to be safe."),
                    "suggestion": "",
                    "gate": "ai_unclear",
                }
            approved = bool(data.get("approved"))
            # The model is inconsistent about types: it returns "issues" as a
            # list, a string, or nothing at all, and "reason" the same. Calling
            # .strip() on a list raised AttributeError, which - now that the
            # auditor fails closed - would have blocked EVERY post. Coerce
            # anything at all into a plain string.
            reason = _as_text(data.get("reason"))
            issues = _as_text(data.get("issues"))
            if not approved and not reason:
                reason = "Failed quality check"
            return {
                "approved": approved,
                "reason": reason if not approved else "",
                "suggestion": issues if not approved else ""
            }
        log("  audit: non-JSON model reply - holding post back")
        return {
            "approved": False,
            "reason": ("The AI reviewer sent back something unreadable, so the "
                       "post was held back instead of guessing."),
            "suggestion": "",
            "gate": "ai_unparseable",
        }
    except Exception as e:
        log("  audit error: " + str(e)[:100])
        return {
            "approved": False,
            "reason": ("The AI reviewer hit an error, so this post was held back "
                       "rather than published unchecked. A new topic will be tried "
                       "instead."),
            "suggestion": str(e)[:120],
            "gate": "ai_error",
        }


def format_audit_result_simple(audit_result, platform):
    """Format audit result in simple, user-friendly way."""
    if audit_result.get("approved"):
        return f"✅ {platform}: Approved"
    else:
        reason = audit_result.get("reason", "Unknown reason")
        return f"❌ {platform}: {reason}"


if __name__ == "__main__":
    test_text = "This is a great post about meditation and mindfulness."
    result = audit_content(test_text, "instagram", "meditation tips")
    print(json.dumps(result, indent=2))
