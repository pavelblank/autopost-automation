# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - make_post.py
Makes IMAGE posts and TEXT posts (the video pipeline lives in make_video.py).

  image : Ollama writes caption+hashtags -> Pollinations makes the picture -> saved as a post
  text  : Ollama writes the post + hashtags -> saved as a post

Zero API keys. Zero accounts. $0.

Usage:
  python make_post.py image "topic here"
  python make_post.py text  "topic here"
"""
import argparse, json, re, shutil, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db, variety, research

ROOT = (Path(__file__).resolve().parents[1])
OLLAMA = "http://127.0.0.1:11434/api/generate"
def _active_model():
    """The single model configured on the Settings page. No fallback chain
    anymore (removed 2026-09-03, the owner's request) - a fallback cascade was
    what caused Ollama to load multiple large models simultaneously and
    nearly exhaust system memory during the qwen3-27b-iq1 evaluation. Only
    ever one model active at a time now. Read fresh each call, no restart
    needed for a Settings-page model change to take effect."""
    try:
        R = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8"))
        return R.get("ollama_model") or "llama3.2:3b"
    except Exception:
        return "llama3.2:3b"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
W, H = 1080, 1350  # 4:5 - the best-performing feed ratio on IG/FB


def log(m):
    try:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m, flush=True)
    except UnicodeEncodeError:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m.encode("ascii", "replace").decode("ascii"), flush=True)


def ollama(prompt, as_json=False, retries=2, timeout=300):
    """Tries the single active model with retries and exponential backoff.
    keep_alive keeps the model warm between calls so a slow CPU doesn't pay
    a full reload + re-prompt on every step. Timeout is per-call, not per-job:
    a wedged model must fail fast so the scheduler's one-run-at-a-time gate
    can retry it, instead of locking the whole schedule for an hour."""
    model = _active_model()
    last_err = None
    payload = {"model": model, "prompt": prompt, "stream": False, "keep_alive": "30m"}
    if as_json:
        payload["format"] = "json"
    body = json.dumps(payload).encode()
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)["response"].strip()
            if not resp or resp == '"':
                raise RuntimeError("empty model response")
            return resp
        except Exception as e:
            last_err = e
            if a < retries:
                wait_time = 10 * (2 ** a)  # 10s, 20s
                log("  " + model + " attempt " + str(a + 1) + " failed: " + str(e)[:80] + " - retrying in " + str(wait_time) + "s...")
                time.sleep(wait_time)
    raise last_err


POPULAR_TAGS = ["#trending", "#viral", "#fyp", "#explore"]  # generic reach tags, platform-neutral


BROKEN_CONTENT_PATTERNS = (
    r"<think>|</think>",
    r'"thoughts"\s*:',
    r'"reasoning"\s*:',
    r"^\s*reasoning\s*:",
    r"\bthe user is asking\b",
    r"\blet me think\b",
    r"\bi need to make sure\b",
    r"\bi should (respond|proceed)\b",
    r"\bno hidden prompt injection\b",
)


def _content_is_broken(text):
    """Final content-quality gate: catches raw model 'thinking' text or
    unparsed JSON structure leaking through as if it were real post content -
    confirmed happening live with qwen3-27b-iq1 (a caption got saved as
    literally '{"thoughts":"This is a request to generate..."}', another as
    just '...'), and both got published to 5 real platforms before anyone
    caught it. Deterministic rule-based check, not another AI call - asking a
    model that just proved unreliable to judge its own output doesn't
    reliably fix anything, and costs more generation time on top of an
    already-slow model."""
    t = (text or "").strip()
    if len(t) < 8:
        return True
    tl = t.lower()
    if any(re.search(p, tl) for p in BROKEN_CONTENT_PATTERNS):
        return True
    if t.startswith("{") and ('"' in t or "thought" in tl):
        return True
    return False


def _safe_caption_from_topic(topic):
    """Guaranteed-clean fallback caption, never derived from raw model text -
    used only when every generation attempt produced broken content, so
    something publishable always exists instead of garbage or an empty post."""
    safe = (topic or "").strip()
    if not safe:
        return "New post."
    safe = safe[0].upper() + safe[1:]
    if not safe.endswith((".", "!", "?")):
        safe += "."
    return safe


def _add_popular_tags(tags):
    """Guarantee a couple of broad-reach tags alongside the topic-specific ones,
    so posts aren't relying purely on the LLM's guess for discoverability.
    Accepts either format models actually return despite the prompt asking for
    one - a space-separated string (llama3.2/gemma2) or a JSON array (phi4-mini) -
    and also splits any glued "#a#b#c" entry apart (seen from gemma2 elsewhere)."""
    raw_items = tags if isinstance(tags, list) else str(tags or "").split()
    items = []
    for t in raw_items:
        for p in str(t or "").split("#"):
            p = re.sub(r"\s+", "", p)
            if p and "#" + p not in items:
                items.append("#" + p)
    for t in POPULAR_TAGS:
        if t not in items and len(items) < 10:
            items.append(t)
    return " ".join(items)


def write_copy(topic, kind):
    """Returns dict with caption, hashtags, and (for image) an art prompt."""
    if kind == "image":
        p = ('Create a social media IMAGE post about "' + topic + '".\n'
             'Return JSON: {"caption":"...","hashtags":"...","image":"..."}\n'
             'caption = 2-3 punchy sentences, hook first, max 60 words.\n'
             'hashtags = 6 relevant, currently-trending-style hashtags separated by spaces, each starting with #.\n'
             'image = detailed photo description for an image generator, no text in the image.')
    else:
        p = ('Write a social media TEXT post about "' + topic + '".\n'
             'Return JSON: {"caption":"...","hashtags":"..."}\n'
             'caption = 3-4 sentences, strong hook first line, max 90 words, no emoji spam.\n'
             'hashtags = 6 relevant, currently-trending-style hashtags separated by spaces, each starting with #.')
    try:
        import plan_hint
        p += plan_hint.block()
    except Exception:
        pass
    raw = ollama(p, as_json=True, retries=2, timeout=300)
    try:
        d = json.loads(raw)
        cap = (d.get("caption") or "").strip()
        if cap and not _content_is_broken(cap):
            d["hashtags"] = _add_popular_tags(d.get("hashtags") or "")
            return d
        log("  caption failed content check (broken/empty) - trying plain-text fallback")
    except Exception as e:
        log("  JSON parse failed (" + str(e) + ") -> plain-text fallback")
    txt = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    if txt and not _content_is_broken(txt):
        return {"caption": txt[:600], "hashtags": " ".join(POPULAR_TAGS), "image": topic + ", cinematic photo"}
    log("  plain-text fallback also broken - using guaranteed-clean topic-based caption")
    return {"caption": _safe_caption_from_topic(topic), "hashtags": " ".join(POPULAR_TAGS),
            "image": topic + ", cinematic photo"}


def write_copy_for_platform(topic, kind, platform):
    """Platform-specific caption generation with optimized length/style for each platform.
    Platforms: telegram, youtube, tiktok, bluesky, mastodon, discord, facebook, instagram."""
    limits = {
        "telegram": (1024, "casual, engaging, use hashtags"),
        "youtube": (4500, "detailed, informative, use hashtags"),
        "tiktok": (3000, "viral, trendy, hashtag-heavy, use emojis"),
        "bluesky": (295, "concise, punchy, minimal hashtags"),
        "mastodon": (490, "thoughtful, community-focused, minimal hashtags"),
        "discord": (1900, "friendly, conversational, no hashtags"),
        "facebook": (4000, "engaging storytelling, use hashtags"),
        "instagram": (2200, "visual storytelling, emoji-friendly, hashtag-optimized"),
    }
    char_limit, style = limits.get(platform, (1024, "engaging"))

    if kind == "image":
        p = (f'Create a social media IMAGE post about "{topic}" optimized for {platform}.\n'
             f'Return JSON: {{"caption":"...","hashtags":"...","image":"..."}}\n'
             f'caption = optimized for {platform} ({char_limit} char limit), style: {style}\n'
             f'hashtags = {6 if char_limit > 500 else 2} trending hashtags, each starting with #.\n'
             f'image = detailed photo description for an image generator, no text in the image.')
    else:
        p = (f'Write a social media TEXT post about "{topic}" optimized for {platform}.\n'
             f'Return JSON: {{"caption":"...","hashtags":"..."}}\n'
             f'caption = optimized for {platform} ({char_limit} char limit), style: {style}\n'
             f'hashtags = {6 if char_limit > 500 else 2} trending hashtags, each starting with #.')

    raw = ollama(p, as_json=True, retries=2, timeout=300)
    try:
        d = json.loads(raw)
        cap = (d.get("caption") or "").strip()
        if cap and not _content_is_broken(cap):
            d["hashtags"] = _add_popular_tags(d.get("hashtags") or "")
            return d
    except Exception:
        pass
    txt = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    if txt and not _content_is_broken(txt):
        return {"caption": txt[:char_limit], "hashtags": " ".join(POPULAR_TAGS), "image": topic + ", cinematic photo"}
    return {"caption": _safe_caption_from_topic(topic), "hashtags": " ".join(POPULAR_TAGS),
            "image": topic + ", cinematic photo"}


def _fresh_topic(avoid, limit=40):
    """Next-best unused topic that is NOT the one that just failed.
    Returns (topic_title) or None when there is nothing left to try."""
    try:
        import research
        rows = research.unused(limit)
    except Exception as e:
        log("    could not look up another topic: " + str(e)[:70])
        return None
    avoid_n = set()
    for a in (avoid or []):
        try:
            import re as _re
            avoid_n.add(_re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).strip())
        except Exception:
            pass
    for r in rows:
        t = (r["title"] or "").strip()
        if not t:
            continue
        key = "".join(ch for ch in t.lower() if ch.isalnum() or ch == " ")
        key = " ".join(key.split())
        if key in avoid_n:
            continue
        try:
            db.note_topic(t)
        except Exception:
            pass
        try:
            db.mark_topic_used_by_title(t)
        except Exception:
            pass
        return t
    return None


def regenerate_on_audit_fail(topic, kind, run_id, max_attempts=3, topic_switches=2):
    """Auto-regenerate content if audit fails. Up to 3 attempts PER TOPIC.
    If every attempt on a topic is rejected, that topic is abandoned and a
    DIFFERENT unused topic is tried - asking the same model to write about the
    same subject again just reproduces the same rejected text, so rotating the
    topic is what actually recovers the run (the owner's request).

    Returns (content_dict, success_bool, attempt_number, used_topic)."""
    import editorial_auditor

    tried = [topic]
    current = topic
    attempt = 0
    total = 0

    for switch in range(topic_switches + 1):
        if switch > 0:
            nxt = _fresh_topic(tried)
            if not nxt:
                log("  no unused topic left to switch to")
                break
            log(f"  all attempts on \"{current[:50]}\" were rejected - switching to a NEW topic: \"{nxt[:60]}\"")
            db.log_audit(run_id, "generic", "regenerating",
                         f"Topic rotated after {total} rejected attempt(s)", total + 1)
            current = nxt
            tried.append(nxt)

        for a in range(1, max_attempts + 1):
            total += 1
            attempt = a
            log(f"  attempt {total} (topic {switch+1}/{topic_switches+1}, try {a}/{max_attempts}): generating content...")
            try:
                content = write_copy(current, kind)
            except Exception as e:
                log(f"    generation error: {str(e)[:100]} - using fallback")
                content = {"caption": _safe_caption_from_topic(current), "hashtags": " ".join(POPULAR_TAGS), "image": current + ", cinematic photo"}
            caption = (content.get("caption") or "").strip()

            if not caption:
                log("    generation failed, retrying...")
                db.log_audit(run_id, "generic", "regenerating", f"Empty content on attempt {total}", total)
                continue

            audit = editorial_auditor.audit_content(caption, "generic", current[:50])
            if audit.get("approved"):
                log(f"    PASSED AUDIT on attempt {total} - \"{current[:50]}\"")
                db.log_audit(run_id, "generic", "approved", f"Regenerated successfully on attempt {total}", total)
                return (content, True, total, current)

            reason = audit.get("reason", "Unknown issue")
            gate = audit.get("gate", "unknown")
            log(f"    AUDIT FAILED [{gate}]: {reason}")
            db.log_audit(run_id, "generic", "regenerating", f"{reason} (attempt {total})", total)

            if a < max_attempts:
                log("    retrying with a fresh roll of the dice...")

    log(f"  FAILED after {total} attempts across {len(tried)} topic(s) - giving up")
    db.log_audit(run_id, "generic", "failed",
                 f"Max {total} attempts across {len(tried)} topic(s) reached", total)
    return (None, False, total, current)


def editorial_review(caption, topic):
    """A second pass over the draft before it's considered ready to publish -
    the owner's explicit request after seeing bland/generic captions reach real
    accounts. The structural _content_is_broken() check only catches gross
    technical failures (raw JSON/reasoning leaking through) - this is a
    genuine quality pass, judging clarity/engagement/professionalism the way
    a human editor would, not just "is this valid text". Runs the SAME
    structural check on its OWN output too (an editor pass can itself
    produce broken text) - if the review looks worse or broken, the
    original already-validated draft is kept instead, never lost."""
    prompt = (
        'You are an expert social media editor reviewing a draft post before it goes live.\n'
        'Topic: "' + (topic or "") + '"\n'
        'Draft: "' + caption + '"\n\n'
        "Judge it as a professional publisher would: is it clear, engaging, and does it read "
        "like it was written by a skilled human writer - not generic, robotic, or AI-sounding?\n"
        "If it is already good, return it EXACTLY unchanged.\n"
        "If it needs improvement, rewrite it - same topic, similar length, more natural and engaging.\n"
        "Return ONLY the final post text. No explanation, no quotes, no labels, no notes."
    )
    try:
        reviewed = ollama(prompt, timeout=300).strip()
        reviewed = re.sub(r'^["\']|["\']$', "", reviewed).strip()
        if reviewed and not _content_is_broken(reviewed) and len(reviewed) <= max(len(caption) * 3, 200):
            return reviewed
        log("  editorial review produced no usable improvement - keeping original draft")
    except Exception as e:
        log("  editorial review failed, keeping original draft: " + str(e)[:120])
    return caption


def generate_blog_body(topic, caption):
    """Longer-form paragraph for the SocialBlog site specifically - see the
    identical function + full rationale in make_video.py. Never touches what
    actually gets posted to any social platform, only what sync.py shows on
    the blog for this post."""
    prompt = (
        'Write a longer blog-post paragraph (120-200 words) about: "' + (topic or "") + '"\n'
        "This is for a blog reader, not a social caption - give real context and detail, "
        "not just a punchy hook. Warm, engaging tone. Return ONLY the paragraph text, "
        "no title, no hashtags, no quotes around it."
    )
    try:
        raw = ollama(prompt, timeout=300).strip()
        raw = re.sub(r'^["\']|["\']$', "", raw).strip()
        if len(raw) > 40 and not _content_is_broken(raw):
            return raw[:2000]
        log("  blog body failed content check - using caption as fallback")
    except Exception as e:
        log("  blog body generation failed, using caption as fallback: " + str(e)[:120])
    return caption


def make_image(prompt, dst, seed):
    """Topic-matched image (Pollinations -> Wikimedia Commons -> Openverse) so a
    broken image API falls back to a real on-topic photo, never a black frame."""
    try:
        import image_sources
        return image_sources.make_image(prompt, dst, seed, fallback_log=log)
    except Exception:
        return False


ESRGAN_PY = str(Path(__file__).resolve().parents[1] / "engine" / "venv" / "Scripts" / "python.exe")
ESRGAN_CLI = str(Path(__file__).resolve().parents[1] / "engine" / "upscale_cli.py")


def upscale_image(path):
    """Sharpen/2x-upscale the Pollinations image via Real-ESRGAN (own venv,
    GPU-accelerated - confirmed working on the GTX 1650, 2026-09-13).
    Best-effort: on any failure, keep the original image and move on - never
    let a quality-enhancement step block a real publish."""
    tmp = path + ".up.jpg"
    try:
        r = subprocess.run([ESRGAN_PY, ESRGAN_CLI, path, tmp, "2"],
                            capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and "UPSCALE_OK" in r.stdout and Path(tmp).stat().st_size > 3000:
            shutil.move(tmp, path)
            return True
        log("  upscale skipped (non-fatal): " + (r.stderr or r.stdout)[-200:])
    except Exception as e:
        log("  upscale skipped (non-fatal): " + str(e)[:150])
    Path(tmp).unlink(missing_ok=True)
    return False


def add_text_overlay(image_path, caption, hashtags=""):
    """Add text overlay to image with caption visible on the image itself.
    Makes captions readable directly from the image (mobile-first optimization
    for TikTok, Instagram, etc.). Adds semi-transparent background behind text
    for readability over any background color."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(image_path)
        draw = ImageDraw.Draw(img, 'RGBA')

        text = caption.strip()
        if hashtags:
            text = text + "\n\n" + hashtags.strip()

        font_size = 48
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        margin = 30
        max_width = img.width - (2 * margin)
        lines = []
        for line in text.split('\n'):
            if line.strip():
                while line:
                    try:
                        bbox = draw.textbbox((0, 0), line, font=font)
                        line_width = bbox[2] - bbox[0]
                        if line_width <= max_width:
                            lines.append(line)
                            line = ""
                        else:
                            words = line.split()
                            if not words:
                                lines.append(line[:20])
                                line = line[20:]
                            else:
                                partial = ""
                                for w in words:
                                    test = (partial + " " + w).strip()
                                    bbox = draw.textbbox((0, 0), test, font=font)
                                    if bbox[2] - bbox[0] <= max_width:
                                        partial = test
                                    else:
                                        break
                                if partial:
                                    lines.append(partial)
                                    line = line[len(partial):].lstrip()
                                else:
                                    lines.append(w)
                                    line = line[len(w):].lstrip()
                    except Exception:
                        lines.append(line[:50])
                        line = line[50:]
            else:
                lines.append("")

        text_height = 0
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_height += bbox[3] - bbox[1] + 10

        y_start = img.height - text_height - (2 * margin)

        background_padding = 15
        bg_y_start = y_start - background_padding
        bg_y_end = img.height - margin + background_padding
        draw.rectangle(
            [(margin - background_padding, bg_y_start),
             (img.width - margin + background_padding, bg_y_end)],
            fill=(0, 0, 0, 200)
        )

        y = y_start
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_height = bbox[3] - bbox[1]
            draw.text((margin, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_height + 10

        img.save(image_path, quality=95)
        return True
    except Exception as e:
        log("  text overlay skipped (non-fatal): " + str(e)[:150])
        return False


def main():
    import atexit
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["image", "text"])
    ap.add_argument("topic")
    ap.add_argument("--preview", action="store_true", help="Request preview before publishing")
    ap.add_argument("--lease", action="store_true", help="take over the one-generation-at-a-time lease (set by dashboard/scheduler)")
    a = ap.parse_args()

    # ONE-GENERATION-AT-A-TIME: take over the gate for our whole run (the
    # spawner already acquired it); release on exit (auto-stolen if hard-killed).
    if a.lease:
        db.takeover_gen_lease()
        atexit.register(db.release_gen_lease)

    run_id = db.start_run(a.topic, kind=a.kind)
    db.note_topic(a.topic)

    # creative check - advisory, never blocks, see make_video.py for the same pattern
    try:
        import research as _research
        fresh, similar = _research.check_fresh(a.topic)
        if fresh:
            log("creative check: fresh angle, no close recent match")
        else:
            log("creative check: WARNING - similar to already-used topic: \"" + similar[:70] + "\"")
    except Exception as e:
        log("creative check skipped: " + str(e)[:100])

    job = ROOT / "out" / ("post_" + a.kind + "_" + time.strftime("%Y%m%d_%H%M%S"))
    job.mkdir(parents=True, exist_ok=True)
    category = variety.classify_category(a.topic)
    (job / "category.txt").write_text(category, encoding="utf-8")
    t0 = time.time()
    log("kind : " + a.kind)
    log("topic: " + a.topic)
    log("category: " + category)
    log("work : " + str(job))

    try:
        db.set_stage(run_id, "1/2 writing copy")
        log("1/2 writing copy (local Ollama, with auto-regeneration on audit fail)...")
        c, success, attempts, used_topic = regenerate_on_audit_fail(a.topic, a.kind, run_id, max_attempts=3)
        topic = used_topic or a.topic

        if not success:
            raise RuntimeError(f"Content generation failed after {attempts} attempts - audit kept rejecting")

        if topic != a.topic:
            log(f"    topic switched: \"{a.topic[:50]}\" -> \"{topic[:50]}\"")
            try:
                db.note_topic(topic)
            except Exception:
                pass

        db.record_regeneration_attempt(run_id, attempts)
        caption = (c.get("caption") or "").strip()
        tags = (c.get("hashtags") or "").strip()
        log(f"    generated successfully (attempt {attempts})")
        log("    draft caption: " + caption[:90])

        db.set_stage(run_id, "classifying topic attributes")
        log("classifying: category, tone, sentiment...")
        category, tone, sentiment = research.classify_topic_attributes(topic, caption)
        log(f"    category: {category}, tone: {tone}, sentiment: {sentiment}")

        db.set_stage(run_id, "calculating variety score")
        log("calculating diversity score vs recent posts...")
        variety_score = research.calculate_variety_score(topic)
        log(f"    variety score: {variety_score:.2f}/1.0 (higher = more unique)")

        db.update_topic_attributes(run_id, category, tone, sentiment, variety_score)

        db.set_stage(run_id, "1b/2 editorial review")
        log("1b/2 editorial review (local Ollama, checking for quality/clarity before publish)...")
        caption = editorial_review(caption, topic)
        log("    final caption: " + caption[:90])
        log("    tags   : " + tags[:90])

        img_path = None
        img_seed = None
        img_prompt = None
        if a.kind == "image":
            db.set_stage(run_id, "2/2 generating image")
            log("2/2 generating image (Pollinations)...")
            img_path = job / "post.jpg"
            img_seed = int(time.time()) % 99999
            img_prompt = c.get("image") or a.topic
            if not make_image(img_prompt, str(img_path), img_seed):
                raise RuntimeError("image generation failed after 3 tries")
            log("    image: " + str(round(img_path.stat().st_size / 1024)) + " KB")
            upscale_image(str(img_path))
            log("    image after upscale: " + str(round(img_path.stat().st_size / 1024)) + " KB")
            # NOTE: NO text overlay on image - caption/hashtags go in post.json & caption.txt only
            # User requirement: "Image generation - dont add any tag inside image but outside text"
            log("    (skipping text overlay - caption/hashtags stored separately)")

        blog_body = generate_blog_body(a.topic, caption)
        payload = {"kind": a.kind, "topic": a.topic, "caption": caption,
                   "hashtags": tags, "text": (caption + "\n\n" + tags).strip(),
                   "media": str(img_path) if img_path else None,
                   "image_seed": img_seed, "image_prompt": img_prompt,
                   "created": time.time(), "blog_body": blog_body}
        (job / "post.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (job / "caption.txt").write_text(payload["text"], encoding="utf-8")

        size = int(img_path.stat().st_size / 1024) if img_path else len(payload["text"]) // 1024

        if a.preview:
            db.request_preview(run_id)
            db.finish_run(run_id, status="ok", stage="preview_pending", scenes=1,
                          build_secs=round(time.time() - t0, 1), size_kb=size, path=str(job))
            log("DONE in " + str(int(time.time() - t0)) + "s -> " + str(job))
            log("??  AWAITING PREVIEW APPROVAL")
            log(f"   Topic: {a.topic[:60]}")
            log(f"   Caption: {caption[:80]}")
            log("   (User must approve via dashboard before publishing)")
        else:
            db.finish_run(run_id, status="ok", stage="done", scenes=1,
                          build_secs=round(time.time() - t0, 1), size_kb=size, path=str(job))
            log("DONE in " + str(int(time.time() - t0)) + "s -> " + str(job))
            log("--- POST READY FOR PUBLISHING ---")
    except Exception as e:
        db.finish_run(run_id, status="failed", error=str(e)[:500])
        log("ERROR: " + str(e))
        raise


if __name__ == "__main__":
    main()
