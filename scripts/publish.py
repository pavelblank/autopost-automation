# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - publish.py
Sends finished content to social platforms. One function per platform, each independent.

  python publish.py --job <folder>            publish everything in a job folder
  python publish.py --text "hello"            quick text test
  python publish.py --setup-telegram          find and save your chat id

Platforms are switched on/off in config/connections.json.
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import argparse, json, mimetypes, re, subprocess, sys, time, urllib.request, uuid
try:
    import requests
except ImportError:
    requests = None
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db
import content_format
import editorial_auditor

ROOT = (Path(__file__).resolve().parents[1])
CONN = ROOT / "config" / "connections.json"
TG = "https://api.telegram.org/bot"


def log(m):
    try:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m, flush=True)
    except UnicodeEncodeError:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m.encode("ascii", "replace").decode("ascii"), flush=True)


def load_conn():
    if CONN.exists():
        try:
            return json.loads(CONN.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_conn(d):
    CONN.parent.mkdir(parents=True, exist_ok=True)
    CONN.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _post(url, fields, files=None, timeout=300):
    """multipart/form-data POST using only the standard library."""
    b = "----ap" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += ("--" + b + "\r\nContent-Disposition: form-data; name=\"" + k + "\"\r\n\r\n"
                 + str(v) + "\r\n").encode("utf-8")
    for k, path in (files or {}).items():
        p = Path(path)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        body += ("--" + b + "\r\nContent-Disposition: form-data; name=\"" + k +
                 "\"; filename=\"" + p.name + "\"\r\nContent-Type: " + ctype + "\r\n\r\n").encode("utf-8")
        body += p.read_bytes() + b"\r\n"
    body += ("--" + b + "--\r\n").encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "multipart/form-data; boundary=" + b})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------- YouTube
RULES = ROOT / "config" / "rules.json"


def _sanitize(s):
    """Decode leftover HTML entities (scraped topics arrive as &quot; etc, and
    the site renders them verbatim). Never raises - a failure here must not
    cost us the post."""
    if not s:
        return s
    try:
        import textsan
        return textsan.sanitize(s)
    except Exception:
        try:
            import html as _h
            return _h.unescape(s)
        except Exception:
            return s


def _job_topic(job):
    """Find the run's topic from brain.db by matching the output path if possible."""
    try:
        with db.conn() as c:
            r = c.execute("SELECT topic FROM runs WHERE path=? ORDER BY id DESC LIMIT 1",
                          (str(job),)).fetchone()
            if not r:
                r = c.execute("SELECT topic FROM runs WHERE path LIKE ? ORDER BY id DESC LIMIT 1",
                              (str(job) + "%",)).fetchone()
            return (r["topic"] if r else "") or job.name
    except Exception:
        return job.name


def _job_run_id(job):
    """Find the run's id from brain.db by matching the output path - same lookup
    _job_topic uses. Lets every posts row record which run it belongs to (the
    posts.run_id column existed but was never actually populated by any INSERT
    here, so every post-to-run link was silently NULL - found while wiring the
    blog's sync script, which needs this to attach YouTube video IDs to posts)."""
    try:
        with db.conn() as c:
            r = c.execute("SELECT id FROM runs WHERE path=? ORDER BY id DESC LIMIT 1",
                          (str(job),)).fetchone()
            if not r:
                r = c.execute("SELECT id FROM runs WHERE path LIKE ? ORDER BY id DESC LIMIT 1",
                              (str(job) + "%",)).fetchone()
            return r["id"] if r else None
    except Exception:
        return None


def _rules():
    if RULES.exists():
        try:
            return json.loads(RULES.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def clean_title(topic):
    """Human title from a topic: strip [image]/[text] tags, tags, extra spaces, cap 95 chars."""
    t = re.sub(r"\[(image|text)\]", "", topic or "", flags=re.I).strip()
    t = re.sub(r"#\w+", "", t)                      # drop existing hashtags
    t = re.sub(r"\s+", " ", t).strip(" .:-")
    return t[:94].rstrip()[:95]


def topic_hashtags(topic):
    """Fallback hashtags (list) from the top meaningful words in the topic. Used only
    if the LLM hook generator fails."""
    stop = {"the","and","for","are","with","that","this","from","you","your",
            "how","what","when","why","who","a","an","in","on","to","of","is",
            "it","s","more","new","over","its","they","be","as","at","by","or"}
    words = re.findall(r"[A-Za-z0-9]+", (topic or "").lower())
    picks = []
    for w in words:
        if len(w) < 4 or w in stop or w in picks:
            continue
        picks.append(w)
        if len(picks) >= 5:
            break
    tags = ["#" + w for w in picks] or []
    return tags + ["#shorts", "#viral"]


POPULAR_TAGS = ["#shorts", "#viral", "#fyp", "#trending", "#explore"]


CATEGORY_KEYS = ["facts_mystery_science", "lifestyle_fashion_beauty", "fitness_motivation",
                 "finance_business", "self_healing_spiritual", "default"]


GENERIC_TITLE_PATTERNS = (
    r"unlock the secret",
    r"the secret to",
    r"here'?s the secret",
    r"discover the (secret|truth)",
)


def _title_matches_topic(title, topic):
    """Guard against the small local model drifting to a memorized generic
    template instead of actually describing this topic - see the identical
    guard + full explanation in make_video.py's generate_hook_meta()."""
    tl = (title or "").lower()
    if any(re.search(p, tl) for p in GENERIC_TITLE_PATTERNS):
        return False
    def kw(s):
        return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) >= 4}
    return bool(kw(title) & kw(topic))


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
    """Final content-quality gate - see the identical function + full
    rationale in make_video.py/make_post.py. Deterministic, not another AI
    call."""
    t = (text or "").strip()
    if len(t) < 8:
        return True
    tl = t.lower()
    if any(re.search(p, tl) for p in BROKEN_CONTENT_PATTERNS):
        return True
    if t.startswith("{") and ('"' in t or "thought" in tl):
        return True
    return False


def generate_hook_meta(topic):
    """Ask the local model for a catchy title + 1-2 sentence hook + popular hashtags
    + which content category this belongs to (drives playlist sorting).
    Falls back to a deterministic version if Ollama is unreachable."""
    import urllib.request as ur
    prompt = (
        'Write YouTube Shorts metadata for a video about: "' + (topic or "") + '"\n'
        'Return ONLY JSON: {"title":"...","hook":"...","hashtags":["#tag1","#tag2",...],"category":"..."}\n'
        "title = catchy, curiosity-driven, under 70 characters, no quotes, no hashtags in it.\n"
        "hook = 1-2 punchy sentences a viewer would want to read, no hashtags in it.\n"
        "hashtags = 6-8 items: mix a few POPULAR/trending general tags "
        "(#shorts #viral #fyp #trending style) with a few specific to the topic. "
        "Every item must start with # and have no spaces.\n"
        "category = exactly one of: " + ", ".join(CATEGORY_KEYS) + " (pick the closest match)."
    )
    R = _rules()
    model = R.get("ollama_model") or "llama3.2:3b"
    raw, last_err = None, None
    try:
        body = json.dumps({"model": model, "prompt": prompt,
                           "stream": False, "format": "json"}).encode()
        req = ur.Request("http://127.0.0.1:11434/api/generate", data=body,
                         headers={"Content-Type": "application/json"})
        with ur.urlopen(req, timeout=60) as r:
            raw = json.load(r)["response"]
    except Exception as e:
        last_err = e
        raw = None
    try:
        if raw is None:
            raise last_err or RuntimeError("no model available")
        d = json.loads(raw)
        title = (d.get("title") or "").strip()[:95]
        hook = (d.get("hook") or "").strip()
        # some models (seen with gemma2) glue every hashtag into one string like
        # "#a#b#c" instead of separate array items - split those apart too
        tags = []
        for t in (d.get("hashtags") or []):
            for p in (t or "").split("#"):
                p = re.sub(r"\s+", "", p)
                if p and "#" + p not in tags:
                    tags.append("#" + p)
        tags = tags[:8]
        category = (d.get("category") or "default").strip()
        if category not in CATEGORY_KEYS:
            category = "default"
        if title and hook and tags:
            if _content_is_broken(title) or _content_is_broken(hook):
                log("  title/hook failed content check (looks like raw model reasoning) - using topic-based fallback")
                raise ValueError("broken content detected")
            if not _title_matches_topic(title, topic):
                log(f"  title didn't reference the topic ('{title[:40]}' for '{(topic or '')[:40]}') - using the topic itself instead")
                title = clean_title(topic)
            return title, hook, tags, category
    except Exception as e:
        log("  hook generation failed, using fallback: " + str(e)[:120])
    return clean_title(topic), (topic or "").strip(), topic_hashtags(topic), "default"


def _job_meta(video_path):
    """meta.json written by make_video.py (title/hook/hashtags, generated once so
    every platform posts the same caption). None if this job predates that file."""
    mf = Path(video_path).parent / "meta.json"
    if not mf.exists():
        return None
    try:
        d = json.loads(mf.read_text(encoding="utf-8"))
        if d.get("title") and d.get("hook") and d.get("hashtags"):
            return d["title"], d["hook"], list(d["hashtags"]), None
    except Exception:
        pass
    return None


def _job_blog_body(video_path):
    """Longer-form paragraph make_video.py generated for this job (meta.json's
    blog_body), if any. Used to give long-form-capable platforms (Telegram,
    YouTube description, TikTok, Discord) real content instead of the same
    short 1-2 sentence hook everything else gets."""
    mf = Path(video_path).parent / "meta.json"
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8")).get("blog_body")
    except Exception:
        return None


def youtube_send(topic, video_path, description="", run_id=None):
    """Upload a Short to the connected YouTube channel with a catchy title, a real
    1-2 sentence hook, popular + topic hashtags, and auto-sorted into a per-category
    playlist. Privacy: connection setting if present, else
    rules['rules']['first_upload_privacy']."""
    import youtube
    conn = load_conn()
    yt = conn.get("youtube", {})
    meta = _job_meta(video_path)
    if meta:
        title, hook, tags, _ = meta
        category = "default"
    else:
        title, hook, tags, category = generate_hook_meta(topic)
    catf = Path(video_path).parent / "category.txt"   # make_video.py already classified
    if catf.exists():                                  # this once - reuse it so the
        c = catf.read_text(encoding="utf-8").strip()    # playlist always matches the
        if c:                                           # video's actual visual style
            category = c
    for t in POPULAR_TAGS:                      # make sure the reach tags are always present
        if t not in tags and len(tags) < 10:
            tags.append(t)
    blog_body = _job_blog_body(video_path)
    body = content_format.format_text("youtube", title=title, hook=hook, tags=tags, blog_body=blog_body)
    rules = _rules()
    privacy = (yt.get("privacy") or
               rules.get("rules", {}).get("first_upload_privacy", "private"))
    vid = youtube.upload(str(video_path), title=title, description=body,
                         tags=[t.lstrip("#") for t in tags], privacy=privacy)
    url = "https://youtu.be/" + vid
    pid = youtube.ensure_playlist(category)      # None if token lacks playlist scope - fine, skip
    if pid:
        youtube.add_to_playlist(pid, vid)
        log("   playlist: " + youtube.PLAYLIST_NAMES.get(category, category))
    with db.conn() as c:
        c.execute("INSERT INTO posts(platform,posted,remote_id,url,status,category,run_id) VALUES(?,?,?,?,?,?,?)",
                  ("youtube", time.time(), vid, url, "ok", category, run_id))
    log("   youtube: " + title[:60] + "  ->  " + url + "  (" + privacy + ")")
    return url
    return url


# ---------------------------------------------------------------- Telegram
def telegram_setup(token):
    """Read pending updates to discover the chat id. User must message the bot first."""
    with urllib.request.urlopen(TG + token + "/getUpdates", timeout=30) as r:
        data = json.load(r)
    found = []
    for u in data.get("result", []):
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member")
        if not msg:
            continue
        c = msg.get("chat", {})
        if c.get("id"):
            found.append((c["id"], c.get("type"), c.get("title") or c.get("first_name") or ""))
    return list(dict.fromkeys(found))


def started_from_tests():
    """True when this process was started from the tools\\ folder (a test, a simulation, the checklist).
    Such a process must never message the owner or change the live schedule: on 2026-10-11 test runs sent
    placeholder alerts ('What happened: x') and corrupted the live slot-claim file three times."""
    try:
        a0 = _os.path.abspath(sys.argv[0]).lower()
        return (_os.sep + "autopost" + _os.sep + "tools" + _os.sep) in a0
    except Exception:
        return False


def telegram_send(token, chat_id, text="", media=None, kind="text"):
    if started_from_tests():
        print("[test guard] Telegram message NOT sent (this process was started from tools\\): " + (text or "")[:60])
        return 0
    if media and kind == "video":
        r = _post(TG + token + "/sendVideo",
                  {"chat_id": chat_id, "caption": text[:1024], "supports_streaming": "true"},
                  {"video": media})
    elif media:
        r = _post(TG + token + "/sendPhoto",
                  {"chat_id": chat_id, "caption": text[:1024]}, {"photo": media})
    else:
        r = _post(TG + token + "/sendMessage", {"chat_id": chat_id, "text": text[:4096]})
    if not r.get("ok"):
        raise RuntimeError("telegram: " + json.dumps(r)[:300])
    return r["result"].get("message_id")


def send_audit_alert(conn, job_name, audit_logs, all_approved):
    """RETIRED (2026-10-11): HELENA's single report already says whether the post is live and verified, so this
    second 'POST APPROVED' message was a duplicate. Kept as a no-op so old callers still work."""
    return
    try:
        tg = conn.get("telegram", {})
        if not tg.get("connected") or not tg.get("token") or not tg.get("chat_id"):
            return

        if all_approved:
            msg = f"✅ POST APPROVED\nTopic: {job_name[:50]}\nAll platforms passed audit."
        else:
            msg = f"⚠️  AUDIT ISSUES\nTopic: {job_name[:50]}\n\n"
            for log in audit_logs:
                platform = log["platform"]
                status = log["status"]
                reason = log["reason"]
                if status == "rejected":
                    msg += f"❌ {platform}: {reason}\n"

        telegram_send(tg["token"], tg["chat_id"], msg)
    except Exception:
        pass


# ---------------------------------------------------------------- driver
def _recently_published_duplicate(topic, exclude_run_id, hours=24):
    """Hard gate (unlike research.check_fresh, which only warns at generation
    time): is there ALREADY a published=1 run (other than this one - excluded
    by id, NOT by topic text, since an identical topic string on a different
    run is exactly the collision we're catching) in the last `hours` whose
    topic overlaps >=60% by keyword with this one? Same similarity math as
    research.check_fresh, just enforced at the moment of publish - the point
    where a collision (two processes picking the same fresh topic within
    minutes of each other, e.g. the live scheduler and a manual run) would
    otherwise actually double-post to every platform. Returns the matching
    already-published topic string, or None."""
    try:
        import research
    except Exception:
        return None
    kw = research._keywords(topic)
    if len(kw) < 2:
        return None
    with db.conn() as c:
        rows = c.execute(
            "SELECT topic FROM runs WHERE published=1 AND topic IS NOT NULL "
            "AND id != ? AND started > strftime('%s','now', ?)",
            (exclude_run_id, f"-{hours} hours")).fetchall()
    for r in rows:
        other = r["topic"]
        if not other:
            continue
        okw = research._keywords(other)
        if okw and len(kw & okw) / max(len(kw), 1) >= 0.6:
            return other
    return None


def publish_job(job_dir, only=None):
    """Detect what a job folder holds and send it everywhere that's connected.
    only: optional set/list of platform keys - when given, hard-restricts
    publishing to ONLY those platforms (everything else is skipped, even if
    connected). Used by TikTok's own 3x/day track so those videos never also
    go to YouTube/Telegram/etc."""
    only = set(only) if only else None
    def allowed(p):
        return only is None or p in only
    job = Path(job_dir)
    text, media, kind = "", None, "text"

    _topic_now = _job_topic(job)
    run_id = _job_run_id(job)

    dup = _recently_published_duplicate(_topic_now, exclude_run_id=run_id)
    if dup:
        log("SKIPPED (duplicate): \"" + _topic_now[:70] + "\" is too similar to "
            "already-published \"" + dup[:70] + "\" within the last 24h - not sending "
            "to any platform.")
        return 0

    # Check if post is pending preview approval
    try:
        with db.conn() as c:
            preview_status = c.execute(
                "SELECT preview_requested, preview_action FROM runs WHERE id=?",
                (run_id,)
            ).fetchone()

        if preview_status and preview_status["preview_requested"] == 1:
            if preview_status["preview_action"] is None:
                log("⏸️  PREVIEW PENDING: \"" + _topic_now[:70] + "\"")
                log("   Awaiting user approval via dashboard")
                log("   (Not publishing until user reviews)")
                return 0
            elif preview_status["preview_action"] == "discard":
                log("❌ DISCARDED: \"" + _topic_now[:70] + "\" - user rejected in preview")
                return 0
            elif preview_status["preview_action"] == "postpone":
                log("⏸️  POSTPONED: \"" + _topic_now[:70] + "\" - user postponed")
                return 0
    except Exception:
        pass  # If preview check fails, continue with publishing

    pj = job / "post.json"
    if pj.exists():
        d = json.loads(pj.read_text(encoding="utf-8"))
        text, kind = d.get("text", ""), d.get("kind", "text")
        media = d.get("media")
    elif (job / "final.mp4").exists():
        kind, media = "video", str(job / "final.mp4")
        cap = job / "caption.txt"
        text = cap.read_text(encoding="utf-8") if cap.exists() else job.name
    else:
        raise RuntimeError("nothing publishable found in " + str(job))

    # Structured fields (title/hook/hashtags/long-form body) for per-platform
    # formatting via content_format - falls back to the flat `text` above for
    # older job folders that predate meta.json/post.json carrying these.
    title, hook, tags, blog_body = "", text, [], None
    if pj.exists():
        title = clean_title(d.get("topic", "")) or (d.get("caption") or "")[:95]
        hook = d.get("caption") or text
        raw_tags = d.get("hashtags")
        tags = raw_tags if isinstance(raw_tags, list) else \
               [t for t in (raw_tags or "").split() if t.startswith("#")]
        blog_body = d.get("blog_body")
    else:
        mf = job / "meta.json"
        if mf.exists():
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
                title = m.get("title") or title
                hook = m.get("hook") or hook
                tags = list(m.get("hashtags") or tags)
                blog_body = m.get("blog_body")
            except Exception:
                pass

    conn = load_conn()
    sent = 0
    failed = []   # [(platform_label, error_str)] - anything here gets a real alert, not just a log line

    # telegram = private monitoring chat (the owner watches every item here);
    # telegram_channel = the real public channel - same content, both independent,
    # one failing never blocks the other.
    tg_profile = "telegram" if media else "telegram_text"
    tg_text = content_format.format_text(tg_profile, title=title, hook=hook, tags=tags, blog_body=blog_body)

    # Audit under the SAME profile that built the text: a text-only post is built with the
    # 4096 "telegram_text" cap but was audited as "telegram" (1024, the media-caption cap),
    # so every long text post was rejected for Telegram and never sent.
    audit_result = editorial_auditor.audit_content(tg_text, tg_profile, title, hashtags=tags, skip_job=job.name)
    if not audit_result.get("approved"):
        log("❌ AUDIT FAILED for telegram: " + audit_result.get("reason", "Unknown"))
        db.log_audit(run_id, "telegram", "rejected", audit_result.get("reason", ""), attempt=1)
        failed.append(("telegram", "Audit failed: " + audit_result.get("reason", "")))
    else:
        log("✅ AUDIT PASSED for telegram")
        db.log_audit(run_id, "telegram", "approved", "", attempt=1)

        for key, label in (("telegram", "telegram (monitoring)"), ("telegram_channel", "telegram (channel)")):
            if not allowed(key):
                continue
            tg = conn.get(key, {})
            if tg.get("connected") and tg.get("token") and tg.get("chat_id"):
                log("-> " + label + " (" + kind + ")...")
                try:
                    mid = telegram_send(tg["token"], tg["chat_id"], tg_text, media, kind)
                    with db.conn() as c:
                        c.execute("INSERT INTO posts(platform,posted,remote_id,status,run_id) VALUES(?,?,?,?,?)",
                                  (key, time.time(), str(mid), "ok", run_id))
                    log("   sent, message_id=" + str(mid))
                    sent += 1
                except Exception as e:
                    log("   " + label + " FAILED: " + str(e)[:200])
                    failed.append((label, str(e)[:150]))
            else:
                log("-> " + label + ": not connected, skipped")

    for p in ("youtube",):
        if not allowed(p):
            continue
        c = conn.get(p, {})
        if not c.get("connected"):
            continue
        if kind != "video":
            log("-> youtube: video only, " + kind + " skipped")
            continue

        yt_text = content_format.format_text("youtube", title=title, hook=hook, tags=tags, blog_body=blog_body)
        audit_result = editorial_auditor.audit_content(yt_text, "youtube", title, skip_job=job.name)
        if not audit_result.get("approved"):
            log("❌ AUDIT FAILED for youtube: " + audit_result.get("reason", "Unknown"))
            db.log_audit(run_id, "youtube", "rejected", audit_result.get("reason", ""), attempt=1)
            failed.append(("youtube", "Audit failed: " + audit_result.get("reason", "")))
        else:
            log("✅ AUDIT PASSED for youtube")
            db.log_audit(run_id, "youtube", "approved", "", attempt=1)
            log("-> youtube (video)...")
            try:
                url = youtube_send(_job_topic(job), str(job / "final.mp4"), description=yt_text, run_id=run_id)
                sent += 1
            except Exception as e:
                log("   youtube FAILED: " + str(e)[:200])
                failed.append(("youtube", str(e)[:150]))

    tt = conn.get("tiktok", {})
    if tt.get("connected") and allowed("tiktok"):
        if kind != "video":
            log("-> tiktok: video only, " + kind + " skipped")
        else:
            meta = _job_meta(str(job / "final.mp4"))
            if meta:
                tt_title, tt_hook, tt_tags, _ = meta
                tt_blog = _job_blog_body(str(job / "final.mp4"))
                tt_caption = content_format.format_text(
                    "tiktok", title=tt_title, hook=tt_hook, tags=tt_tags, blog_body=tt_blog)
            else:
                tt_caption = clean_title(_job_topic(job))

            audit_result = editorial_auditor.audit_content(tt_caption, "tiktok", title, skip_job=job.name)
            if not audit_result.get("approved"):
                log("❌ AUDIT FAILED for tiktok: " + audit_result.get("reason", "Unknown"))
                db.log_audit(run_id, "tiktok", "rejected", audit_result.get("reason", ""), attempt=1)
                failed.append(("tiktok", "Audit failed: " + audit_result.get("reason", "")))
            else:
                log("✅ AUDIT PASSED for tiktok")
                db.log_audit(run_id, "tiktok", "approved", "", attempt=1)
                log("-> tiktok (draft)...")
                try:
                    import tiktok
                    tiktok.upload_to_inbox(str(job / "final.mp4"), title=tt_caption)
                    with db.conn() as c2:
                        c2.execute("INSERT INTO posts(platform,posted,status,run_id) VALUES(?,?,?,?)",
                                  ("tiktok", time.time(), "draft", run_id))
                    sent += 1
                except Exception as e:
                    log("   tiktok FAILED: " + str(e)[:200])
                    failed.append(("tiktok", str(e)[:150]))

    # Bluesky/Mastodon can't take video - rather than skip them entirely on
    # video days, send the caption as text with the video's first scene frame
    # as a thumbnail image, so they still get every post, just not the video itself.
    still_image = media if kind == "image" else None
    if kind == "video":
        thumb = job / "img_0.jpg"
        if thumb.exists():
            still_image = str(thumb)

    bs = conn.get("bluesky", {})
    if bs.get("connected") and allowed("bluesky"):
        bs_text = content_format.format_text("bluesky", title=title, hook=hook, tags=tags)
        audit_result = editorial_auditor.audit_content(bs_text, "bluesky", title, skip_job=job.name)
        if not audit_result.get("approved"):
            log("❌ AUDIT FAILED for bluesky: " + audit_result.get("reason", "Unknown"))
            db.log_audit(run_id, "bluesky", "rejected", audit_result.get("reason", ""), attempt=1)
            failed.append(("bluesky", "Audit failed: " + audit_result.get("reason", "")))
        else:
            log("✅ AUDIT PASSED for bluesky")
            db.log_audit(run_id, "bluesky", "approved", "", attempt=1)
            log("-> bluesky (" + kind + (" -> text+thumbnail" if kind == "video" else "") + ")...")
            try:
                import bluesky
                uri = bluesky.send(bs_text, still_image, alt=_job_topic(job)[:200])
                with db.conn() as c3:
                    c3.execute("INSERT INTO posts(platform,posted,remote_id,status,run_id) VALUES(?,?,?,?,?)",
                              ("bluesky", time.time(), uri, "ok", run_id))
                log("   sent, " + str(uri))
                sent += 1
            except Exception as e:
                log("   bluesky FAILED: " + str(e)[:200])
                failed.append(("bluesky", str(e)[:150]))

    md = conn.get("mastodon", {})
    if md.get("connected") and allowed("mastodon"):
        md_text = content_format.format_text("mastodon", title=title, hook=hook, tags=tags)
        audit_result = editorial_auditor.audit_content(md_text, "mastodon", title, skip_job=job.name)
        if not audit_result.get("approved"):
            log("❌ AUDIT FAILED for mastodon: " + audit_result.get("reason", "Unknown"))
            db.log_audit(run_id, "mastodon", "rejected", audit_result.get("reason", ""), attempt=1)
            failed.append(("mastodon", "Audit failed: " + audit_result.get("reason", "")))
        else:
            log("✅ AUDIT PASSED for mastodon")
            db.log_audit(run_id, "mastodon", "approved", "", attempt=1)
            log("-> mastodon (" + kind + (" -> text+thumbnail" if kind == "video" else "") + ")...")
            try:
                import mastodon
                url = mastodon.send(md_text, still_image)
                with db.conn() as c4:
                    c4.execute("INSERT INTO posts(platform,posted,remote_id,status,run_id) VALUES(?,?,?,?,?)",
                              ("mastodon", time.time(), url, "ok", run_id))
                log("   sent, " + str(url))
                sent += 1
            except Exception as e:
                log("   mastodon FAILED: " + str(e)[:200])
                failed.append(("mastodon", str(e)[:150]))

    fb = conn.get("facebook", {})
    if fb.get("connected") and allowed("facebook"):
        log("-> facebook (" + kind + ")...")
        try:
            import meta
            fb_media = media if kind in ("image", "video") else None
            fb_text = content_format.format_text("facebook", title=title, hook=hook, tags=tags, blog_body=blog_body)
            url = meta.send(fb_text, fb_media, kind)
            with db.conn() as c6:
                c6.execute("INSERT INTO posts(platform,posted,remote_id,status,run_id) VALUES(?,?,?,?,?)",
                          ("facebook", time.time(), url, "ok", run_id))
            log("   sent, " + str(url))
            sent += 1
        except Exception as e:
            log("   facebook FAILED: " + str(e)[:200])
            failed.append(("facebook", str(e)[:150]))

    for _xp in ("threads", "linkedin", "wordpress", "webhook"):
        xc = conn.get(_xp, {})
        if xc.get("connected") and allowed(_xp):
            log("-> " + _xp + " (" + kind + ")...")
            try:
                import extra_publishers
                x_text = content_format.format_text(_xp, title=title, hook=hook, tags=tags, blog_body=blog_body)
                x_media = media if kind in ("image", "video") else None
                xurl = extra_publishers.send(_xp, xc, x_text, x_media, kind, title)
                with db.conn() as cx:
                    cx.execute("INSERT INTO posts(platform,posted,remote_id,status,run_id) VALUES(?,?,?,?,?)",
                               (_xp, time.time(), xurl, "ok", run_id))
                log("   sent, " + str(xurl))
                sent += 1
            except Exception as e:
                log("   " + _xp + " FAILED: " + str(e)[:200])
                failed.append((_xp, str(e)[:150]))

    dc = conn.get("discord", {})
    if dc.get("connected") and allowed("discord"):
        dc_text = content_format.format_text("discord", title=title, hook=hook, tags=tags, blog_body=blog_body)
        audit_result = editorial_auditor.audit_content(dc_text, "discord", title, skip_job=job.name)
        if not audit_result.get("approved"):
            log("❌ AUDIT FAILED for discord: " + audit_result.get("reason", "Unknown"))
            db.log_audit(run_id, "discord", "rejected", audit_result.get("reason", ""), attempt=1)
            failed.append(("discord", "Audit failed: " + audit_result.get("reason", "")))
        else:
            log("✅ AUDIT PASSED for discord")
            db.log_audit(run_id, "discord", "approved", "", attempt=1)
            log("-> discord (" + kind + ")...")
            try:
                import discord_bot
                url = discord_bot.send(dc_text, media, kind)
                with db.conn() as c5:
                    c5.execute("INSERT INTO posts(platform,posted,remote_id,status,run_id) VALUES(?,?,?,?,?)",
                              ("discord", time.time(), url, "ok", run_id))
                log("   sent, " + str(url))
                sent += 1
            except Exception as e:
                log("   discord FAILED: " + str(e)[:200])
                failed.append(("discord", str(e)[:150]))

    # blog.your-domain.example is pushed ONCE, further down via _notify_socialblog().
    # It used to be pushed TWICE per run - once inline here and once by that
    # function - which is why every post appeared two or three times on the
    # site. This inline copy also sent only the short `hook`, so the duplicate
    # the site displayed was the cut-off version. _notify_socialblog() is the
    # one that knows which platforms actually succeeded, so it is the one that
    # stays; it builds the full-length caption now (see socialai profile in
    # content_format.py).

    if failed:

        _alert_failure(conn, job.name, kind, failed, sent)
    # socialai is NOT in the send loop above, so it never increments `sent`.
    # Gating the push on `if sent:` therefore skipped it entirely when
    # socialai was the only connected platform. Gate on socialai being
    # connected instead, or on anything having actually been sent.
    socialai_on = bool(conn.get("socialai", {}).get("connected"))
    if sent or socialai_on:
        yt_url = None
        img_url = None
        if kind == "video":
            yt_url = _youtube_url_for_run(run_id)
            if yt_url and "youtu" in yt_url:
                # YouTube always serves a thumbnail at this fixed URL for any
                # public video id - use it so video posts aren't blank on the blog
                _yt_id = yt_url.split("/")[-1].split("?")[0]
                img_url = "https://img.youtube.com/vi/" + _yt_id + "/hqdefault.jpg"
        elif kind == "image":
            img_url = _pollinations_url(job)
        try:                                   # CHLOE's full blog article (video / image / text) beats the short script text
            _art = (job / "blog_article.txt").read_text(encoding="utf-8").strip()
            if len(_art) > len(blog_body or ""):
                blog_body = _art
        except Exception:
            pass
        _notify_socialblog(run_id, kind, job.name, title=title, hook=hook,
                            tags=tags, blog_body=blog_body,
                            youtube_url=yt_url, image_url=img_url,
                            platforms=[p for p in ("telegram","telegram_channel","youtube","tiktok","bluesky","mastodon","discord","facebook","socialai") if conn.get(p,{}).get("connected")])

    all_audit_logs = db.get_audit_logs(run_id)
    all_approved = all(log["status"] == "approved" for log in all_audit_logs) if all_audit_logs else False
    if all_audit_logs:
        send_audit_alert(conn, job.name, all_audit_logs, all_approved)

    return sent


SOCIALBLOG_URL = "https://localhost:8543"


def blog_password(conn_settings=None):
    """The Studio password for the CURRENT target. The local blog and the public site
    (blog.your-domain.example, on the Linux PC) keep separate passwords - using the local one
    against the public site returned 401, so nothing could ever be published there."""
    cfg = ((conn_settings if conn_settings is not None else load_conn()).get("socialai") or {})
    if cfg.get("target") == "public":
        return cfg.get("public_password") or ""
    return cfg.get("password") or ""


def apply_blog_target():
    """connections.json -> socialai.target: "local" (default, https://localhost:8543) or "public"
    (https://blog.your-domain.example). Public is a deliberate switch made by the owner, never implicit."""
    global SOCIALBLOG_URL
    cfg = (load_conn().get("socialai") or {})
    SOCIALBLOG_URL = ((cfg.get("public_url") or "https://blog.your-domain.example")
                      if cfg.get("target") == "public" else "https://localhost:8543")
    return SOCIALBLOG_URL


def _youtube_url_for_run(run_id):
    """Get THIS run's own YouTube URL, not just whatever was posted most recently
    (the old version returned the last-ever YouTube url regardless of which run
    it belonged to, silently mislinking or blanking video posts on the blog)."""
    try:
        with db.conn() as c:
            row = c.execute("SELECT url FROM posts WHERE platform='youtube' AND run_id=? AND url IS NOT NULL "
                             "ORDER BY posted DESC LIMIT 1", (run_id,)).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _pollinations_url(job):
    """Generate the Pollinations.ai URL for an image post's prompt. Reuses the
    exact seed + model that make_post.py actually used so this reproduces the
    real posted image instead of a fresh random one each time the blog loads it."""
    try:
        pj = job / "post.json"
        seed = None
        if pj.exists():
            d = json.loads(pj.read_text(encoding="utf-8"))
            prompt = d.get("image_prompt") or d.get("text", d.get("caption", ""))
            seed = d.get("image_seed")
        else:
            prompt = job.name
        import urllib.parse
        q = urllib.parse.quote(prompt[:300], safe="")
        url = "https://image.pollinations.ai/prompt/" + q + "?width=720&height=1280&nologo=true&model=flux"
        if seed is not None:
            url += "&seed=" + str(seed)
        return url
    except Exception:
        return None


def _find_socialai_post(session, title, since):
    """The server ignores our id/run_id and mints its own manual_* id, so a
    pushed post can only be found again by title + creation time."""
    r = session.get(SOCIALBLOG_URL + "/socialai/api/posts", timeout=15, verify=False)
    if r.status_code != 200:
        return None
    want = (title or "").strip().lower()
    for p in r.json().get("posts", []):
        if (p.get("title") or "").strip().lower() == want and float(p.get("created") or 0) >= since - 5:
            return p
    return None


def _verify_blog_text(session, post, run_id):
    """Check the stored post carries the full text we sent. Log only - this
    must NEVER raise: it used to be undefined, the NameError landed inside the
    push retry loop and the same post was pushed 3 times."""
    try:
        stored = _find_socialai_post(session, post.get("title"), post.get("created") or 0)
        if not stored:
            log("   socialai verify: pushed post not found on read-back")
            return
        sent, got = len(post.get("caption") or ""), len(stored.get("caption") or "")
        if sent and got < sent * 0.95:
            log("   socialai verify: TEXT CUT - sent %d chars, site holds %d" % (sent, got))
        else:
            log("   socialai verify: full text on site (%d chars)" % got)
    except Exception as _e:
        log("   socialai verify skipped (" + str(_e)[:60] + ")")


def _socialai_already_recorded(run_id):
    """Local guard: this run already has a successful socialai push."""
    try:
        with db.conn() as _c:
            row = _c.execute("SELECT 1 FROM posts WHERE run_id=? AND platform='socialai' "
                             "AND status='ok' LIMIT 1", (run_id,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _notify_socialblog(run_id, kind, topic, title="", hook="", tags=None, blog_body=None,
                       youtube_url=None, image_url=None, platforms=None):
    """Push this post to blog.your-domain.example via its admin API - best-effort,
    never let a failure here touch the actual publish result.
    Now passes YouTube URL, image URL, and platform info."""
    import sqlite3 as _sqlite3
    try:
        import requests as _requests
    except ImportError:
        _requests = None
    if not _requests:
        log("  socialai push skipped (requests not installed)")
        return
    try:
        import json as _json
        conn_settings = _json.loads(CONN.read_text(encoding="utf-8")) if CONN.exists() else {}
    except Exception:
        conn_settings = {}
    apply_blog_target()
    tags = tags or []
    hashtags = tags if isinstance(tags, list) else \
               [t for t in str(tags).split() if t.startswith("#")]
    # blog.your-domain.example's /socialai/api/add 502s (server-side crash) on ANY
    # non-empty "hashtags" list, confirmed by bisecting a live payload
    # 2026-09-13 - this alone was silently dropping almost every real post,
    # since real posts almost always carry hashtags. No SSH access to this
    # Pi from this machine to fix the server itself, so work around it here:
    # always send an empty hashtags array, fold the real tags into the
    # caption text instead so they're still visible on the post.
    hashtag_text = " ".join(hashtags)
    # The site used to receive only `hook` (a 1-2 sentence teaser) in `caption`
    # while `excerpt` got the full blog_body - so the page rendered the teaser
    # and looked like every post was cut off mid-sentence. It is a website, not
    # a 500-char social network: give caption and excerpt the SAME complete,
    # long-form text via the shared formatter.
    full_text = content_format.format_text(
        "socialai", title=clean_title(title) or topic, hook=hook,
        tags=hashtags, blog_body=blog_body)
    full_text = _sanitize(full_text)
    caption = full_text
    excerpt = full_text
    if hashtag_text and hashtag_text not in full_text:
        caption = (full_text + "\n\n" + hashtag_text).strip()
        excerpt = caption
    hashtags = []

    # BLOG COMPLETENESS GATE. blog.your-domain.example is a blog, so it must carry the
    # WHOLE post - never a shortened teaser and never an article cut in half by
    # the formatter. If the complete text cannot fit, hold the blog push back
    # with a plain reason instead of publishing something cut down. Other
    # platforms are unaffected.
    try:
        import publish_guard as _pg
        _src = _sanitize(blog_body or hook or "")
        _chk = _pg.check_socialai_full(caption, _src or caption, "socialai")
        if not _chk.ok:
            log("  socialai HELD BACK: " + _chk.reason)
            try:
                with db.conn() as _c:
                    _c.execute("INSERT INTO posts(run_id,platform,status,posted) "
                               "VALUES(?,?,?,?)",
                               (run_id, "socialai", "held: " + _chk.reason[:90], time.time()))
            except Exception:
                pass
            return
    except Exception as _e:
        log("  socialai completeness check errored (" + str(_e)[:60] + ") - pushing anyway")
    if _socialai_already_recorded(run_id):
        log("  socialai: run %s already pushed (local record), skipping duplicate push" % run_id)
        return

    # IDEMPOTENCY CHECK: Don't push if this run already exists on socialai.
    # This prevents duplicate posts when publish_job() is called multiple times
    # for the same run (e.g., scheduler retry + manual trigger).
    try:
        sa_pass = blog_password(conn_settings)
        s_check = _requests.Session()
        r_login = s_check.post(SOCIALBLOG_URL + "/socialai/login",
                              json={"password": sa_pass}, timeout=10, verify=False)
        if r_login.status_code == 200:
            r_posts = s_check.get(SOCIALBLOG_URL + "/socialai/api/posts", timeout=10, verify=False)
            if r_posts.status_code == 200:
                existing_ids = {p["id"] for p in r_posts.json().get("posts", [])}
                post_id = f"run_{run_id}"
                if post_id in existing_ids:
                    log(f"  socialai: {post_id} already exists, skipping duplicate push")
                    # Still record in local DB for tracking
                    try:
                        with db.conn() as _c:
                            _c.execute("INSERT OR IGNORE INTO posts(run_id,platform,status,posted,url,remote_id) "
                                       "VALUES(?,?,?,?,?,?)",
                                       (run_id, "socialai", "ok", time.time(),
                                        SOCIALBLOG_URL + "/socialai/post/" + str(run_id),
                                        post_id))
                    except Exception:
                        pass
                    return
    except Exception as _e:
        log(f"  socialai idempotency check failed ({str(_e)[:60]}) - proceeding with push")

    # Extract YouTube ID from URL if provided
    youtube_id = None
    if youtube_url and "youtu" in youtube_url:
        try:
            youtube_id = youtube_url.split("/")[-1].split("?")[0]
        except Exception:
            pass
    # Track connected platforms
    platforms_dict = {}
    if platforms:
        for p in platforms:
            platforms_dict[p] = "ok"
    post = {
        "id": f"run_{run_id}",
        "run_id": run_id,
        "kind": kind,
        "title": _sanitize(clean_title(title) or topic.title()),
        "topic": _sanitize(topic),
        "category": "self_healing_spiritual",
        "created": time.time(),
        "hashtags": hashtags,
        "caption": caption,
        "excerpt": excerpt,
        "image": image_url,
        "youtube_id": youtube_id,
        "youtube_url": youtube_url,
        "platforms": platforms_dict
    }
    # Pi/tunnel hiccups (e.g. a transient 502) used to silently drop the post
    # forever with no retry and no visible alert - try up to 3 times before
    # giving up, and log loudly on final failure so this isn't invisible.
    last_err = None
    for attempt in range(3):
        try:
            s = _requests.Session()
            sa_pass = blog_password(conn_settings)
            r = s.post(SOCIALBLOG_URL + "/socialai/login",
                       json={"password": sa_pass}, timeout=10, verify=False)
            if r.status_code != 200:
                last_err = "login failed: " + str(r.status_code)
                time.sleep(3)
                continue
            if attempt > 0:
                # A failed attempt may still have stored the post (timeout after
                # the server wrote it) - never push again if it is already there.
                try:
                    if _find_socialai_post(s, post["title"], post["created"]):
                        log("   socialai: post already on site from earlier attempt, not re-pushing")
                        last_err = None
                        r2 = None
                    else:
                        r2 = s.post(SOCIALBLOG_URL + "/socialai/api/add", json=post, timeout=10, verify=False)
                except Exception:
                    r2 = s.post(SOCIALBLOG_URL + "/socialai/api/add", json=post, timeout=10, verify=False)
            else:
                r2 = s.post(SOCIALBLOG_URL + "/socialai/api/add", json=post, timeout=10, verify=False)
            if r2 is None or r2.status_code == 200:
                # Record FIRST, then verify - and verification can never raise
                # into the retry loop (that re-pushed the post 3x).
                log("   blog.your-domain.example pushed (img=" + str(bool(image_url)) + " yt=" + str(bool(youtube_url)) + ")")
                try:
                    with db.conn() as _c:
                        _c.execute("INSERT INTO posts(run_id,platform,status,posted,url,remote_id) "
                                   "VALUES(?,?,?,?,?,?)",
                                   (run_id, "socialai", "ok", time.time(),
                                    SOCIALBLOG_URL + "/socialai/post/" + str(run_id),
                                    "run_" + str(run_id)))
                except Exception as _e:
                    log("   socialai recorded? no (" + str(_e)[:60] + ")")
                try:
                    _verify_blog_text(s, post, run_id)
                except Exception:
                    pass
                return
            last_err = str(r2.status_code) + " " + r2.text[:200]
        except Exception as e:
            last_err = str(e)[:150]
        time.sleep(3)
    log("  socialai push FAILED after 3 tries (post lost, needs manual re-add via Studio): " + str(last_err))


def _alert_failure(conn, job_name, kind, failed, sent):
    """A connected platform failing mid-publish must never fail silently - alert
    the monitoring chat even though the overall run still 'succeeded' (other
    platforms went out fine). Best-effort: an alert that itself fails must not
    blow up the publish."""
    try:                                   # HELENA's report already says which places are live and which are not:
        import json as _j                  # while the department runs she is the ONLY voice (no second, technical message)
        if (_j.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig")).get("department") or {}).get("enabled"):
            return
    except Exception:
        pass
    import report_format as _rf            # legacy path only (department switched off): same clear format
    text = _rf.post_report(job_name, kind, "partial", [], [(str(l), str(e)) for l, e in failed])
    for key in ("telegram", "telegram_channel"):
        tg = conn.get(key, {})
        if tg.get("connected") and tg.get("token") and tg.get("chat_id"):
            try:
                telegram_send(tg["token"], tg["chat_id"], text)
                return   # one alert is enough - don't spam both chats
            except Exception:
                continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job")
    ap.add_argument("--text")
    ap.add_argument("--setup-telegram", metavar="TOKEN")
    ap.add_argument("--latest", action="store_true", help="publish the newest job folder")
    ap.add_argument("--only-platforms", default="",
                    help="comma-separated: restrict publish to ONLY these platforms")
    a = ap.parse_args()
    only = [p.strip() for p in a.only_platforms.split(",") if p.strip()] or None

    if a.setup_telegram:
        chats = telegram_setup(a.setup_telegram)
        if not chats:
            print("No chats found.")
            print("Open Telegram, find your bot, press START and send any message. Then run this again.")
            return
        cid, ctype, title = chats[-1]
        conn = load_conn()
        conn["telegram"] = {"connected": True, "token": a.setup_telegram,
                            "chat_id": cid, "account": (title or str(cid)) + " (" + ctype + ")"}
        save_conn(conn)
        print("CONNECTED -> chat_id=" + str(cid) + "  " + ctype + "  " + title)
        for c in chats:
            print("   also saw:", c)
        return

    if a.text:
        tg = load_conn().get("telegram", {})
        if not tg.get("connected"):
            print("Telegram not connected. Run --setup-telegram <token> first.")
            return
        mid = telegram_send(tg["token"], tg["chat_id"], a.text)
        print("sent, message_id=" + str(mid))
        return

    job = a.job
    if a.latest or not job:
        outs = sorted([p for p in (ROOT / "out").iterdir() if p.is_dir() and p.name != "test"],
                      key=lambda p: p.stat().st_mtime)
        if not outs:
            print("no jobs in out/")
            return
        job = str(outs[-1])
    log("publishing: " + job)
    n = publish_job(job, only=only)
    log("done, sent to " + str(n) + " platform(s)")


if __name__ == "__main__":
    main()
