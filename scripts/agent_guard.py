# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""
AutoPost - agent_guard.py    (the keep-alive brain)

Run by the Windows Scheduled Task "AutoPost-Guard" every 10 minutes, and once
~30s after every boot by "AutoPost-Guard-Boot". Idempotent and cheap: if the
agent is healthy it does nothing and exits.

What "healthy" means:
  GET http://127.0.0.1:8600/health  ->  {"ok": true, "scheduler_alive": true}
  scheduler_alive is true only while the scheduler thread has ticked in the
  last 180s, so a web server that is up but whose scheduler has wedged still
  counts as UNHEALTHY and gets recovered.

Recovery (only when unhealthy, after 3 failed checks ~15s apart):
  1. kill any stale/wedged AutoPost dashboard.py process
  2. start Ollama if its port is down
  3. make sure the venv has the packages the agent needs (pip install if not)
  4. clear orphaned 'running' rows in brain.db
  5. relaunch the dashboard (which starts the scheduler thread) with the VENV
     python, detached so it outlives this guard
  6. wait for /health to go green, then Telegram the owner:
     "recovered, was down ~N min"   or   "FAILED to recover"

Usage:
  python agent_guard.py            # normal watchdog pass
  python agent_guard.py --boot     # same, tagged as a boot run in the log
  python agent_guard.py --status   # just print health, never act
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import json, os, shutil, socket, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT      = (Path(__file__).resolve().parents[1])
PORT      = 8600
HEALTH    = "http://127.0.0.1:%d/health" % PORT
LOGDIR    = ROOT / "logs"
GUARD_LOG = LOGDIR / "guard.log"
TICK_FILE = ROOT / "data" / "last_tick.txt"
PYW       = ROOT / "venv" / "Scripts" / "pythonw.exe"
PY        = ROOT / "venv" / "Scripts" / "python.exe"
DASHBOARD = ROOT / "scripts" / "dashboard.py"
def _find_ollama():
    for c in (os.environ.get("OLLAMA_EXE", ""), r"C:\Program Files\Ollama\ollama.exe",
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
              shutil.which("ollama") or ""):
        if c and os.path.exists(c):
            return Path(c)
    return Path(r"C:\Program Files\Ollama\ollama.exe")


OLLAMA_EXE = _find_ollama()
NEED_PKGS = ["googleapiclient", "google_auth_oauthlib", "requests", "yt_dlp",
             "PIL", "faster_whisper", "trend_pulse"]
PIP_NAMES = {"googleapiclient": "google-api-python-client",
             "google_auth_oauthlib": "google-auth-oauthlib",
             "PIL": "pillow", "yt_dlp": "yt-dlp",
             "faster_whisper": "faster-whisper", "trend_pulse": "trend-pulse"}

DETACHED = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED | NEW_GROUP | NO_WINDOW
LAST_OPT = ROOT / "data" / ".last_optimize"

OPTIMIZE_EVERY_S = 6 * 3600   # run optimize_resources.py (out-folder cleanup + db vacuum) at most every 6h


def maybe_run_optimizer():
    """Periodically (every ~6h) run the resource optimizer: it deletes old
    content folders ONLY when the run is AI-verified published (>3 days old),
    deletes failed/stale folders (>1 day), vacuum-checks the DB. Runs in a
    detached subprocess so a watchdog pass is never blocked by it. The
    timestamp file makes the cadence survive reboots (cleanup continues even
    if the 6-hourly scheduled task is not present)."""
    try:
        if LAST_OPT.exists():
            last = os.path.getmtime(LAST_OPT)
            if time.time() - last < OPTIMIZE_EVERY_S:
                return
        out = open(LOGDIR / "optimize.log", "a", encoding="utf-8", errors="replace")
        subprocess.Popen([str(PY), str(ROOT / "scripts" / "optimize_resources.py")],
                         cwd=str(ROOT), stdout=out, stderr=out,
                         creationflags=DETACHED, close_fds=True)
        try:
            LAST_OPT.touch()
        except Exception:
            pass
        log("optimizer scheduled (every 6h)")
    except Exception as e:
        log("optimizer hook error: " + str(e)[:150])


def log(msg):
    LOGDIR.mkdir(parents=True, exist_ok=True)
    line = "[" + time.strftime("%Y-%m-%d %I:%M:%S %p") + "] " + msg
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(GUARD_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def health():
    """Return the parsed /health dict, or None if unreachable/bad."""
    try:
        with urllib.request.urlopen(HEALTH, timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def is_healthy(h):
    return bool(h and h.get("ok") and h.get("scheduler_alive"))


def port_up(port):
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _guard_notice(kind, what, did, need=None):
    """Plain, professional Telegram text (same format as HELENA's reports)."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import report_format
        return report_format.notice(kind, None, None, what, did, need)
    except Exception:
        return "AutoPost: " + what


def telegram(text):
    """Best-effort. Never raises."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import publish
        c = publish.load_conn()
        for key in ("telegram", "telegram_channel"):
            tg = c.get(key, {})
            tok, chat = tg.get("token"), tg.get("chat_id")
            if tok and chat:
                try:
                    publish.telegram_send(tok, chat, text)
                    return
                except Exception:
                    continue
    except Exception as e:
        log("telegram alert failed: " + str(e)[:120])


def render_running():
    """True while a department step (e.g. NOAH rendering video on the GPU) is running.
    A render can take 40+ min and starves Ollama and the scheduler heartbeat, which LOOKS
    like a hang. 2026-10-10: the guard read that as dead, killed the dashboard AND the
    in-flight render, and wrongly declared Ollama wedged. Never again - see main()."""
    try:
        import sqlite3
        c = sqlite3.connect(str(ROOT / "data" / "brain.db"), timeout=5)
        n = c.execute("SELECT COUNT(*) FROM department_steps WHERE status='running' AND "
                      "MAX(COALESCE(heartbeat,0), COALESCE(started,0)) > ?",
                      (time.time() - 5400,)).fetchone()[0]
        c.close()
        return n > 0
    except Exception:
        return False


def kill_stale_dashboard():
    """Kill any python(w) process running THIS project's dashboard.py. Only
    called when unhealthy, so we are killing something already broken.
    Also kills leftover autopilot/make_video/make_post workers whose parent
    died - those zombies hold the SQLite write lock forever and cause the
    'database is locked' cascade."""
    workers = "" if render_running() else \
        " -or $_.CommandLine -match '(make_video|make_post|autopilot|publish)\\.py'"
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -match 'AutoPost' -and "
        "($_.CommandLine -match 'dashboard\\.py'" + workers + ") } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30)
        killed = [x for x in (out.stdout or "").split() if x.strip().isdigit()]
        if killed:
            log("killed stale dashboard/worker pid(s): " + ", ".join(killed))
            time.sleep(3)
    except Exception as e:
        log("kill_stale_dashboard error: " + str(e)[:120])


def model_loaded():
    """True if Ollama currently has at least one model resident (/api/ps)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as r:
            return bool(json.load(r).get("models"))
    except Exception:
        return False


LOCK_FILE = ROOT / "data" / "agent_guard.lock"


def acquire_lock():
    """Single instance only. The 10-min task and the boot task used to run side by side and
    fight over Ollama. A lock whose PID is dead, or older than 20 min, is stale and ignored."""
    try:
        if LOCK_FILE.exists():
            pid = int(LOCK_FILE.read_text().split()[0])
            age = time.time() - LOCK_FILE.stat().st_mtime
            alive = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                                   capture_output=True, text=True).stdout.find('"%d"' % pid) >= 0
            if alive and pid != os.getpid() and age < 20 * 60:
                return False
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text("%d %s" % (os.getpid(), time.strftime("%H:%M:%S")))
    except Exception:
        pass
    return True


def release_lock():
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().split()[0] == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception:
        pass


def ensure_ollama():
    """Ollama is only 'healthy' if it can actually GENERATE, not just if the
    port is open. A wedged-but-listening server fails this probe, gets killed
    via taskkill (tree), and restarted. This is the fix for the silent hang
    where every post died at 'writing script/copy' because the model never
    answered but the port looked fine."""
    if port_up(11434):
        if render_running():
            return          # the GPU is busy rendering - a slow probe is not a wedged Ollama
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            import ai_health
            if model_loaded():
                # a loaded model must answer fast; allow one retry for a busy moment
                if ai_health.ollama_ok(timeout=25) or ai_health.ollama_ok(timeout=40):
                    return
                log("Ollama has a model loaded but generation probe FAILED - wedged, restarting it")
            else:
                # COLD START (after a reboot nothing is loaded). Loading the 14B model takes
                # 1-3 min; the old 12 s probe called that "wedged", killed Ollama and looped
                # forever (2026-10-11). Give the load the time it needs, kill only if it fails.
                log("Ollama up, no model loaded yet - waiting for the cold load (up to 240 s)")
                if ai_health.ollama_ok(timeout=240):
                    log("Ollama cold load finished, generating cleanly")
                    return
                log("Ollama did not finish a cold load in 240 s - wedged, restarting it")
        except Exception as e:
            log("ollama health probe error: " + str(e)[:120])
            return
    else:
        log("Ollama down - starting it")
    try:
        subprocess.run(["taskkill", "/F", "/T", "/IM", "ollama.exe"],
                       capture_output=True, text=True)
        subprocess.run(["taskkill", "/F", "/T", "/IM", "ollama app.exe"],
                       capture_output=True, text=True)
        subprocess.run(["taskkill", "/F", "/T", "/IM", "ollama-app.exe"],
                       capture_output=True, text=True)
        time.sleep(3)
        subprocess.Popen([str(OLLAMA_EXE)], creationflags=DETACHED)
        for _ in range(25):
            time.sleep(1.5)
            if port_up(11434):
                break
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            import ai_health
            if ai_health.ollama_ok(timeout=240):
                log("Ollama restarted and generating cleanly")
                return
        except Exception:
            pass
        log("Ollama still not generating after restart + 240 s (will retry next pass)")
    except Exception as e:
        log("ensure_ollama error: " + str(e)[:120])


def ensure_packages():
    missing = []
    for m in NEED_PKGS:
        try:
            __import__(m)
        except Exception:
            missing.append(m)
    if not missing:
        return
    names = [PIP_NAMES.get(m, m) for m in missing]
    log("venv missing packages " + str(missing) + " - pip installing " + str(names))
    try:
        subprocess.run([str(PY), "-m", "pip", "install", "--disable-pip-version-check",
                        "-q", *names], timeout=600)
    except Exception as e:
        log("pip install error: " + str(e)[:150])


def clear_stale_runs():
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import db
        n = db.clear_stale_runs()
        if n:
            log("cleared %d orphaned 'running' row(s) in brain.db" % n)
    except Exception as e:
        log("clear_stale_runs error: " + str(e)[:120])


def downtime_estimate():
    """Rough minutes since the scheduler last ticked, from last_tick.txt."""
    try:
        t = float(TICK_FILE.read_text(encoding="utf-8").strip())
        return max(0, int((time.time() - t) / 60))
    except Exception:
        try:
            return max(0, int((time.time() - TICK_FILE.stat().st_mtime) / 60))
        except Exception:
            return None


POLLER_BAT = Path(__file__).resolve().parents[1] / "START-POLLER.bat"
WATCH_FILE = ROOT / "data" / "department_watch.json"


def poller_alive():
    """Same test the poller itself uses to refuse a second copy: a fresh heartbeat file whose
    pid is still running."""
    try:
        w = json.loads(WATCH_FILE.read_text(encoding="utf-8"))
        age = time.time() - float(w.get("ts", 0))
        # a live pid that has not beaten for a while is BUSY on one long step (a render), not dead
        return nowindow.pid_alive(w["pid"]) and (age < 60 or (render_running() and age < 5400))
    except Exception:
        return False


def ensure_poller():
    """Keep the department poller running (it drives the 3 sidecar agents and every job step).

    It used to be manual-start only, so after a reboot the dispatcher, supervisor and retention
    agents were silently offline. Starting it is safe while the rails are on: it only ADVANCES
    jobs that already exist and the dispatcher refuses to enqueue while paused / disabled.
    The poller refuses to run twice, so calling this every pass is harmless."""
    try:
        # the owner stops the automation by pausing it. A paused / department-off system must NOT have
        # its poller (and the dispatcher + side desk inside it) quietly restarted by the guard.
        R = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig"))
        if R.get("paused") or not (R.get("department") or {}).get("enabled"):
            return
        if poller_alive():
            return
        sup = ROOT / "scripts" / "poller_supervisor.py"
        # hidden python supervisor (single instance, no console, no cmd/timeout windows) - NOT the old batch loop
        subprocess.Popen([str(PYW), str(sup)], cwd=str(ROOT), creationflags=DETACHED, close_fds=True)
        log("department poller was not running - started it (hidden supervisor)")
    except Exception as e:
        log("could not start poller: %s" % str(e)[:100])


def start_dashboard():
    LOGDIR.mkdir(parents=True, exist_ok=True)
    out = open(LOGDIR / "dashboard_stdout.log", "a", encoding="utf-8", errors="replace")
    subprocess.Popen([str(PYW), str(DASHBOARD)], cwd=str(ROOT),
                     stdout=out, stderr=out, creationflags=DETACHED, close_fds=True)
    log("dashboard relaunched with venv python")


def recover(boot):
    down = downtime_estimate()
    log("UNHEALTHY%s - starting recovery (last tick ~%s min ago)"
        % (" [boot]" if boot else "", down))
    kill_stale_dashboard()
    ensure_ollama()
    ensure_packages()
    clear_stale_runs()
    start_dashboard()
    for i in range(30):                     # up to ~90s for green
        time.sleep(3)
        if is_healthy(health()):
            log("recovered OK after ~%ds" % ((i + 1) * 3))
            if not boot or (down or 0) > 20:
                telegram(_guard_notice("info", "AutoPost was down for about %s minutes" % (down if down is not None else "a few"),
                                 "restarted everything at %s; missed posts are caught up, spaced out" % time.strftime("%I:%M %p").lstrip("0")))
            return 0
    log("FAILED to recover - dashboard did not go healthy within 90s")
    telegram(_guard_notice("alert", "AutoPost is down and automatic recovery failed at %s" % time.strftime("%I:%M %p").lstrip("0"),
                           "tried to restart everything", "Double-click AUTOPOST-FULL-RESTART on the Desktop."))
    return 1


def open_dashboard():
    """Open the dashboard admin page in the default browser - only called on
    a real boot/restart run (--boot), once, so the owner sees it on screen after
    a reboot instead of the server just starting silently in the background."""
    try:
        chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        if chrome.exists():          # the owner's rule: local sites open in Google Chrome only
            subprocess.Popen([str(chrome), "http://localhost:8600"], creationflags=DETACHED, close_fds=True)
            log("opened dashboard in Google Chrome")
        else:
            log("Chrome not found - not opening a browser (rule: never the system default)")
    except Exception as e:
        log("could not open browser: " + str(e)[:120])

def open_dashboard_delayed():
    """Open the dashboard in Chrome once after a boot/login. SYNCHRONOUS on purpose: the guard process exits
    as soon as main() returns, so a background thread never got to open the browser. Waits (max 60 s) for the
    dashboard to answer, then opens it. A marker file stops the two boot triggers (startup + logon) and any
    repeat run from opening a second tab within 15 minutes."""
    marker = ROOT / "data" / "browser_opened.txt"
    try:
        if marker.exists() and time.time() - marker.stat().st_mtime < 15 * 60:
            log("dashboard was already opened in the last 15 min - not opening again")
            return
    except Exception:
        pass
    for _ in range(12):
        if is_healthy(health()):
            break
        time.sleep(5)
    time.sleep(3)
    open_dashboard()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(time.time()))
    except Exception:
        pass


def ensure_tailscale():
    """Remote access must survive a reboot with nobody logged in to the tray app. 2026-10-11: after a
    reboot the daemon sat in 'NoState' (logged out, control server fetch cancelled) until
    `tailscale set --unattended` was applied. Idempotent: does nothing when an IP is already assigned."""
    ts = r"C:\Program Files\Tailscale\tailscale.exe"
    if not os.path.exists(ts):
        return

    def _ip():
        try:
            r = subprocess.run([ts, "ip", "-4"], capture_output=True, text=True, timeout=15,
                               creationflags=0x08000000)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""
    if _ip().startswith("100."):
        return
    log("Tailscale has no IP - applying unattended mode and reconnecting")
    for args in (["set", "--unattended"], ["up"]):
        try:
            subprocess.run([ts] + args, capture_output=True, text=True, timeout=60, creationflags=0x08000000)
        except Exception as e:
            log("tailscale %s failed: %s" % (args[0], str(e)[:80]))
    for _ in range(10):
        time.sleep(3)
        if _ip().startswith("100."):
            log("Tailscale connected (%s)" % _ip())
            return
    log("Tailscale still has no IP after reconnect - needs a look")


def main():
    boot = "--boot" in sys.argv
    if "--status" in sys.argv:
        h = health()
        print(json.dumps(h, indent=2) if h else "unreachable")
        return 0
    try:
        ensure_tailscale()
    except Exception as e:
        log("ensure_tailscale error (non-fatal): %s" % str(e)[:100])

    # On boot: ensure Ollama is running FIRST (before health check)
    if boot:
        log("Boot mode: ensuring Ollama is up...")
        ensure_ollama()
        time.sleep(3)
    
    h = health()
    if is_healthy(h):
        log("healthy (tick age %ss)" % h.get("scheduler_tick_age_sec"))
        ensure_poller()
        if boot:
            open_dashboard_delayed()
        maybe_run_optimizer()
        return 0
    # The web server answers but the scheduler heartbeat is stale WHILE a render runs: that
    # is load, not death. Leave everything alone - killing it would destroy the render.
    if h and h.get("ok") and render_running():
        log("scheduler tick stale (%ss) but a render is running - NOT recovering, "
            "leaving the dashboard and the render alone" % h.get("scheduler_tick_age_sec"))
        ensure_poller()
        return 0
    # not obviously healthy - re-check a few times before acting (transient load)
    for _ in range(2):
        time.sleep(7)
        if is_healthy(health()):
            log("healthy on retry")
            ensure_poller()
            if boot:
                open_dashboard_delayed()
            maybe_run_optimizer()
            return 0
    rc = recover(boot)
    if rc == 0:
        ensure_poller()
    if boot and rc == 0:
        open_dashboard_delayed()
    return rc


if __name__ == "__main__":
    if "--status" not in sys.argv and not acquire_lock():
        log("another guard run is already active - exiting (single instance)")
        sys.exit(0)
    try:
        rc = main()
    finally:
        release_lock()
    sys.exit(rc)
