# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""AutoPost - shared SQLite memory. Used by make_video.py, dashboard.py, and later run_daily/run_weekly."""
import os, sqlite3, time
from pathlib import Path

DB = (Path(__file__).resolve().parents[1] / "data" / "brain.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  topic      TEXT NOT NULL,
  kind       TEXT DEFAULT 'video',   -- video | image | text
  started    REAL NOT NULL,
  finished   REAL,
  status     TEXT NOT NULL DEFAULT 'running',   -- running | ok | failed
  stage      TEXT,                              -- current step, for live view
  pid        INTEGER,                           -- OS pid of the worker that owns this run
  scenes     INTEGER,
  seconds    REAL,                              -- video length
  build_secs REAL,                              -- how long it took to build
  size_kb    INTEGER,
  path       TEXT,
  error      TEXT,
  approval   TEXT DEFAULT 'pending',   -- pending | approved | rejected | auto
  approved_at REAL,
  published  INTEGER DEFAULT 0,
  regeneration_attempts INTEGER DEFAULT 1,   -- how many times regenerated
  category   TEXT,                           -- self_healing, spiritual, mental, physical
  tone       TEXT,                           -- inspirational, educational, motivational
  sentiment  TEXT,                           -- positive, neutral, actionable
  diversity_score REAL DEFAULT 0.5,          -- 0-1 (1 = maximum variety)
  preview_requested INTEGER DEFAULT 0,       -- 1 if user wants preview before publish
  preview_shown REAL,                        -- timestamp when preview shown
  preview_action TEXT                        -- publish | postpone | discard
);
CREATE TABLE IF NOT EXISTS posts (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id    INTEGER,
  platform  TEXT,
  posted    REAL,
  remote_id TEXT,
  url       TEXT,
  status    TEXT,
  views_48h INTEGER,
  score     TEXT,
  category  TEXT
);
CREATE TABLE IF NOT EXISTS topics_used (
  topic TEXT PRIMARY KEY, first_used REAL, times INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id    INTEGER,
  post_id   INTEGER,
  platform  TEXT,
  status    TEXT,                        -- approved | rejected | regenerated | failed
  reason    TEXT,                        -- simple, user-friendly message
  attempt   INTEGER DEFAULT 1,
  timestamp REAL,
  FOREIGN KEY(run_id) REFERENCES runs(id),
  FOREIGN KEY(post_id) REFERENCES posts(id)
);

-- Single-row cross-process GENERATION LEASE. At most ONE content generation
-- (manual OR auto) may be in flight at any moment. Whoever wins this row may
-- spawn a worker; everyone else is rejected until the owner lets go (worker
-- finishes, worker dies, or the lease goes stale).
CREATE TABLE IF NOT EXISTS gen_lease (
  id      INTEGER PRIMARY KEY CHECK(id=1),  -- exactly one row, forced
  pid     INTEGER NOT NULL,                 -- OS pid of the lease holder
  started REAL NOT NULL,                    -- when it took the lease
  source  TEXT                              -- 'manual' | 'auto' | 'manual-admin'
);

-- ---------------------------------------------------------------------
-- SOCIAL MEDIA DEPARTMENT v3.0 (added 2026-10-10)
-- 17 specialised agents, 7 stages, one post at a time. Every agent has ONE
-- primary job and writes ONE row here when it finishes, so the whole chain is
-- auditable afterwards. Purely additive - nothing above is changed or dropped.
-- ---------------------------------------------------------------------

-- One row per agent per job. status: done | rejected | skipped | pending
CREATE TABLE IF NOT EXISTS department_jobs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id    TEXT NOT NULL,          -- stable id shared by every row of one post
  run_id    INTEGER,                -- brain.db runs.id once generation starts
  agent     TEXT NOT NULL,          -- MAYA / ARJUN / ... / HELENA
  stage     INTEGER,                -- 1..7
  role      TEXT,                   -- the agent's one-line job description
  status    TEXT NOT NULL DEFAULT 'pending',
  reason    TEXT,                   -- plain-English reason (shown in the dashboard)
  version   INTEGER DEFAULT 1,      -- every revision increments this
  path      TEXT,                   -- where the artefact was written
  ts        REAL NOT NULL
);

-- Chloe's per-platform publishing packages (STAGE 5)
CREATE TABLE IF NOT EXISTS department_packages (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id    TEXT NOT NULL,
  platform  TEXT NOT NULL,
  pkg_path  TEXT,
  hash      TEXT,
  status    TEXT NOT NULL DEFAULT 'ready',   -- ready | approved | rejected
  ts        REAL NOT NULL,
  UNIQUE(job_id, platform)
);

-- Victoria's live post verification, and James's remediation audit trail (STAGE 6)
CREATE TABLE IF NOT EXISTS live_audits (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id        TEXT NOT NULL,
  platform      TEXT NOT NULL,
  status        TEXT NOT NULL,        -- pass | fail | fixed | escalated
  reason        TEXT,
  evidence_path TEXT,                 -- ffprobe output / frame grabs
  checked_at    REAL NOT NULL,
  by_agent      TEXT NOT NULL DEFAULT 'VICTORIA'
);

CREATE TABLE IF NOT EXISTS remediation (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id    TEXT NOT NULL,
  platform  TEXT,
  action    TEXT NOT NULL,           -- edited | deleted | none | escalated
  details   TEXT,
  by_agent  TEXT NOT NULL DEFAULT 'JAMES',
  ts        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS final_reports (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id      TEXT NOT NULL UNIQUE,
  status      TEXT NOT NULL,         -- PUBLISHED AND VERIFIED / PUBLISHED WITH
                                     -- CORRECTIONS / PARTIALLY PUBLISHED / FAILED /
                                     -- AWAITING HUMAN ACTION
  summary     TEXT,
  channels    TEXT,                  -- comma list, or 'none'
  delivered   INTEGER DEFAULT 0,
  ts          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dept_jobs_job ON department_jobs(job_id);
CREATE INDEX IF NOT EXISTS idx_live_audits_job ON live_audits(job_id);
"""

def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    # Enable WAL mode for better concurrency (readers don't block writers, writers don't block readers)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA synchronous=NORMAL")
    _ensure_schema(c)
    cols = [r[1] for r in c.execute("PRAGMA table_info(runs)")]
    if "kind" not in cols:                       # migrate older databases
        c.execute("ALTER TABLE runs ADD COLUMN kind TEXT DEFAULT 'video'")
        c.execute("UPDATE runs SET kind='image' WHERE topic LIKE '[image]%'")
        c.execute("UPDATE runs SET kind='text'  WHERE topic LIKE '[text]%'")
        c.commit()
    cols = [r[1] for r in c.execute("PRAGMA table_info(runs)")]
    if "pid" not in cols:                        # migrate: track owning process per run
        c.execute("ALTER TABLE runs ADD COLUMN pid INTEGER")
        c.commit()
    for col, ddl in (("approval", "TEXT DEFAULT 'pending'"),
                     ("approved_at", "REAL"),
                     ("published", "INTEGER DEFAULT 0"),
                     ("regeneration_attempts", "INTEGER DEFAULT 1"),
                     ("category", "TEXT"),
                     ("tone", "TEXT"),
                     ("sentiment", "TEXT"),
                     ("diversity_score", "REAL DEFAULT 0.5"),
                     ("preview_requested", "INTEGER DEFAULT 0"),
                     ("preview_shown", "REAL"),
                     ("preview_action", "TEXT")):
        if col not in cols:
            c.execute("ALTER TABLE runs ADD COLUMN " + col + " " + ddl)
            c.commit()
    pcols = [r[1] for r in c.execute("PRAGMA table_info(posts)")]
    if "category" not in pcols:                     # migrate older databases
        c.execute("ALTER TABLE posts ADD COLUMN category TEXT")
        c.commit()
    return c


_schema_done = False
def _ensure_schema(c):
    """Run CREATE TABLE once per process. Many workers opening connections
    concurrently was re-running these DDL statements every time, contending on
    the same write lock and producing 'database is locked' under load."""
    global _schema_done
    if _schema_done:
        return
    _schema_done = True
    for attempt in range(5):
        try:
            c.executescript(SCHEMA)
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 4:
                time.sleep(0.3 * (attempt + 1))
                continue
            raise

def _locked(fn):
    """Retry a DB write a few times on 'database is locked'. SQLite in WAL
    mode is very robust, but one long-running write from another worker can
    still hit the 30s busy timeout right at the moment a run is updating its
    stage. A transient lock must NEVER kill a content run - retry instead."""
    def wrapper(*a, **k):
        for attempt in range(6):
            try:
                return fn(*a, **k)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 5:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise
    return wrapper

@_locked
def start_run(topic, kind="video", pid=None):
    with conn() as c:
        cur = c.execute("INSERT INTO runs(topic,kind,started,status,stage,pid) VALUES(?,?,?,'running','starting',?)",
                        (topic, kind, time.time(), pid or os.getpid()))
        return cur.lastrowid

@_locked
def set_stage(run_id, stage):
    with conn() as c:
        c.execute("UPDATE runs SET stage=? WHERE id=?", (stage, run_id))

def record_regeneration_attempt(run_id, attempt_count):
    """Track how many times content was regenerated before approval."""
    if not run_id: return
    with conn() as c:
        c.execute("UPDATE runs SET regeneration_attempts=? WHERE id=?", (attempt_count, run_id))

def update_topic_attributes(run_id, category, tone, sentiment, diversity_score):
    """Store topic diversity attributes for variety tracking."""
    if not run_id: return
    with conn() as c:
        c.execute(
            "UPDATE runs SET category=?, tone=?, sentiment=?, diversity_score=? WHERE id=?",
            (category, tone, sentiment, diversity_score, run_id)
        )

@_locked
def finish_run(run_id, **kw):
    if not run_id: return
    kw["finished"] = time.time()
    kw["pid"] = None             # worker is done - it no longer owns this run
    cols = ",".join(f"{k}=?" for k in kw)
    with conn() as c:
        c.execute(f"UPDATE runs SET {cols} WHERE id=?", (*kw.values(), run_id))

@_locked
def note_topic(topic):
    with conn() as c:
        c.execute("""INSERT INTO topics_used(topic,first_used) VALUES(?,?)
                     ON CONFLICT(topic) DO UPDATE SET times=times+1""", (topic, time.time()))

def topic_seen(topic):
    with conn() as c:
        return c.execute("SELECT 1 FROM topics_used WHERE topic=?", (topic,)).fetchone() is not None


@_locked
def mark_topic_used_by_title(title):
    """Flag a row in the `topics` queue as consumed.

    note_topic() only touches the topics_used history table, so research.unused()
    keeps handing the SAME topic back - which defeats topic rotation on an audit
    retry. This is the call that actually removes it from the unused pool."""
    if not title:
        return 0
    with conn() as c:
        cur = c.execute("UPDATE topics SET used=1 WHERE title=? AND used=0", (title,))
        return cur.rowcount or 0


def request_preview(run_id):
    """Mark a post for preview before publishing."""
    if not run_id: return
    with conn() as c:
        c.execute("UPDATE runs SET preview_requested=1 WHERE id=?", (run_id,))

def get_pending_previews():
    """Get all posts waiting for preview approval."""
    with conn() as c:
        return c.execute(
            "SELECT id, topic, started FROM runs WHERE preview_requested=1 AND preview_action IS NULL "
            "ORDER BY started DESC"
        ).fetchall()

def approve_preview(run_id, action):
    """User approved/postponed/discarded post after preview."""
    if not run_id: return
    with conn() as c:
        c.execute(
            "UPDATE runs SET preview_shown=?, preview_action=? WHERE id=?",
            (time.time(), action, run_id)
        )

@_locked
def set_approval(run_id, decision):
    """decision: approved | rejected | pending"""
    with conn() as c:
        c.execute("UPDATE runs SET approval=?, approved_at=? WHERE id=?",
                  (decision, time.time(), run_id))

@_locked
def mark_published(run_id):
    with conn() as c:
        c.execute("UPDATE runs SET published=1 WHERE id=?", (run_id,))

def pending_approvals():
    with conn() as c:
        return c.execute(
            "SELECT * FROM runs WHERE status='ok' AND approval='pending' AND published=0 "
            "ORDER BY id DESC").fetchall()

def approved_unpublished():
    with conn() as c:
        return c.execute(
            "SELECT * FROM runs WHERE status='ok' AND approval IN ('approved','auto') "
            "AND published=0 ORDER BY id ASC").fetchall()


def total_published():
    """Count of distinct content pieces actually published (not per-platform sends) -
    what growth-vs-steady schedule phase reads to decide when to slow down."""
    with conn() as c:
        return c.execute("SELECT COUNT(*) n FROM runs WHERE published=1").fetchone()["n"]


def _pid_alive(pid):
    """True if the OS process is alive. Cheap, cross-PID, no exceptions, no deps."""
    if not pid:
        return False
    pid = int(pid)
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def live_running_runs():
    """List runs still status='running' that are genuinely live: owner pid set
    AND that pid's process is alive. Anything else is a zombie, not a real run."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE status='running' AND pid IS NOT NULL ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows if _pid_alive(r["pid"])]


def live_run_active():
    """True if there is at least one real live generation still in flight.
    The scheduler uses this to enforce ONE-JOB-AT-A-TIME (no parallel runs)."""
    return len(live_running_runs()) > 0


def clear_stale_runs(max_age_h=1, live_check=True):
    """Mark stuck status='running' runs as failed.
    1) live_check (default): any run whose owner process has died is failed
       immediately - a crash/kill/timeout always leaves a dead pid, so this
       finds zombies in seconds instead of waiting max_age_h.
    2) age fallback: any run that has been 'running' over max_age_h with no
       alive-process record is also failed (covers pid-less legacy rows).
    Returns how many rows were cleaned."""
    cleaned = 0
    if live_check:
        with conn() as c:
            rows = c.execute("SELECT * FROM runs WHERE status='running' AND pid IS NOT NULL").fetchall()
        for r in rows:
            if not _pid_alive(r["pid"]):
                with conn() as c:
                    c.execute(
                        "UPDATE runs SET status='failed', stage='orphaned', "
                        "error='orphaned - worker process (pid %d) died mid-run', finished=? "
                        "WHERE id=?" % (r["pid"],), (time.time(), r["id"]))
                cleaned += 1
        if cleaned:
            return cleaned
    cutoff = time.time() - max_age_h * 3600
    with conn() as c:
        cur = c.execute(
            "UPDATE runs SET status='failed', stage='orphaned', "
            "error='orphaned - process died or PC rebooted mid-run', finished=? "
            "WHERE status='running' AND started < ?", (time.time(), cutoff))
        cleaned += cur.rowcount
    return cleaned


def schedule_phase(R):
    """'growth' (daily, ramping up while the channel is new) until growth_threshold
    published posts are reached, then 'steady' (the light weekly cadence) forever
    after - matches the owner's request: post as much as safely possible early on,
    then ease off once there's a real track record."""
    threshold = R.get("growth_threshold", 50)
    return "growth" if total_published() < threshold else "steady"


def log_audit(run_id, platform, status, reason="", attempt=1, post_id=None):
    """Log editorial audit result. Status: approved | rejected | regenerated | failed."""
    with conn() as c:
        c.execute(
            "INSERT INTO audit_logs(run_id,post_id,platform,status,reason,attempt,timestamp) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, post_id, platform, status, reason, attempt, time.time())
        )


def get_audit_logs(run_id, platform=None):
    """Retrieve audit logs for a run, optionally filtered by platform."""
    with conn() as c:
        if platform:
            return c.execute(
                "SELECT * FROM audit_logs WHERE run_id=? AND platform=? ORDER BY timestamp DESC",
                (run_id, platform)
            ).fetchall()
        else:
            return c.execute(
                "SELECT * FROM audit_logs WHERE run_id=? ORDER BY timestamp DESC",
                (run_id,)
            ).fetchall()


def active_schedule(R):
    phase = schedule_phase(R)
    key = "schedule_growth" if phase == "growth" else "schedule_steady"
    return R.get(key, []) or R.get("schedule", [])   # legacy fallback for old configs


# ---------------------------------------------------------------------------
# GENERATION LEASE - enforces ONE content generation at a time across ALL
# entry points: scheduler (auto) + dashboard manual + admin programmatic.
# The problem it solves: `live_run_active()` only becomes true AFTER a worker
# registers its run row (~1-2s after spawn). In that window a manual click and
# an auto job could BOTH spawn -> two concurrent generations (the old cause of
# 'database is locked' crashes). The lease is taken synchronously at spawn
# time and handed to the worker immediately after Popen, so the window is gone.
# ---------------------------------------------------------------------------

GEN_LEASE_TTL = 12 * 3600   # a lease older than 12h is always stale (safety net)
_SPAWN_LOCK = __import__("threading").Lock()   # same-process (dashboard port) serialization


def gen_lease_owner():
    """Return (pid, started, source) of the current lease holder, or None if free."""
    with conn() as c:
        row = c.execute("SELECT pid, started, source FROM gen_lease WHERE id=1").fetchone()
    if not row:
        return None
    return (row["pid"], row["started"], row["source"])


def _steal_gen_lease(stale_pid):
    with conn() as c:
        c.execute("DELETE FROM gen_lease WHERE id=1 AND pid=?", (stale_pid,))


def generation_busy():
    """True if a generation is in flight RIGHT NOW, from ANY entry point.
    This is the SINGLE source of truth check every spawner must call before
    launching a worker (replaces both manual and auto gates)."""
    if live_run_active():                          # a worker already registered its run
        return True
    try:
        if _SPAWN_LOCK.locked():                   # same-process spawn in progress
            return True
    except Exception:
        pass
    owner = gen_lease_owner()
    if not owner:
        return False
    pid, started, _ = owner
    if time.time() - started > GEN_LEASE_TTL:
        _steal_gen_lease(pid)                      # stale holder - take back
        return False
    return _pid_alive(pid)


def acquire_gen_gate(source):
    """Spawner-side gate: call IMMEDIATELY before Popen/run. Returns True if this
    spawner may proceed to launch a worker. False = another generation is
    running (auto or manual) - do NOT spawn. Non-blocking, atomic.
    Two mechanisms: _SPAWN_LOCK serializes same-process spawners (dashboard's
    manual clicks + scheduler thread share one pid); the gen_lease row blocks
    foreign live owners (a worker running in another process)."""
    if not _SPAWN_LOCK.acquire(blocking=False):
        return False                               # same-process: already spawning
    try:
        with conn() as c:
            row = c.execute("SELECT pid, started, source FROM gen_lease WHERE id=1").fetchone()
            if row:
                owner_pid, owner_started, _ = row
                if (owner_pid != os.getpid() and _pid_alive(owner_pid)
                        and time.time() - owner_started < GEN_LEASE_TTL):
                    _SPAWN_LOCK.release()          # live foreign owner - busy
                    return False
        if live_run_active():
            _SPAWN_LOCK.release()
            return False
        with conn() as c:
            c.execute(
                "INSERT INTO gen_lease(id, pid, started, source) VALUES(1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET pid=excluded.pid, "
                "started=excluded.started, source=excluded.source",
                (os.getpid(), time.time(), source))
        return True
    except Exception:
        _SPAWN_LOCK.release()
        return False


def bind_gen_worker(worker_pid):
    """Call immediately AFTER Popen: transfer lease ownership to the spawned
    worker process so the gate stays closed for its whole lifetime, not just
    the spawner's. No-op if we no longer own it."""
    try:
        with conn() as c:
            c.execute("UPDATE gen_lease SET pid=?, started=? WHERE id=1 AND pid=?",
                      (worker_pid, time.time(), os.getpid()))
    except Exception:
        pass


def release_gen_gate():
    """Spawner releases its in-process hold (the lease already belongs to the
    worker). Always safe to call after bind_gen_worker()."""
    try:
        _SPAWN_LOCK.release()
    except Exception:
        pass


def abort_gen_gate(source):
    """Spawner failed before/at Popen: give the lease + lock back."""
    try:
        with conn() as c:
            c.execute("DELETE FROM gen_lease WHERE id=1 AND pid=?", (os.getpid(),))
    except Exception:
        pass
    try:
        _SPAWN_LOCK.release()
    except Exception:
        pass


def takeover_gen_lease():
    """Worker startup (env AUTOPOST_GEN_LEASE=1 set by its spawner): re-stamp
    ownership to our own pid + refresh the TTL, so the gate tracks the process
    that actually generates."""
    try:
        with conn() as c:
            c.execute("UPDATE gen_lease SET pid=?, started=? WHERE id=1",
                      (os.getpid(), time.time()))
    except Exception:
        pass


def release_gen_lease():
    """Worker finally: release the lease if we own it (next generation may start)."""
    try:
        with conn() as c:
            c.execute("DELETE FROM gen_lease WHERE id=1 AND pid=?", (os.getpid(),))
    except Exception:
        pass
