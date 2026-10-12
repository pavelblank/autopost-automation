# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - autopilot.py
Full agentic worker: MAKE content, AUDIT it (quality gate), then AUTO-PUBLISH.
Used by scheduler.py when approval_mode="auto" so a scheduled run goes out by itself.

  python autopilot.py --topic "..." --kind video [--scenes 4] [--max-retries 3]

Exit 0 = made, audited, and published. Exit 1 = something failed.
"""
import argparse, sys, time, subprocess, json
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
import db
import editorial_auditor
import content_format
from editorial_auditor import _job_blog_body

PY = str(ROOT / "venv" / "Scripts" / "python.exe")
MAX_AUDIT_RETRIES = 3  # max times to regenerate if audit fails


def log(m):
    try:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m, flush=True)
    except UnicodeEncodeError:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m.encode("ascii", "replace").decode("ascii"), flush=True)


def run(cmd, timeout=3600):
    """Run a generator subprocess. On timeout, kill the WHOLE process tree
    (children + grandchildren like ffmpeg) so a hang doesn't leave zombie
    workers holding SQLite locks or CPU. subprocess.run's bare timeout only
    kills the direct child, which is exactly how the DB-lock zombies leaked."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, creationflags=0)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, text=True, timeout=15)
        except Exception:
            p.kill()
        out = (p.stdout.read() if p.stdout else "") or ""
        raise RuntimeError("Command %s timed out after %ss" % (cmd[0], timeout))
    if p.returncode != 0:
        raise RuntimeError((out or "")[-500:])
    return out


def run_publish(cmd, timeout=1800):
    """publish.py wrapper with the same tree-kill timeout behaviour. Returns
    a real subprocess.CompletedProcess (has .returncode/.stdout/.stderr) so
    callers can slice output without TypeError, like the old subprocess.run."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, creationflags=0)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, text=True, timeout=15)
        except Exception:
            p.kill()
        out = (p.stdout.read() if p.stdout else "") or ""
        err = "publish.py timed out after %ss\n" % timeout
        return subprocess.CompletedProcess(cmd, 124, out, err)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def _load_conn():
    conn_file = ROOT / "config" / "connections.json"
    if conn_file.exists():
        return json.loads(conn_file.read_text(encoding="utf-8"))
    return {}


def _get_platforms_for_kind(kind, only_platforms=None):
    """Get list of connected platforms for this content kind."""
    conn = _load_conn()
    only = set(only_platforms.split(",")) if only_platforms else None
    
    platforms = []
    if kind == "video":
        for p in ("youtube", "tiktok", "telegram", "telegram_channel", "bluesky", "mastodon", "discord", "facebook", "socialai"):
            if conn.get(p, {}).get("connected") and (only is None or p in only):
                platforms.append(p)
    elif kind == "image":
        for p in ("instagram", "facebook", "threads", "telegram", "telegram_channel", "bluesky", "mastodon", "discord", "socialai"):
            if conn.get(p, {}).get("connected") and (only is None or p in only):
                platforms.append(p)
    else:  # text
        for p in ("instagram", "facebook", "threads", "telegram", "telegram_channel", "bluesky", "mastodon", "x", "discord", "socialai"):
            if conn.get(p, {}).get("connected") and (only is None or p in only):
                platforms.append(p)
    return platforms


def _extract_job_content(job_dir, kind):
    """Extract the content (text, title, hook, tags) from a job folder for auditing."""
    job = Path(job_dir)
    text, title, hook, tags = "", "", "", []
    
    pj = job / "post.json"
    if pj.exists():
        d = json.loads(pj.read_text(encoding="utf-8"))
        text = d.get("text", "")
        title = d.get("caption", "")[:95]
        hook = d.get("caption", "") or text
        raw_tags = d.get("hashtags")
        tags = raw_tags if isinstance(raw_tags, list) else [t for t in (raw_tags or "").split() if t.startswith("#")]
    else:
        cap = job / "caption.txt"
        if cap.exists():
            text = cap.read_text(encoding="utf-8")
        else:
            text = job.name
        title = text[:95]
        hook = text
        tags = []
    
    if kind == "video" and (job / "meta.json").exists():
        try:
            m = json.loads((job / "meta.json").read_text(encoding="utf-8"))
            title = m.get("title") or title
            hook = m.get("hook") or hook
            tags = list(m.get("hashtags") or tags)
        except Exception:
            pass
    
    return text, title, hook, tags


def _format_for_platform(platform, text, title, hook, tags, job_dir, kind):
    """Format content for a specific platform using content_format."""
    blog_body = None
    if kind == "video":
        blog_body = _job_blog_body(Path(job_dir) / "final.mp4")
    
    if platform in ("telegram", "telegram_channel"):
        profile = "telegram" if kind == "video" else "telegram_text"
        return content_format.format_text(profile, title=title, hook=hook, tags=tags, blog_body=blog_body)
    return content_format.format_text(platform, title=title, hook=hook, tags=tags, blog_body=blog_body)


def audit_job_before_publish(job_dir, kind, only_platforms=None):
    """
    PRE-PUBLISH AUDIT GATE: Run editorial audit on content BEFORE approval.
    Returns (approved: bool, failed_platforms: list, audit_details: list)
    If ANY platform fails, the entire job is rejected for regeneration.
    """
    platforms = _get_platforms_for_kind(kind, only_platforms)
    if not platforms:
        log("  No connected platforms for " + kind + " - skipping audit")
        return True, [], []

    text, title, hook, tags = _extract_job_content(job_dir, kind)

    # The blog is always audited too, even though it is not one of the
    # `platforms` - it is the owner's own site and it must carry the COMPLETE post.
    audit_targets = list(platforms)
    try:
        import db as _db
        _conn = json.loads((ROOT / "config" / "connections.json").read_text(encoding="utf-8"))
        if _conn.get("socialai", {}).get("connected") and "socialai" not in audit_targets:
            audit_targets.append("socialai")
    except Exception:
        pass

    log("  [AUDIT] Running the full step-by-step gate on " + str(len(audit_targets))
        + " destination(s) for: " + title[:50])

    audit_results = []
    failed = []

    for platform in audit_targets:
        formatted = _format_for_platform(platform, text, title, hook, tags, job_dir, kind)
        # skip_job: this job's own folder is already in out/, so without it the
        # duplicate gate matches the post against ITSELF at 100% and every
        # video gets rejected. Same fix as publish.py.
        try:
            _self = Path(str(job_dir)).name
        except Exception:
            _self = None

        result = editorial_auditor.audit_content(formatted, platform, title,
                                                hashtags=tags, skip_job=_self)
        # publish_guard adds the steps audit_content does not cover: the post
        # must be FINISHED (not cut off), the blog must carry the complete text,
        # and the platform must not be over its posting/rate limit. It reuses
        # the same underlying checks, so nothing is judged twice differently.
        try:
            import publish_guard as _pg
            _rep = _pg.run_all(formatted, platform, title=title, tags=tags,
                               blog_body=text, topic=title, kind=kind,
                               skip_job=_self, use_ai=False)
            if not result.get("approved", True) and _rep.ok:
                # audit_content caught something publish_guard did not - keep it
                pass
            elif _rep.ok:
                result = {"approved": True, "reason": "", "suggestion": ""}
            else:
                _first = _rep.first_problem()
                result = {"approved": False,
                          "reason": _rep.summary(),
                          "suggestion": "Held back so nothing broken goes out.",
                          "severity": getattr(_first, "severity", "content")}
        except Exception as _e:
            log("  guard error on " + platform + ": " + str(_e)[:60])

        # A platform that is merely RATE LIMITED or at its daily cap must not
        # throw the whole post away - skip that one destination and still post
        # everywhere else. Only a CONTENT problem (cut off, duplicate, broken
        # text) rejects the job so a new topic gets generated.
        if not result.get("approved", True) and result.get("severity") == "platform":
            log("  SKIP " + platform + " for now: " + result.get("reason", "")[:110])
            audit_results.append({"platform": platform, "approved": True,
                                  "reason": "", "suggestion": "",
                                  "skipped": result.get("reason", "")})
            continue

        audit_results.append({
            "platform": platform,
            "approved": result.get("approved", True),
            "reason": result.get("reason", ""),
            "suggestion": result.get("suggestion", "")
        })

        if result.get("approved"):
            log("  OK " + platform + ": passed every check")
        else:
            log("  BLOCKED " + platform + ": " + result.get("reason", "Unknown")[:150])
            failed.append(platform)

    all_approved = len(failed) == 0
    return all_approved, failed, audit_results


def main():
    import atexit
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--kind", choices=["video", "image", "text"], default="video")
    ap.add_argument("--scenes", type=int, default=4)
    ap.add_argument("--only-platforms", default="",
                    help="comma list; if given, publish to ONLY those platforms")
    ap.add_argument("--max-retries", type=int, default=MAX_AUDIT_RETRIES,
                    help="max regeneration attempts before giving up")
    ap.add_argument("--lease", action="store_true", help="take over the one-generation-at-a-time lease (set by dashboard/scheduler)")
    a = ap.parse_args()

    # ONE-GENERATION-AT-A-TIME: take over the gate for our whole run (the
    # spawner already acquired it); release on exit (auto-stolen if hard-killed).
    if a.lease:
        db.takeover_gen_lease()
        atexit.register(db.release_gen_lease)

    log("autopilot: " + a.kind + " <- '" + a.topic[:70] + "' (max audit retries: " + str(a.max_retries) + ")")

    topic = a.topic
    tried_topics = [a.topic]
    TOPIC_SWITCH_AFTER = 2   # after this many rejects on one topic, change subject

    for attempt in range(a.max_retries + 1):
        if attempt > 0:
            # Regenerating about the SAME subject just reproduces the same
            # rejected text. Rotate to a different unused topic instead
            # (the owner's request) - that is what actually recovers the run.
            if (attempt % TOPIC_SWITCH_AFTER) == 0 and attempt < a.max_retries:
                nxt = None
                try:
                    import make_post
                    nxt = make_post._fresh_topic(tried_topics)
                except Exception as e:
                    log("  could not pick a new topic: " + str(e)[:70])
                if nxt:
                    log("  switching to a NEW topic: '" + nxt[:60] + "'")
                    tried_topics.append(nxt)
                    topic = nxt
            log("  [RETRY " + str(attempt) + "/" + str(a.max_retries) + "] Regenerating: '" + topic[:60] + "'")
        
        # Step 1: Generate content
        if a.kind == "video":
            run([PY, str(ROOT / "scripts" / "make_video.py"), topic, "--scenes", str(a.scenes)])
        else:
            run([PY, str(ROOT / "scripts" / "make_post.py"), a.kind, topic])

        # Step 2: Find the freshest finished run
        with db.conn() as c:
            r = c.execute("SELECT * FROM runs WHERE status='ok' AND approval='pending' "
                          "AND published=0 ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            raise RuntimeError("no finished run found to publish")
        
        job = Path(r["path"])
        if job.is_file():
            job = job.parent
        
        # Step 3: PRE-PUBLISH AUDIT GATE (HARD GATE)
        log("  [AUDIT GATE] Running pre-publish quality audit...")
        approved, failed, audit_details = audit_job_before_publish(job, a.kind, a.only_platforms)
        
        if approved:
            log("  ✅ ALL AUDITS PASSED - Content approved for publishing")
            
            # Step 4: Approve and publish
            db.set_approval(r["id"], "auto")
            log("publishing: " + str(job))
            pub_args = [PY, str(ROOT / "scripts" / "publish.py"), "--job", str(job)]
            if a.only_platforms:
                pub_args += ["--only-platforms", a.only_platforms]
            out = run_publish(pub_args)
            ok = out.returncode == 0
            log(out.stdout[-800:])
            if not ok:
                raise RuntimeError((out.stderr or out.stdout)[-400:])
            db.mark_published(r["id"])
            log("DONE: " + r["topic"][:60])
            return 0
        else:
            # Audit failed - mark run as rejected so it doesn't clutter dashboard
            log("  ❌ AUDIT GATE FAILED - " + str(len(failed)) + " platform(s) rejected: " + ", ".join(failed))
            db.set_approval(r["id"], "rejected")
            
            # Log audit failures to database for debugging
            for detail in audit_details:
                if not detail["approved"]:
                    db.log_audit(r["id"], detail["platform"], "rejected", detail["reason"], attempt=attempt+1)
            
            if attempt < a.max_retries:
                log("  Will regenerate and retry...")
                continue
            else:
                log("  MAX RETRIES EXCEEDED - giving up after trying "
                    + str(len(tried_topics)) + " topic(s)")
                return 1
    
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("AUTOPILOT ERROR: " + str(e)[:300])
        sys.exit(1)