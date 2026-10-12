# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - scheduler.py
The autopilot daemon. Wakes every 30s, checks config/rules.json schedule, and
fires any due job: pick the highest-scoring unused topic -> make content ->
(wait for approval in manual mode, or auto-post in auto mode).

Not a separate process; import its `start()` and it runs as a daemon thread
alongside the dashboard so there is one thing to keep alive.

  python scheduler.py            # run standalone (blocks)
  from scheduler import start    # run as thread in dashboard
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import json, os, random, subprocess, sys, threading, time
from pathlib import Path

# Ensure venv is in path for googleapiclient
venv_path = (Path(__file__).resolve().parents[1] / "venv" / "Lib" / "site-packages")
if str(venv_path) not in sys.path:
    sys.path.insert(0, str(venv_path))

ROOT  = (Path(__file__).resolve().parents[1])
RULES = ROOT / "config" / "rules.json"
LOGDIR = ROOT / "logs"
sys.path.insert(0, str(ROOT / "scripts"))
import db, research, analytics
import ai_health


_ALERT_LAST = {}  # reason -> last sent timestamp, throttles identical alerts

def _alert(text, reason="general", min_interval_min=60):
    """Best-effort Telegram alert to the monitoring channel. Never raises -
    a broken alert must not take down the scheduler thread. Same-reason alerts
    are throttled to min_interval_min so a failing schedule can't spam."""
    import time as _t
    now = _t.time()
    last = _ALERT_LAST.get(reason, 0)
    if now - last < min_interval_min * 60:
        return
    _ALERT_LAST[reason] = now
    try:
        import publish
        tg = publish.load_conn().get("telegram", {})
        if tg.get("connected"):
            publish.telegram_send(tg["token"], tg["chat_id"], text)
    except Exception as e:
        log("  alert failed too: " + str(e)[:100])


def _notice(kind, topic, what, did):
    """Plain, professional Telegram text (same format as HELENA's reports)."""
    try:
        import report_format
        return report_format.notice(kind, topic, None, what, did, None)
    except Exception:
        return "AutoPost: " + what


def _kill_tree(pid):
    """Force-kill a process AND its whole child tree (ffmpeg, ffprobe, etc.) so
    a timed-out job can't leave zombie workers. Best-effort, never raises."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, timeout=15)
    except Exception:
        pass


def _launch_and_watch(args, kind, topic, job_id=None, retry=0):
    """Run a scheduled job in its own thread so a slow/stuck run never blocks
    the 30s tick loop, and CAPTURE output so a failure is never silent.
    Includes auto-retry logic (max 3 retries) for failed generations.
    ONE-GENERATION-AT-A-TIME: this function OWNS the generation gate for its
    whole lifetime (all retries included). It spawns each attempt via Popen so
    we can bind the lease to the real worker pid, and releases the gate only
    when every retry has finished - so a manual click can never overlap."""
    MAX_RETRIES = 3

    def worker():
        attempt = retry
        try:
            while attempt <= MAX_RETRIES:
                try:
                    p = subprocess.Popen(args, cwd=str(ROOT, ),
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True)
                    db.bind_gen_worker(p.pid)
                    try:
                        out, err = p.communicate(timeout=5400)
                    except subprocess.TimeoutExpired:
                        _kill_tree(p.pid)
                        out, err = p.communicate()
                        raise TimeoutError("worker timed out after 90 min")
                    LOGDIR.mkdir(parents=True, exist_ok=True)
                    (LOGDIR / "last_scheduled_run.log").write_text(
                        (out or "") + "\n" + (err or ""), encoding="utf-8")
                    if p.returncode != 0:
                        tail = (err or out or "")[-500:]
                        log("  SCHEDULED RUN FAILED (" + kind + "): " + tail[:200])
                        if attempt < MAX_RETRIES and job_id:
                            log("  AUTO-RETRY " + str(attempt + 1) + "/" + str(MAX_RETRIES) + " for " + kind + ": " + topic[:50])
                            time.sleep(10 * (attempt + 1))
                            attempt += 1
                            continue
                        _alert(_notice("warn", topic, "A scheduled " + kind + " post could not be created after " + str(MAX_RETRIES) + " tries", "the System Watchdog closes it and the next scheduled post uses another topic"))
                    else:
                        log("  scheduled run finished ok: " + kind)
                    return
                except Exception as e:
                    log("  SCHEDULED RUN CRASHED: " + str(e)[:200])
                    if attempt < MAX_RETRIES and job_id:
                        log("  AUTO-RETRY " + str(attempt + 1) + "/" + str(MAX_RETRIES) + " for " + kind + " (crash)")
                        time.sleep(10 * (attempt + 1))
                        attempt += 1
                        continue
                    _alert(_notice("warn", topic, "A scheduled " + kind + " post could not be started", "it will be tried again by itself"))
                    return
        finally:
            # our worker thread is done (success, all retries exhausted, or
            # timer) - give the generation gate back so a new run may start.
            try:
                db.release_gen_gate()
            except Exception:
                pass
    threading.Thread(target=worker, daemon=True).start()

TICK = 30          # seconds between schedule checks
RESEARCH_REFRESH_INTERVAL = 900  # 15 minutes - refresh topics frequently for freshness
WEEKDAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
PY = str(ROOT / "venv" / "Scripts" / "python.exe")   # console exe so stdout/stderr are capturable, not pythonw
STATE_FILE = ROOT / "data" / "scheduler_state.json"
HEARTBEAT  = ROOT / "data" / "last_tick.txt"   # touched every tick; watchdog + /health read it
LAST_RESEARCH_REFRESH = 0  # timestamp of last research refresh


def log(m):
    try:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m, flush=True)
    except UnicodeEncodeError:
        print("[" + time.strftime("%I:%M:%S %p") + "] " + m.encode("ascii", "replace").decode("ascii"), flush=True)


class _DeskOwns(Exception):
    """Not an error: research and learning now belong to the side desk (desk_work.py)."""


def load_rules():
    try:
        return json.loads(RULES.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_rules(R):
    RULES.write_text(json.dumps(R, indent=2), encoding="utf-8")


def _load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st), encoding="utf-8")


def _jittered_target(job, state, day_key):
    """Pick this job's actual fire minute for TODAY: base time + a random offset
    from jitter_minutes (picked once per day, cached, so it doesn't re-randomize
    every 30s tick). Posting at a slightly different minute each time - instead of
    the exact same second forever - looks human, not like a bot script. This is a
    SAFETY feature (reduces automated-pattern fingerprint), not a frequency change:
    it never posts more often, only shifts WHEN within the same day."""
    jid = job.get("id", job.get("type"))
    key = jid + "_jitter"
    cached = state.get(key) or {}
    if cached.get("day") == day_key:
        return cached["hh"], cached["mm"]
    try:
        hh, mm = map(int, str(job.get("time", "06:00")).split(":"))
    except Exception:
        hh, mm = 6, 0
    pool = job.get("jitter_minutes") or []
    if pool:
        offset = random.choice([0] + list(pool))  # 0 = sometimes exactly on time too
        total = hh * 60 + mm + offset
        hh, mm = (total // 60) % 24, total % 60
    state[key] = {"day": day_key, "hh": hh, "mm": mm}
    return hh, mm


def _is_catchup(job, state, now):
    """True if this job's target time already passed by more than ~3 min -
    i.e. we are firing it late (after downtime), not on schedule. Display only."""
    day_key = time.strftime("%Y%m%d", now)
    hh, mm = _jittered_target(job, state, day_key)
    return (now.tm_hour * 60 + now.tm_min) - (hh * 60 + mm) > 3


def _due_at_time(job, state, now, R):
    """Catch-up aware. A timed job is due when ALL of:
      - today is one of its weekdays, AND
      - it has NOT already fired today (state records the last fire day, so the
        30s tick can never double-post - that IS a real flag risk), AND
      - its (jittered) target time has arrived OR already passed, AND
      - it is not more than reliability.catchup_max_hours late.

    The last two points are the fix for the silent-outage bug: the old code
    fired a job ONLY if the clock matched its target minute exactly, so ANY
    downtime across that minute (PC asleep, reboot, crash) dropped that post
    with no retry. Now a missed slot is simply 'still due' and gets picked up
    the moment the agent is back - unless it's so late (default >8h) that
    posting it would do more harm than good, in which case the slot is written
    off for the day."""
    day_key = time.strftime("%Y%m%d", now)
    days = str(job.get("days", "daily")).lower()
    if not (days in ("daily", "everyday", "all") or WEEKDAY.get(days[:3]) == now.tm_wday):
        return False
    jid = job.get("id", job.get("type"))
    last = state.get(jid) or 0.0
    if isinstance(last, float) and 1600000000 < last < 2000000000:
        if time.strftime("%Y%m%d", time.localtime(last)) == day_key:
            return False
    elif str(last) == day_key:
        return False
    hh, mm = _jittered_target(job, state, day_key)
    target_min = hh * 60 + mm
    now_min = now.tm_hour * 60 + now.tm_min
    if now_min < target_min:
        return False
    max_late = int((R or {}).get("reliability", {}).get("catchup_max_hours", 8)) * 60
    if now_min - target_min > max_late:
        state[jid] = day_key          # too stale to be worth posting - write it off for today
        return False
    return True


def _due_interval(job, state, now_ts):
    """True if an interval job is due: now - last_fire >= interval_hours."""
    itv = float(job.get("interval_hours", 0))
    if itv <= 0:
        return False
    last = state.get(job.get("id"), 0.0)
    return last == 0.0 or (now_ts - last) >= itv * 3600


def _due(job, state, now_ts, now, R=None):
    if not job.get("enabled"):
        return False
    if job.get("interval_hours"):
        return _due_interval(job, state, now_ts)
    return _due_at_time(job, state, now, R)


def _dynamic_jobs(R, state, cfg_key, id_prefix, kind_choices, note, jitter, only_platforms=None):
    """Shared generator: today's randomized posts of some kind - a genuinely
    different COUNT and set of TIMES each day (not just minute-jitter on a fixed
    template). Generated once per day, cached in scheduler_state.json under
    today's date so it doesn't reshuffle every tick, then fed into the normal
    _due()/_jittered_target() machinery exactly like a static job.

    only_platforms: if set, these jobs are hard-restricted to publish ONLY to
    these platforms (run_job/publish.py enforce this) - unlike the "platforms"
    field below, which is display-only and does not restrict anything."""
    cfg = R.get(cfg_key, {})
    if not cfg.get("enabled"):
        return []
    day_key = time.strftime("%Y%m%d")
    st = state if state is not None else _load_state()
    cache_key = cfg_key + "_" + day_key
    cached = st.get(cache_key)
    if cached:
        return cached
    lo, hi = cfg.get("count_range", [1, 2])
    n = random.randint(int(lo), int(hi))
    w_start, w_end = cfg.get("window", ["09:00", "22:00"])
    sh, sm = map(int, w_start.split(":"))
    eh, em = map(int, w_end.split(":"))
    start_min, end_min = sh * 60 + sm, eh * 60 + em
    platforms = cfg.get("platforms", ["telegram"])
    pool = list(range(start_min, end_min))
    # optional "quiet band" - e.g. cap how many can land 8-11am, a low-traffic
    # window the owner wants mostly avoided without banning it outright
    rband = cfg.get("restricted_band")
    if rband:
        rsh, rsm = map(int, rband[0].split(":"))
        reh, rem = map(int, rband[1].split(":"))
        r_start, r_end = rsh * 60 + rsm, reh * 60 + rem
        in_band = [m for m in pool if r_start <= m < r_end]
        out_band = [m for m in pool if not (r_start <= m < r_end)]
        r_max = int(cfg.get("restricted_max", 1))
        n_in = min(r_max, len(in_band), n)
        n_out = min(n - n_in, len(out_band))
        minutes = sorted(random.sample(in_band, n_in) + random.sample(out_band, n_out))
    else:
        minutes = sorted(random.sample(pool, min(n, max(1, len(pool)))))
    jobs = []
    for i, m in enumerate(minutes):
        hh, mm = m // 60, m % 60
        job = {
            "id": id_prefix + "-" + day_key + "-" + str(i),
            "enabled": True, "days": "daily",
            "time": f"{hh:02d}:{mm:02d}",
            "type": random.choice(kind_choices),
            "platforms": platforms,
            "note": note,
            "jitter_minutes": jitter,
        }
        if only_platforms:
            job["only_platforms"] = only_platforms
        jobs.append(job)
    st[cache_key] = jobs
    if state is None:
        _save_state(st)
    return jobs


def dynamic_filler_jobs(R, state=None):
    """Today's randomized filler (text/image) posts with adaptive content mix."""
    # Get adaptive mix based on performance
    try:
        text_ratio, image_ratio, video_ratio = analytics.recommend_content_mix(7)
        # Use weighted random selection for content types
        if random.random() < (text_ratio / (text_ratio + image_ratio)):
            types = ["text"]
        else:
            types = ["image"]
    except Exception:
        types = ["image", "text"]  # Fallback if analytics fails

    return _dynamic_jobs(R, state, "dynamic_fillers", "dynfiller", types,
                         "A quick post (adaptive mix) at a random time so posting doesn't look robotic.",
                         [5, 10, 15, 20])


def dynamic_video_jobs(R, state=None):
    """Today's randomized video count (1 or 2, per the owner's request) and times -
    still respects YouTube's own safe 2/day guidance via count_range."""
    return _dynamic_jobs(R, state, "dynamic_videos", "dynvideo", ["video"],
                         "A new video, at a random time - 1 or 2 a day depending on the day.",
                         [10, 20, 35, 50])


def dynamic_tiktok_jobs(R, state=None):
    """TikTok's OWN video track: separate from YouTube's dynamic_video_jobs above -
    its own videos, its own count (3/day), its own window (noon-5pm), and hard-
    restricted (only_platforms) so these never also post to YouTube/Telegram/etc."""
    return _dynamic_jobs(R, state, "dynamic_tiktok", "dyntiktok", ["video"],
                         "A TikTok-only video, 3 a day between 12pm-5pm.",
                         [5, 10, 15], only_platforms=["tiktok"])


def next_fire(job, from_t=None):
    """Human-readable next fire (for display). Interval jobs -> 'every N hours'."""
    if job.get("interval_hours"):
        return "every " + str(job.get("interval_hours")) + "h"
    from datetime import datetime, timedelta
    from_t = from_t or datetime.now()
    try:
        hh, mm = map(int, str(job.get("time", "06:00")).split(":"))
    except Exception:
        return "?"
    jitter = job.get("jitter_minutes") or []
    jitter_note = (" (+0-" + str(max(jitter)) + "m random)") if jitter else ""
    days = str(job.get("days", "daily")).lower()
    target = from_t.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= from_t:
        target += timedelta(days=1)
    if days not in ("daily", "everyday", "all"):
        want = WEEKDAY.get(days[:3])
        while target.weekday() != want:
            target += timedelta(days=1)
    return target.strftime("%Y-%m-%d %I:%M %p") + jitter_note


def pick_topic(kind):
    """Highest-scoring unused topic for this content type. Return (id, title) or None."""
    rows = research.unused(40)
    if not rows:
        return None
    # best-ranked topic that is not a near-duplicate of something already used. ARJUN refuses
    # near-duplicates (a dead-lettered job, red in the office), so skip them HERE, once.
    import fact_grounding
    for r in rows:
        if not fact_grounding.is_subject(r["title"])[0]:
            continue                       # a personal Reddit story is not a subject a post can be about
        try:
            fresh, _similar = research.check_fresh(r["title"])
        except Exception:
            fresh = True
        if fresh:
            return r["id"], r["title"]
        research.mark(r["id"], "skipped")
    return None


def run_job(job):
    """Start one scheduled job (make + auto-publish, or make + queue for approval).
    Returns True if a worker was actually launched, False if it could not start
    (no unused topic, spawn error) - the caller uses this so a job that fails to
    start is retried on a later tick instead of being marked done."""
    kind = job.get("type", "video")
    R = load_rules()
    r = pick_topic(kind)
    if not r:
        log("  no unused topic - skipping scheduled run")
        return False
    # DEPARTMENT hand-off: only when rules.json department.enabled is true (it is false by
    # default, so this block changes nothing today). The department's own engine then owns
    # the lease, retries, timeouts and the 18-agent chain for this topic.
    try:
        import department_gate
        if department_gate.enabled():
            tid, topic = r
            jid = department_gate.submit(topic, kind, job.get("only_platforms"), source="scheduler")
            if jid:
                research.mark(tid, "used")
                db.note_topic(topic)
                log("  handed to the department: " + jid + " (" + kind + ") " + topic[:60])
                return True
            log("  department could not queue the job - will retry on a later tick")
            return False
    except Exception as e:
        log("  department hand-off failed (" + str(e)[:100] + ") - using the old path")
    # ONE-GENERATION-AT-A-TIME lease: auto fires only if no other generation
    # (manual included) holds it. Won atomically; the spawned worker takes it
    # over via --lease and returns it when it exits.
    if not db.acquire_gen_gate("auto"):
        log("  lease busy - a generation is already in flight; holding job for next tick")
        return False
    tid, topic = r
    log("  picked: " + topic[:70])
    only_platforms = job.get("only_platforms")
    if R.get("approval_mode") == "auto":
        # autopilot worker: make AND auto-publish to connected platforms
        args = [PY, str(ROOT / "scripts" / "autopilot.py"), "--topic", topic,
                "--kind", kind, "--scenes", str(job.get("scenes", 4)), "--lease"]
        if only_platforms:
            args += ["--only-platforms", ",".join(only_platforms)]
    else:
        # manual mode: make only, the owner approves from the dashboard
        script = "make_video.py" if kind == "video" else "make_post.py"
        args = [PY, str(ROOT / "scripts" / script), "--lease"]
        args += ([topic, "--scenes", "4"] if kind == "video" else [kind, topic])
    try:
        _launch_and_watch(args, kind, topic, job.get("id", "unknown"))
    except Exception as e:
        log("  spawn failed: " + str(e)[:100])
        db.abort_gen_gate("auto")
        _alert(_notice("warn", None, "A scheduled " + kind + " post could not be started", "it will be tried again by itself"), reason=("spawn-" + kind))
        return False
    research.mark(tid, "used")
    db.note_topic(topic)
    log("  scheduled -> " + kind + " " + ("auto-published" if R.get("approval_mode") == "auto" else "queued for approval"))
    return True


def _loop(stop):
    log("scheduler daemon started, tick " + str(TICK) + "s")
    try:
        n = db.clear_stale_runs()
        if n:
            log("cleared " + str(n) + " orphaned run(s) left 'running' by a prior crash/reboot")
    except Exception as e:
        log("stale-run cleanup skipped: " + str(e)[:120])
    last_launch = 0.0     # ts of the most recent job we started - drives the catch-up pacer
    last_research_refresh = 0.0  # timestamp of last research refresh
    last_cleanup = 0.0    # timestamp of last stale-run sweep (every ~5 min)
    while not stop.is_set():
        try:                                    # heartbeat: prove the scheduler thread is alive
            HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
            HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
        except Exception:
            pass
        try:
            R = load_rules()
            if R.get("paused"):
                stop.wait(TICK)
                continue
            rel = R.get("reliability", {})
            gap = float(rel.get("catchup_gap_min", 15)) * 60   # min seconds between launches
            cap = int(rel.get("max_posts_per_day", 12))        # hard ceiling incl. catch-up
            now = time.localtime()
            now_ts = time.time()
            state = _load_state()
            day_key = time.strftime("%Y%m%d", now)
            is_new_day = ("dynamic_fillers_" + day_key) not in state   # dynamic lists regenerated below
            if is_new_day:   # new day - drop old cached lists / counters so state doesn't grow forever
                for k in [k for k in list(state) if (k.startswith("dynamic_fillers_")
                         or k.startswith("dynamic_videos_") or k.startswith("dynamic_tiktok_")
                         or k.startswith("_launched_")) and not k.endswith(day_key)]:
                    del state[k]
            jobs_today = (db.active_schedule(R) + dynamic_filler_jobs(R, state)
                         + dynamic_video_jobs(R, state) + dynamic_tiktok_jobs(R, state))
            lk = "_launched_" + day_key
            launched_today = int(state.get(lk, 0))
            due_jobs = [j for j in jobs_today if _due(j, state, now_ts, now, R)]
            if due_jobs:
                pending = len(due_jobs)
                if launched_today >= cap:
                    log("daily cap (" + str(cap) + ") reached - holding " + str(pending) + " pending job(s)")
                elif now_ts - last_launch < gap:
                    wait_m = int((gap - (now_ts - last_launch)) / 60)
                    log(str(pending) + " job(s) pending - pacing to avoid a burst, next fire in ~"
                        + str(wait_m) + "m")
                else:
                    # fire exactly ONE job per pass; the rest stay 'due' and drain
                    # one per `gap`, so a post-downtime backlog goes out smoothly
                    # instead of all at once (which trips spam filters).
                    if db.generation_busy():
                        log("  ONE-GENERATION-AT-A-TIME: a live generation is already in flight - holding the queue")
                    elif not ai_health.ollama_ok(timeout=15):
                        log("  OLLAMA NOT RESPONSIVE - holding job, will retry next tick")
                        _alert(_notice("warn", None, "The AI engine is not answering, so new posts are paused", "waiting for it, and restarting it automatically if needed"), reason="ollama-wedged", min_interval_min=60)
                    else:
                        job = due_jobs[0]
                        jid = job.get("id", job.get("type"))
                        tag = " [CATCH-UP]" if _is_catchup(job, state, now) else ""
                        more = ("  (" + str(pending - 1) + " more queued)") if pending > 1 else ""
                        log("firing job: " + str(job.get("type")) + " " + jid + tag + more)
                        ok = run_job(job)
                        last_launch = now_ts        # respect the gap before the next fire either way
                        if ok:
                            state[jid] = now_ts
                            state[lk] = launched_today + 1
            _save_state(state)
            if (load_rules().get("department") or {}).get("enabled"):
                raise _DeskOwns()     # ARJUN scouts topics, LEO studies views: see desk_work.py
            try:
                import learn
                learn.refresh_if_due()   # rate-limited internally, safe every tick
            except Exception as e:
                log("learning refresh skipped: " + str(e)[:120])
            try:
                # Refresh research topics every 15 minutes for fresh content
                if now_ts - last_research_refresh >= RESEARCH_REFRESH_INTERVAL:
                    import research
                    n = research.refresh()
                    if n:
                        log("research: refreshed topics (" + str(n) + " new)")
                    last_research_refresh = now_ts
            except Exception as e:
                log("research refresh skipped: " + str(e)[:120])
        except _DeskOwns:
            pass
        except Exception as e:
            log("scheduler error: " + str(e)[:120])
        try:                                    # periodic zombie sweep: a run whose
            _now = time.time()                  # owner process died is failed within
            if _now - last_cleanup >= 300:      # minutes, not left 'running' for hours
                last_cleanup = _now
                n = db.clear_stale_runs()
                if n:
                    log("swept " + str(n) + " stale run(s) out of 'running'")
        except Exception as e:
            log("stale sweep skipped: " + str(e)[:120])
        stop.wait(TICK)


def start():
    """Start scheduler in a background daemon thread. Safe to call repeatedly."""
    if getattr(start, "_running", False):
        return start._running
    stop = threading.Event()
    t = threading.Thread(target=_loop, args=(stop,), daemon=True)
    t.start()
    start._running = t
    return t


if __name__ == "__main__":
    R = load_rules()
    print("AutoPost Scheduler - phase: " + db.schedule_phase(R))
    for j in db.active_schedule(R):
        if j.get("enabled"):
            print("  next", j.get("type").ljust(6), next_fire(j), "->", j.get("note", ""))
    _loop(threading.Event())
