#!/usr/bin/env python3
# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost Resource Optimizer - runs periodically to keep system lean
- Delete old CONTENT folders only when AI-verified already posted (published=1
  in brain.db) and older than RETENTION_DAYS - NEVER delete unpublished or
  live content.
- Cleanup temp files older than 24h (non-content)
- Clear old logs
- Vacuum SQLite database
"""
import os
import shutil
import sqlite3
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
DB   = ROOT / "data" / "brain.db"
LOGS = ROOT / "logs"
OUT  = ROOT / "out"
DOWNLOAD = ROOT / "Download"
MAX_CONCURRENT_JOBS = 2     # max simultaneous autopilot/scheduler jobs
LOG_RETENTION_DAYS = 7      # keep logs for 7 days
RETENTION_DAYS = 10         # content folders kept 10 days AFTER verified publish
                           # (the owner 2026-10-10: was 3, he wants 10. This is the blunt
                           # safety net; the RETENTION agent owns draft/final cleanup
                           # precisely - see department.py agent 20.)
FAILED_KEEP_DAYS = 1        # never-deployable job folders kept only 1 day


def log(m):
    print(f"[{time.strftime('%I:%M:%S %p')}] {m}", flush=True)


def _job_state():
    """Map each content folder name -> ('published'|'failed'|'other', age_sec).
    Verified via brain.db runs.path: a run published=1 means the content really
    went out, so its files are no longer needed on disk."""
    state = {}
    if not DB.exists():
        return state
    try:
        con = sqlite3.connect(str(DB), timeout=30)
        rows = con.execute(
            "SELECT path, published, status, started, finished FROM runs "
            "WHERE path IS NOT NULL AND path != ''"
        ).fetchall()
        con.close()
    except Exception as e:
        log("db scan failed: " + str(e)[:120])
        return state
    now = time.time()
    for path, published, status, started, finished in rows:
        p = Path(path)
        name = p.parent.name if p.is_file() else p.name   # folder name holding it
        age = now - (finished or started or now)
        if published == 1:
            state[name] = ("published", age)
        elif status == "failed":
            state[name] = ("failed", age)
        else:
            state[name] = ("other", age)
    return state


def cleanup_out_folders():
    """Delete whole content job folders once AI-verified posted (>3d old) or
    failed/stale (>1d). NEVER touches folders with no DB record (could be an
    in-progress run) and returns their names as protected."""
    if not OUT.exists():
        return
    st = _job_state()
    now = time.time()
    removed = 0
    freed_mb = 0.0
    for d in OUT.iterdir():
        if not d.is_dir():
            continue
        kind, age = st.get(d.name, ("unknown", 0))
        if kind == "unknown":
            # no DB record yet: could be a live run or stray - protect it
            continue
        cutoff = FAILED_KEEP_DAYS * 86400 if kind == "failed" else RETENTION_DAYS * 86400
        if age >= cutoff:
            try:
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d)
                removed += 1
                freed_mb += size / (1024 * 1024)
                log(f"deleted {kind} folder {d.name} (age {int(age//3600)}h)")
            except Exception as e:
                log("skip " + d.name + ": " + str(e)[:120])
    if removed:
        log(f"Removed {removed} content folders, freed {freed_mb:.1f} MB")


def cleanup_temp_files():
    """Remove stray temp files (not whole content folders) in Download and
    run leftovers inside out/ older than 24h. Content folders inside out/ are
    handled by cleanup_out_folders() so they are never touched here."""
    cutoff = time.time() - (24 * 3600)
    removed = 0
    freed_mb = 0
    for base in (DOWNLOAD, OUT):
        if not base.exists():
            continue
        for entry in base.iterdir():
            try:
                # only loose FILES here; directories are content -> skip
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    freed_mb += entry.stat().st_size / (1024 * 1024)
                    entry.unlink()
                    removed += 1
            except Exception:
                pass
    if removed:
        log(f"Cleaned {removed} stray temp files, freed {freed_mb:.1f} MB")


def cleanup_old_logs():
    """Remove log files older than LOG_RETENTION_DAYS"""
    cutoff = time.time() - (LOG_RETENTION_DAYS * 86400)
    removed = 0
    for f in LOGS.glob("*.log"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            pass
    if removed:
        log(f"Cleaned {removed} old log files")


def vacuum_database():
    """Vacuum SQLite databases to reclaim space"""
    for db_file in ROOT.rglob("*.db"):
        try:
            conn = sqlite3.connect(str(db_file))
            conn.execute("VACUUM")
            conn.close()
            log(f"Vacuumed {db_file.name}")
        except Exception as e:
            log(f"Vacuum failed for {db_file.name}: {e}")


def check_concurrent_processes():
    """Check for runaway python processes and kill excess"""
    import subprocess
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -match 'AutoPost' -and $_.CommandLine -match '(autopilot|make_video|make_post|scheduler)' } | "
        "Measure-Object | Select-Object -ExpandProperty Count"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=10)
        count = int((out.stdout or "0").strip())
        if count > MAX_CONCURRENT_JOBS:
            log(f"WARNING: {count} AutoPost jobs running (max {MAX_CONCURRENT_JOBS})")
    except Exception:
        pass


def main():
    log("Starting resource optimization...")
    cleanup_out_folders()
    cleanup_temp_files()
    cleanup_old_logs()
    vacuum_database()
    check_concurrent_processes()
    log("Resource optimization complete")


if __name__ == "__main__":
    main()