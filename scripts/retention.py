# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""RETENTION - agent 20. Keeps storage bounded without ever losing a published post.

the owner's rule: once a job is approved and published, the working drafts and the script are
deleted. Only the FINAL output file is kept, for 10 days. After 10 days it goes too.

WHAT COUNTS AS A DRAFT (deleted as soon as the job finishes)
  videogen/WORK/<area>/<job_id>/**   every artifact the 18 agents produced:
      creative_brief.json, research_report.json, script.json, storyboard.json,
      visual_spec.json, emotion_guide.json, the audit files, the evidence folder,
      any raw clips. The screenplay lives here - that is the draft being removed.

WHAT COUNTS AS FINAL OUTPUT (kept 10 days)
  out/<run folder>/                  final.mp4 + post.json + meta.json (the real deliverable)

SAFETY (all checked before anything is deleted)
  * disabled unless rules.json -> retention.enabled is true (the owner flips this)
  * a job is only cleaned once its HELENA step is done or skipped  -> still-running jobs
    are never touched, whatever their age
  * a run is only cleaned if published = 1                 -> never delete unpublished work
  * a final output younger than retention_days is never deleted
  * never touches config/, scripts/, videogen/SCREENPLAYS, venv/, data/brain.db or models
  * every path is appended to data/retention.log BEFORE the delete, so the action is
    reconstructable and a mistake is visible

Usage:  python scripts/retention.py --once     |   --watch 3600
"""
import json, os, shutil, sys, time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

import db                                   # noqa: E402
import department_queue as Q                # noqa: E402

LOG = ROOT / "data" / "retention.log"
WORK = ROOT / "videogen" / "WORK"
OUT = ROOT / "out"
RULES = ROOT / "config" / "rules.json"

NEVER_TOUCH = ("config", "scripts", "venv", "engine", "assets", "tools", "deploy",
               "models", "backups", "logs")


def log(m):
    line = "[RET %s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), m)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def cfg():
    try:
        r = json.loads(RULES.read_text(encoding="utf-8-sig")).get("retention") or {}
    except Exception:
        r = {}
    return {
        "enabled": bool(r.get("enabled", False)),
        "days": int(r.get("final_days", 10)),
        "delete_drafts": bool(r.get("delete_drafts_after_publish", True)),
        "sweep_hours": int(r.get("sweep_hours", 6)),
    }


def _guard(p: Path):
    """Refuse anything that is not inside WORK/ or out/. Belt and braces."""
    try:
        rp = p.resolve()
    except Exception:
        return False
    for bad in NEVER_TOUCH:
        if bad.lower() in [x.lower() for x in rp.parts]:
            return False
    return (WORK.resolve() in rp.parents) or (OUT.resolve() in rp.parents)


def _size(p: Path):
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _rm(p: Path, why: str):
    """Delete with a recorded reason. Returns MB freed, 0 if refused."""
    if not p.exists() or not _guard(p):
        if p.exists():
            log("REFUSED (outside WORK/ or out/): %s" % p)
        return 0.0
    mb = _size(p) / (1024 * 1024)
    # record BEFORE deleting
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write("    DELETE %s  (%.2f MB)  reason: %s\n" % (p, mb, why))
    except Exception:
        pass
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            p.unlink()
        except Exception:
            return 0.0
    log("deleted %s (%.2f MB) - %s" % (p.name, mb, why))
    return mb


def finished_jobs():
    """job_ids whose chain is complete: HELENA done or skipped."""
    c = Q.schema()
    out = []
    for r in c.execute("SELECT job_id, MAX(stage) s FROM department_steps GROUP BY job_id"):
        h = c.execute("SELECT status FROM department_steps WHERE job_id=? AND step_key='helena'",
                      (r["job_id"],)).fetchone()
        if h and h["status"] in ("done", "skipped"):
            out.append(r["job_id"])
    return out


def _job_published(jid):
    """True only when this job's post is CONFIRMED published (runs.published = 1 or a posts row with status ok).
    Drafts of a job that failed to publish are the only material a retry can use - they are never deleted."""
    try:
        m = Q.read_job(jid) or {}
        rid = m.get("run_id")
        if not rid:
            return False
        c = db.conn()
        r = c.execute("SELECT published FROM runs WHERE id=?", (rid,)).fetchone()
        if r and r["published"] == 1:
            return True
        return bool(c.execute("SELECT 1 FROM posts WHERE run_id=? AND status='ok' LIMIT 1", (rid,)).fetchone())
    except Exception:
        return False


def _job_closed(jid):
    """A job the owner / the 3.0 clean-up closed on purpose (cancelled or ARCHIVED) - its drafts are junk after the window."""
    try:
        c = Q.schema()
        return bool(c.execute("SELECT 1 FROM department_steps WHERE job_id=? AND (reason LIKE 'ARCHIVED%' OR reason LIKE 'cancelled%') LIMIT 1", (jid,)).fetchone())
    except Exception:
        return False


def purge_logs(days=14):
    """Old log / diagnostic text files in logs/ (never the audit trail data/retention.log, never the database)."""
    freed, n, now = 0.0, 0, time.time()
    L = ROOT / "logs"
    if not L.exists():
        return 0.0
    for f in L.iterdir():
        try:
            if f.is_file() and f.parent.resolve() == L.resolve() and f.suffix.lower() in (".log", ".txt", ".out", ".err")                     and now - f.stat().st_mtime > days * 86400:
                mb = f.stat().st_size / (1024 * 1024)
                with LOG.open("a", encoding="utf-8") as lf:                       # recorded BEFORE the delete
                    lf.write("    DELETE %s  (%.2f MB)  reason: log older than %d days\n" % (f, mb, days))
                f.unlink()
                freed += mb
                n += 1
        except Exception:
            pass
    if n:
        log("logs: removed %d old file(s)" % n)
    return freed


def purge_drafts(days):
    """Delete WORK/<area>/<job_id> for jobs that finished more than `days` ago.
    The age guard stops us racing a job that is still being written."""
    freed = 0.0
    n = 0
    now = time.time()
    try:
        import stock
        held = {i["id"] for i in stock.items() if i["status"] in ("ready", "in_production")}
    except Exception:
        held = set()
    for jid in finished_jobs():
        if jid in held:
            continue                    # a post waiting in STOCK is never cleaned up
        if not (_job_published(jid) or _job_closed(jid)):
            continue                    # not confirmed published (or failed / awaiting retry): keep everything
        newest = 0.0
        for area in WORK.iterdir() if WORK.exists() else []:
            jd = area / jid
            if not jd.is_dir():
                continue
            newest = max(newest, max((f.stat().st_mtime for f in jd.rglob("*") if f.is_file()),
                                     default=jd.stat().st_mtime))
        if not newest or (now - newest) < days * 86400:
            continue
        for area in WORK.iterdir():
            jd = area / jid
            if jd.is_dir():
                freed += _rm(jd, "drafts of finished job %s" % jid)
                n += 1
    if n:
        log("drafts: removed %d folder(s), freed %.2f MB" % (n, freed))
    return freed


def purge_expired(days):
    """Delete the FINAL output of a PUBLISHED run older than the window."""
    freed = 0.0
    n = 0
    now = time.time()
    if not OUT.exists():
        return 0.0
    try:
        rows = db.conn().execute(
            "SELECT id, path, published, finished, started FROM runs "
            "WHERE path IS NOT NULL AND path != ''").fetchall()
    except Exception as e:
        log("cannot read runs: %s" % str(e)[:90]); return 0.0
    for r in rows:
        if r["published"] != 1:
            continue                      # never delete unpublished work
        p = Path(r["path"])
        target = p.parent if p.is_file() else p
        if not target.exists() or target.parent.resolve() != OUT.resolve():
            continue
        age = now - (r["finished"] or r["started"] or now)
        if age < days * 86400:
            continue
        freed += _rm(target, "final output of run %s, published, older than %d days"
                     % (r["id"], days))
        n += 1
    if n:
        log("final output: removed %d item(s) older than %d day(s), freed %.2f MB"
            % (n, days, freed))
    return freed


def run_once():
    c = cfg()
    if not c["enabled"]:
        return {"enabled": False, "note": "retention.enabled is false in rules.json - "
                                          "nothing deleted (the owner flips this when ready)"}
    freed = 0.0
    if c["delete_drafts"]:
        # drafts go once the job finished AND its own window has passed
        freed += purge_drafts(max(1, c["days"]))
    freed += purge_expired(c["days"])
    freed += purge_logs(14)
    res = {"enabled": True, "days": c["days"], "freed_mb": round(freed, 2)}
    try:                    # proof of life for the dashboard: the log above only grows when something is deleted
        (ROOT / "data" / "retention_state.json").write_text(
            json.dumps(dict(res, ts=time.time())), encoding="utf-8")
    except Exception:
        pass
    return res


def watch(interval=3600):
    log("retention online (every %.0f min). enabled=%s" % (interval / 60, cfg()["enabled"]))
    while True:
        try:
            run_once()
        except Exception as e:
            log("sweep failed (non-fatal): %s" % str(e)[:140])
        time.sleep(interval)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        i = sys.argv[sys.argv.index("--watch") + 1]
        watch(int(i) if i.isdigit() else 3600)
    else:
        print(json.dumps(run_once(), indent=2, default=str))