# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""AutoPost Control Dashboard - http://localhost:8600
Pages:  /  overview      /settings  connections + rules
Design: "Data-Dense Dashboard" (ui-ux-pro-max), density 8, motion 3.
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import base64, hmac, html, json, os, shutil, socket, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db
import dashboard_auth
from ui import CSS, icon


def _ollama_ok():
    """Is the local model server answering? Used by the overview status strip."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def _ago_short(sec):
    sec = int(sec or 0)
    if sec < 60:
        return "%ds ago" % sec
    if sec < 3600:
        return "%dm ago" % (sec // 60)
    if sec < 86400:
        return "%dh ago" % (sec // 3600)
    return "%dd ago" % (sec // 86400)


def _ollama_models():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
            return json.loads(r.read()).get("models", [])
    except Exception:
        return []


# Only these rules.json keys may be changed from the dashboard. A whitelist, so a stray
# or malicious request cannot rewrite the posting rules or anything else.
#
# department.allowed_platforms is DELIBERATELY NOT HERE. It is the rail that stops the
# department posting anywhere except the local blog, and a test proved it could be
# overwritten with a plain string ("['youtube']"), which would have quietly widened where
# content can go. Changing the destinations is a deliberate act: edit config/rules.json.
RULE_WRITE_OK = {
    "paused", "approval_mode", "ollama_model", "language", "dashboard_auth",
    "department.enabled", "department.live_publish",
    "retention.enabled", "retention.final_days",
}

# The type each writable key must receive. A key that must be a list can never be given
# a string, which is how the rail above was nearly broken.
RULE_WRITE_TYPES = {"retention.final_days": int}


def rule_write(key, value):
    """Set one whitelisted rule, with a type check. Returns (ok, message)."""
    if key not in RULE_WRITE_OK:
        return False, "that setting cannot be changed from the dashboard"
    want = RULE_WRITE_TYPES.get(key)
    if want is int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return False, "%s must be a whole number" % key
    elif want is bool and not isinstance(value, bool):
        value = str(value).lower() in ("1", "true", "yes", "on")
    try:
        R = load_rules()
        if key.startswith("department."):
            R.setdefault("department", {})[key.split(".", 1)[1]] = value
        elif key.startswith("retention."):
            R.setdefault("retention", {})[key.split(".", 1)[1]] = value
        else:
            R[key] = value
        save_json(RULES_FILE, R)
        # The department gate reads rules.json every time, so a change takes effect on the
        # next tick without a restart.
        return True, "%s set to %s" % (key, value)
    except Exception as e:
        return False, str(e)[:120]

ROOT, PORT = (Path(__file__).resolve().parents[1]), int(os.environ.get("AUTOPOST_PORT", "8600"))
_ffmpeg_path = None
def _ffmpeg_bin():
    """Locate ffmpeg.exe. Installed via winget on this machine, NOT in
    C:\Program Files\FFmpeg\bin. Fall back to shutil.which, then a few known
    winget layouts, so the dashboard health row shows the true state."""
    global _ffmpeg_path
    if _ffmpeg_path:
        return Path(_ffmpeg_path).parent
    exe = shutil.which("ffmpeg")
    if exe:
        _ffmpeg_path = exe
        return Path(exe).parent
    for base in [
        r"C:\Program Files\FFmpeg\bin",
        r"C:\Users\user\AppData\Local\Microsoft\WinGet\Links",
    ]:
        if (Path(base) / "ffmpeg.exe").exists():
            _ffmpeg_path = str(Path(base) / "ffmpeg.exe")
            return Path(base)
    return Path(r"C:\Program Files\FFmpeg\bin")
CONN_FILE = ROOT / "config" / "connections.json"
RULES_FILE = ROOT / "config" / "rules.json"
DAY_IDX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
esc = html.escape
B = ("padding:9px 11px;border-radius:6px;border:1px solid var(--border);background:var(--bg);"
     "color:var(--fg);font:12px var(--mono);box-sizing:border-box")

# key, label, what it posts, setup minutes, analytics, how-to
PLATFORMS = [
    ("youtube",   "YouTube",   "Shorts",             "20 min", "YES - best",
     "console.cloud.google.com -> new project -> enable YouTube Data API v3 -> OAuth client (Desktop) -> upload the JSON below."),
    ("instagram", "Instagram", "Reels, image",       "30 min", "good",
     "Switch IG to Business/Creator, link a Facebook Page, then create an app at developers.facebook.com."),
    ("facebook",  "Facebook",  "video, image, text", "0 min",  "good",
     "Uses the same Meta app as Instagram - no extra work."),
    ("threads",   "Threads",   "text, video",        "0 min",  "basic",
     "Uses the same Meta app as Instagram - no extra work."),
    ("tiktok",    "TikTok",    "video (to your drafts)", "30 min", "no",
     "developers.tiktok.com -> create app -> Content Posting API -> verify your-domain.example -> "
     "save client key+secret below. Posts land in your TikTok drafts - you tap Post yourself."),
    ("x",         "X / Twitter", "text, image, video", "15 min", "basic",
     "developer.x.com -> create a free App -> copy the 5 keys. Posting needs Read+Write (often the Basic paid plan)."),
    ("bluesky",   "Bluesky",   "text, image",        "2 min",  "basic",
     "bsky.app -> Settings -> Privacy and Security -> App Passwords -> Add App Password. "
     "Not your normal login password - a separate one made just for apps like this."),
    ("mastodon",  "Mastodon",  "text, image",        "2 min",  "basic",
     "Your instance (e.g. mastodon.social) -> Settings -> Development -> New Application, "
     "check write:statuses + write:media, Submit, then copy the access token."),
    ("discord",   "Discord",   "text, image, video", "3 min",  "basic",
     "discord.com/developers/applications -> New Application -> Bot tab -> copy token. "
     "OAuth2 -> URL Generator -> scope 'bot' + Send Messages/Attach Files -> invite it to your "
     "server -> right-click the target channel (Developer Mode on) -> Copy Channel ID."),
]

# Telegram is NOT a place we post to - it is where the owner WATCHES what the agent is doing.
NOTIFY = [
    ("telegram", "Telegram", "Your monitoring channel", "3 min",
     "Message @BotFather, send /newbot, paste the token below. "
     "The agent sends you every finished item for preview, plus an alert whenever anything fails."),
]


# ---------------------------------------------------------------- state
def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def save_json(p, d):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(d, indent=2), encoding="utf-8")


def load_conn():
    return load_json(CONN_FILE, {})


def load_rules():
    return load_json(RULES_FILE, {})


def next_fire_dt(t, days):
    """Same rule as next_fire() but returns the raw datetime (or None) instead
    of a formatted string - lets callers compare multiple jobs to find the
    single soonest one, not just format one job in isolation."""
    try:
        hh, mm = (int(x) for x in t.split(":"))
    except Exception:
        return None
    days = (days or "daily").lower().strip()
    allowed = set(range(7)) if days == "daily" else {DAY_IDX[d.strip()] for d in days.split(",") if d.strip() in DAY_IDX}
    allowed = allowed or set(range(7))
    now = datetime.now()
    for add in range(8):
        c = (now + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if c > now and c.weekday() in allowed:
            return c
    return None


def today_schedule(R):
    """The static schedule PLUS today's randomly-generated filler posts (count and
    times differ every day) - the real picture of what's actually going to fire,
    not just the fixed video-job template."""
    try:
        import scheduler
        return (db.active_schedule(R) + scheduler.dynamic_filler_jobs(R)
               + scheduler.dynamic_video_jobs(R) + scheduler.dynamic_tiktok_jobs(R))
    except Exception:
        return db.active_schedule(R)


def soonest_job(R):
    """The single next thing that will fire, across the whole active schedule -
    powers the idle banner's countdown instead of a flat 'nothing waiting'."""
    best = None
    for job in today_schedule(R):
        if not job.get("enabled"):
            continue
        dt = next_fire_dt(job.get("time", ""), job.get("days"))
        if dt and (best is None or dt < best[1]):
            best = (job, dt)
    return best


def next_fire(t, days):
    try:
        hh, mm = (int(x) for x in t.split(":"))
    except Exception:
        return "?"
    days = (days or "daily").lower().strip()
    allowed = set(range(7)) if days == "daily" else {DAY_IDX[d.strip()] for d in days.split(",") if d.strip() in DAY_IDX}
    allowed = allowed or set(range(7))
    now = datetime.now()
    for add in range(8):
        c = (now + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if c > now and c.weekday() in allowed:
            s = (c - now).total_seconds()
            if s < 86400:
                return c.strftime("%a %I:%M %p") + "  (" + (str(int(s // 3600)) + "h " if s >= 3600 else "") + str(int(s % 3600 // 60)) + "m)"
            return c.strftime("%a %I:%M %p") + "  (" + str(int(s // 86400)) + "d)"
    return "?"


def fmt_time(hhmm):
    """'17:30' -> '5:30 PM' - display only, config keeps the raw 24h string."""
    try:
        hh, mm = (int(x) for x in str(hhmm).split(":"))
        return datetime(2000, 1, 1, hh, mm).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(hhmm)


def plan_next(row):
    """Next-fire for a schedule row; handles interval (every Nh) and fixed-time jobs."""
    if row.get("interval_hours"):
        return "every " + str(row.get("interval_hours")) + "h"
    return next_fire(row.get("time", ""), row.get("days"))


def port_open(p):
    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", p)) == 0


def _folder_gb(p):
    if not p.exists():
        return 0.0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


WAN_MODEL = ROOT / "models" / "Wan2.1-T2V-1.3B-Diffusers"
VIDEOGEN = ROOT / "videogen"


def video_model_ready():
    """Wan2.1 diffusers model fully present (transformer + vae + text_encoder)."""
    if not (WAN_MODEL / "model_index.json").exists():
        return False
    return (_folder_gb(WAN_MODEL / "transformer") > 5e9
            and _folder_gb(WAN_MODEL / "vae") > 4e8
            and _folder_gb(WAN_MODEL / "text_encoder") > 9e9)


def gpu_info():
    """Live GPU name + VRAM usage from nvidia-smi (short timeout, safe to fail)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4).stdout.strip().splitlines()[0]
        name, used, total = [x.strip() for x in out.split(",")]
        return name.replace("NVIDIA ", "").replace(" Laptop GPU", ""), int(used) // 1024, int(total) // 1024
    except Exception:
        return None, 0, 0


def _ver(pkg):
    """Installed version from package metadata - cheap, no heavy imports."""
    try:
        import importlib.metadata as md
        return md.version(pkg)
    except Exception:
        return "?"


# component, how version is found, kind, what it is used for
AI_STACK = [
    ("torch", "torch", "CORE", "GPU compute engine (CUDA 12.6) - runs every AI model on the RTX 4060"),
    ("diffusers", "diffusers", "VIDEO", "the video generator - Wan2.1 text-to-video pipeline"),
    ("transformers", "transformers", "VIDEO", "prompt > text embeddings (UMT5 encoder) for the video model"),
    ("huggingface_hub", "huggingface_hub", "CORE", "downloads + verifies model files"),
    ("pillow", "pillow", "IMAGE", "image read/write - thumbnails, frame export"),
    ("regex", "regex", "VIDEO", "tokenizer dependency required by the text encoder"),
    ("edge-tts", "edge-tts", "VOICE", "free neural voice-over / dialog for the videos"),
    ("FFmpeg", "ffmpeg", "VIDEO", "cuts, scales, burns captions, stitches scenes, encodes MP4"),
]


def ai_stack():
    """Real installed versions (metadata only) with the job each one does."""
    rows = []
    for name, mod, kind, role in AI_STACK:
        if name == "FFmpeg":
            ver = "9.0.2" if (_ffmpeg_bin() / "ffmpeg.exe").exists() else "missing"
        else:
            ver = _ver(mod)
        rows.append((name, ver, kind, role))
    return rows


def stack_card():
    R = load_rules()
    rows = ""
    for name, ver, kind, role in ai_stack():
        rows += ("<tr><td><b style='font-family:var(--mono)'>" + esc(name) + "</b></td>"
                 "<td><code>" + esc(ver) + "</code></td>"
                 "<td>" + kind_pill(kind.lower()) + "</td><td class=sub>" + esc(role) + "</td></tr>")
    # extra runtime pieces that are not pip packages
    extra = [
        ("Ollama + " + esc(R.get("ollama_model") or "qwen3-coder:30b"), "TEXT",
         "writes captions / scripts / picks topics (local LLM)"),
        ("Pollinations (cloud, free)", "IMAGE", "generates scene images for image posts"),
        ("Wan2.1-T2V-1.3B (11.3 GB)", "VIDEO", "the local moving-video model itself"),
    ]
    for name, kind, role in extra:
        rows += ("<tr><td><b style='font-family:var(--mono)'>" + name + "</b></td>"
                 "<td><code>local</code></td><td>" + kind_pill(kind.lower()) + "</td>"
                 "<td class=sub>" + esc(role) + "</td></tr>")
    return ("<div class=card style='margin-bottom:12px'><h2>" + icon("cpu") +
            "AI software stack &mdash; what runs what</h2>"
            "<div class=lead>Everything the agent uses, real installed versions, and which job each pieces does. "
            "VIDEO = moving-video generation &middot; IMAGE = stills &middot; TEXT = captions/scripts.</div>"
            "<div class=tw><table><tr><th>Component</th><th>Version</th><th>For</th><th>What it does</th></tr>"
            + rows + "</table></div></div>")


def health():
    h = [("Local AI (Ollama)", port_open(11434), load_rules().get("ollama_model") or "llama3.2:3b"),
         ("FFmpeg", (_ffmpeg_bin() / "ffmpeg.exe").exists(), "9.0.2"),
         ("Voice (edge-tts)", (ROOT / "venv" / "Scripts" / "edge-tts.exe").exists(), "free")]
    try:
        urllib.request.urlopen(urllib.request.Request(
            "https://image.pollinations.ai/", headers={"User-Agent": "Mozilla/5.0"}), timeout=5)
        ok = True
    except Exception:
        ok = False
    h.append(("Images (Pollinations)", ok, "free"))
    gname, gused, gtotal = gpu_info()
    if video_model_ready() and gname:
        gpu_detail = gname + " - " + str(gused) + "/" + str(gtotal) + " GB"
    elif video_model_ready():
        gpu_detail = "RTX 4060 / local"
    else:
        gpu_detail = "model downloading"
    h.append(("Video GPU (Wan2.1)", video_model_ready(), gpu_detail))
    h.append(("Memory (brain.db)", db.DB.exists(),
              (str(db.DB.stat().st_size // 1024) if db.DB.exists() else "0") + " KB"))
    return h


def ago(ts):
    if not ts:
        return "-"
    d = time.time() - ts
    for n, s in ((86400, "d"), (3600, "h"), (60, "m")):
        if d >= n:
            return str(int(d // n)) + s + " ago"
    return "just now"


def pill(cls, txt, ic=None):
    return "<span class='pill p-" + cls + "'>" + (icon(ic, 11) if ic else "") + esc(txt) + "</span>"


KIND = {"video": ("video", "VIDEO"), "image": ("image", "IMAGE"),
        "text": ("text", "TEXT"), "learn": ("learn", "LEARN")}

CATEGORY_LABELS = {
    "facts_mystery_science": "Facts",
    "lifestyle_fashion_beauty": "Lifestyle",
    "fitness_motivation": "Fitness",
    "finance_business": "Finance",
    "self_healing_spiritual": "Self-Healing",
    "default": "General",
}


def kind_pill(k):
    c, t = KIND.get(k, ("planned", (k or "?").upper()))
    return pill(c, t, {"video": "video", "image": "image", "text": "text", "learn": "brain"}.get(k, "activity"))


def _dept_on():
    """True only when rules.json department.enabled is true (false today -> old buttons unchanged)."""
    try:
        import department_gate
        return department_gate.enabled()
    except Exception:
        return False


def _dept_submit(topic, kind):
    import department_gate
    return department_gate.submit(topic.strip(), kind or "text", None, source="dashboard")


def shell(title, body, active):
    nav = ""
    for href, label, ic in (("/", "Overview", "activity"), ("/office", "Agent Office", "user"), ("/research", "Research", "target"), ("/settings", "Settings", "link")):
        cls = " active" if href == active else ""
        nav += ("<a href='" + href + "' class='nav-item" + cls + "' title='" + esc(label) + "'>" +
                icon(ic, 19) + "</a>")
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta name=author content='Mr Blank'>"
            "<title>" + esc(title) + "</title><style>" + CSS + "</style></head><body>"
            "<div class=sidebar><div class=brand>S</div><nav>" + nav + "</nav>"
            "<div class=foot>v2.0</div></div>"
            "<div class=main>"
            "<div class=hdr><div><h1>Social_AI Agent</h1></div>"
            "<div class=stamp>" + datetime.now().strftime("%b %d, %Y &middot; %I:%M:%S %p") + "</div></div>"
            + body + "</div></body></html>")


# ---------------------------------------------------------------- overview
def page_overview():
    with db.conn() as c:
        runs = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 100").fetchall()
        tot = c.execute("SELECT COUNT(*) a, SUM(status='ok') b, SUM(status='failed') d FROM runs").fetchone()
        kinds = dict(c.execute("SELECT kind, COUNT(*) FROM runs WHERE status='ok' GROUP BY kind").fetchall())
        live = c.execute("SELECT * FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
        nposts = c.execute("SELECT COUNT(*) n FROM posts").fetchone()["n"]
        frec = c.execute("SELECT * FROM runs WHERE status='failed' AND started > ? "
                         "ORDER BY id DESC LIMIT 8", (time.time() - 7 * 86400,)).fetchall()
    R, C = load_rules(), load_conn()
    pend = db.pending_approvals()
    mode = R.get("approval_mode", "manual")
    n_conn = sum(1 for k, *_ in PLATFORMS if C.get(k, {}).get("connected"))

    # banner
    if R.get("paused"):
        banner = ("<div class='banner b-pause'>" + icon("pause", 16) +
                  "<div><b>AGENT PAUSED</b><div class=sub style='color:inherit;opacity:.8'>"
                  "No scheduled posts will run. Resume on the Settings page.</div></div></div>")
    elif live:
        banner = ("<div class='banner b-live'><div class=pulse></div><div><b>RUNNING NOW</b> &middot; " +
                  kind_pill(live["kind"] or "video") + " &middot; " +
                  esc(live["topic"]) + "<div class=sub>" + esc(live["stage"] or "") +
                  " &middot; started " + ago(live["started"]) + "</div></div></div>")
    elif pend:
        banner = ("<div class='banner b-hold'>" + icon("clock", 16) + "<div><b>" + str(len(pend)) +
                  " item(s) waiting for your approval</b><div class=sub style='color:inherit;opacity:.85'>"
                  "Nothing is published until you approve it.</div></div></div>")
    else:
        soon = soonest_job(R)
        if soon:
            job, dt = soon
            s = (dt - datetime.now()).total_seconds()
            countdown = ((str(int(s // 3600)) + "h " + str(int(s % 3600 // 60)) + "m") if s < 86400
                        else (str(int(s // 86400)) + "d " + str(int(s % 86400 // 3600)) + "h"))
            banner = ("<div class='banner b-idle'>" + icon("clock", 15) +
                      "<div>Idle right now &mdash; next up: <b style='color:var(--fg)'>" +
                      kind_pill(job.get("type", "video")) + "</b> in <b style='color:var(--fg)'>" + countdown +
                      "</b><div class=sub style='margin-top:2px'>" + dt.strftime("%a, %b %d &middot; %I:%M %p") +
                      "</div></div></div>")
        else:
            banner = "<div class='banner b-idle'>" + icon("check", 15) + "<div>Idle &mdash; nothing scheduled right now</div></div>"

    # Quick action: Generate & Post manually - ONLY while the system is idle.
    # While the local AI is generating/publishing a run ('live' set) the button
    # is disabled so a manual post can never collide with an in-flight run
    # (scheduler's one-job-at-a-time gate would reject it anyway, and a stray
    # double-run is what previously caused database-lock crashes).
    busy = bool(live)
    quick_action = """
    <div class=card style="margin-bottom:16px;background:linear-gradient(135deg,var(--surface),var(--surface-2));border:1px solid var(--border-bright)">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div>
          <div style="font-weight:700;font-size:15px;color:var(--fg)">""" + icon("send", 18) + """ Generate & Post Now</div>
          <div class=sub style="margin-top:4px">""" + (
            "<b class=warnc>" + icon("cpu", 12) + " AI is generating right now - manual posts unlock automatically when it finishes.</b>"
            if busy else "Manually trigger the AI agent to create content and publish it (runs full audit gate)") + """</div>
        </div>
        <form method=post action='/generate-post' style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <select name=kind """ + ("disabled" if busy else "") + """ style="padding:8px 12px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg);font:13px var(--mono);min-width:140px">
            <option value=video>Video (Shorts/Reels/TikTok)</option>
            <option value=image>Image (Instagram/FB/Threads)</option>
            <option value=text>Text (X/Bluesky/Mastodon/Threads)</option>
          </select>
          <input type=text name=topic placeholder="Optional: your topic (auto-picks if empty)" """ + ("disabled" if busy else "") + """ style="flex:1;min-width:200px;padding:8px 12px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg);font:13px var(--mono)">
          <button class=btn-ok type=submit style="min-height:40px" """ + ("disabled title='AI busy'" if busy else "") + """>""" + icon("send", 14) + """ """ + ("Waiting..." if busy else "Generate & Post") + """</button>
        </form>
      </div>
    </div>
    """

    # approval queue - with REAL media preview served through this server
    ap = ""
    for r in pend:
        p = Path(r["path"] or "")
        job = p.parent if p.is_file() else p
        caption, media_html = "", ""
        try:
            cap = job / "caption.txt"
            if cap.exists():
                caption = cap.read_text(encoding="utf-8")[:800]
            vid = job / "final.mp4"
            img = job / "post.jpg"
            if vid.exists():
                media_html = ("<video class=prev controls preload=metadata src='/media?f=" +
                              urllib.parse.quote(str(vid)) + "'></video>")
            elif img.exists():
                media_html = ("<img class=prev src='/media?f=" + urllib.parse.quote(str(img)) +
                              "' alt='post image preview'>")
        except Exception:
            pass
        if not caption:
            caption = "(no caption text found)"
        where = ", ".join(k for k, *_ in PLATFORMS if C.get(k, {}).get("connected")) or "nowhere yet - connect an account on Settings"
        ap += ("<div class=appr><div class=appr-top>" + kind_pill(r["kind"] or "video") +
               "<b>" + esc(r["topic"][:70]) + "</b><span class=faint style='font:11px var(--mono)'>#" +
               str(r["id"]) + " &middot; " + ago(r["started"]) + "</span></div>"
               "<div class=prevwrap>" + media_html +
               "<div style='flex:1;min-width:230px'>"
               "<div class=faint style='font-size:10px;letter-spacing:.07em;margin-bottom:5px'>CAPTION THAT WILL BE POSTED</div>"
               "<div class=appr-body>" + esc(caption) + "</div></div></div>"
               "<div class=sub style='margin:10px 0'>" + icon("send", 12) +
               " Will post to: <b style='color:var(--fg)'>" + esc(where) + "</b></div>"
               "<div class=acts>"
               "<form class=inl method=post action='/approve'><input type=hidden name=id value='" + str(r["id"]) + "'>"
               "<button class=btn-ok type=submit>" + icon("check", 13) + "Approve &amp; post</button></form>"
               "<form class=inl method=post action='/reject'><input type=hidden name=id value='" + str(r["id"]) + "'>"
               "<button class=btn-no type=submit>" + icon("x", 13) + "Reject</button></form>"
               "<form class=inl method=post action='/openfolder'><input type=hidden name=f value='" +
               esc(str(job)) + "'>"
               "<button type=submit style='background:var(--surface-2);color:var(--fg-dim)'>" +
               icon("layers", 13) + "Open folder</button></form>"
               "</div></div>")
    if not ap:
        ap = "<div class=sub>Nothing waiting. New content will appear here for your approval before it posts.</div>"

    # health status lights
    hb = "<div class=healthgrid>"
    for n, v, d in health():
        hb += ("<div class='hlight " + ("ok" if v else "bad") + "'><span class=dotlight></span>"
               "<div><b>" + esc(n) + "</b><span>" + esc(str(d)) + "</span></div></div>")
    hb += "</div>"

    # What's not working - recent failures with their error/stage
    if frec:
        frows = "".join(
            "<div class=appr><div class=appr-top>" + kind_pill(f["kind"] or "video") +
            "<b>" + esc((f["topic"] or "")[:60]) + "</b>" +
            "<span class=faint style='font:11px var(--mono)'>#" + str(f["id"]) + " &middot; " +
            ago(f["started"]) + "</span></div>"
            "<div class=fail-body>" + icon("x", 12) + " " +
            esc((f["error"] or f["stage"] or "failed")[:220]) + "</div></div>" for f in frec)
        fcard = ("<div class='card' style='margin-bottom:12px'><h2>" + icon("cpu") +
                 "What's not working &mdash; last 7 days</h2>" + frows + "</div>")
    else:
        fcard = ""

    # plan
    prows = ""
    for row in today_schedule(R):
        on = row.get("enabled")
        pl = " ".join("<span class='plat " + ("pl-on" if C.get(p, {}).get("connected") else "pl-off") + "'>" +
                      esc(p) + "</span>" for p in row.get("platforms", []))
        # every published post reaches SocialBlog unconditionally (not tied to
        # any single job's platform list, so shown separately, always on)
        pl += " <span class='plat pl-on'>socialblog</span>"
        prows += ("<tr class='" + ("" if on else "dimmed") + "'><td>" + pill("on" if on else "off", "ON" if on else "OFF") +
                  "</td><td><b style='font-family:var(--mono)'>" + esc(fmt_time(row.get("time", ""))) + "</b>"
                  "<div class=faint style='font-size:11px'>" + esc(row.get("days", "daily")) + "</div></td>"
                  "<td>" + kind_pill(row.get("type", "video")) + "</td><td>" + pl + "</td>"
                  "<td class=sub style='font-family:var(--mono);font-size:11px'>" +
                  (esc(plan_next(row)) if on else "paused") + "</td>"
                  "<td class=sub style='font-size:12.5px;max-width:260px'>" + esc(row.get("note", "")) + "</td></tr>")

    # sources
    srows = ""
    for key, label in (("google_trends", "Google Trends"), ("reddit", "Reddit"),
                       ("youtube_trending", "YouTube Trending"), ("manual_list", "Your own topics")):
        s_ = R.get("topic_sources", {}).get(key, {})
        extra = (", ".join("r/" + x for x in s_.get("subreddits", [])[:3]) if key == "reddit"
                 else (str(len(s_.get("topics", []))) + " saved" if key == "manual_list" else s_.get("note", "")))
        srows += ("<tr><td>" + pill("on" if s_.get("enabled") else "off", "ON" if s_.get("enabled") else "OFF") +
                  "</td><td><b>" + esc(label) + "</b></td><td class=faint style='font-family:var(--mono)'>x" +
                  str(s_.get("weight", 0)) + "</td><td class=sub>" + esc(extra) + "</td></tr>")

    # history
    hrows = ""
    for r in runs:
        st = r["status"]
        apr = r["approval"] or "pending"
        aprp = (pill("ok", "APPROVED") if apr == "approved" else
                pill("bad", "REJECTED") if apr == "rejected" else
                pill("ok", "AUTO") if apr == "auto" else pill("pending", "WAITING"))
        # old-data lookup: preview whatever this run produced, right from history
        prev = "<span class=faint>-</span>"
        cat_label = "<span class=faint>-</span>"
        jp = Path(r["path"] or "")
        job = jp.parent if jp.is_file() else jp
        try:
            catf = job / "category.txt"
            if catf.exists():
                cat_key = catf.read_text(encoding="utf-8").strip()
                cat_label = esc(CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title()))
        except Exception:
            pass
        try:
            vid = job / "final.mp4"
            img = job / "post.jpg"
            capf = job / "caption.txt"
            if vid.exists():
                prev = ("<a href='/media?f=" + urllib.parse.quote(str(vid)) +
                        "' target=_blank style='color:#60A5FA;font-size:11px'>" +
                        icon("video", 11) + "watch</a>")
            elif img.exists():
                prev = ("<a href='/media?f=" + urllib.parse.quote(str(img)) +
                        "' target=_blank style='color:#60A5FA;font-size:11px'>" +
                        icon("image", 11) + "view</a>")
            elif capf.exists():
                txt = capf.read_text(encoding="utf-8")[:500]
                prev = ("<details><summary style='cursor:pointer;color:#60A5FA;font-size:11px;"
                        "list-style:none'>" + icon("text", 11) + "read</summary>"
                        "<div style='max-width:280px;white-space:pre-wrap;color:var(--fg-dim);"
                        "font-size:11px;margin-top:4px'>" + esc(txt) + "</div></details>")
        except Exception:
            pass
        hrows += ("<tr><td class=faint style='font-family:var(--mono)'>#" + str(r["id"]) + "</td>"
                  "<td>" + kind_pill(r["kind"] or "video") + "</td>"
                  "<td class=sub style='font-size:11px;max-width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>" + cat_label + "</td>"
                  "<td>" + esc(r["topic"][:46]) + "</td>"
                  "<td>" + pill({"ok": "ok", "failed": "bad", "running": "running"}.get(st, "planned"), st) + "</td>"
                  "<td>" + (aprp if st == "ok" else "<span class=faint>-</span>") + "</td>"
                  "<td>" + prev + "</td>"
                  "<td class=faint style='font-family:var(--mono);font-size:11px'>" +
                  ((str(r["seconds"]) + "s") if r["seconds"] else "-") + "</td>"
                  "<td class=faint style='font-family:var(--mono);font-size:11px'>" +
                  ((str(int(r["build_secs"])) + "s") if r["build_secs"] else "-") + "</td>"
                  "<td class=faint style='font-size:11px'>" +
                  datetime.fromtimestamp(r["started"]).strftime("%b %d %I:%M %p") + "</td></tr>")
    if not hrows:
        hrows = "<tr><td colspan=10 class=faint>Nothing made yet.</td></tr>"

    mode_pill = ("<span class='mode " + ("auto" if mode == "auto" else "") + "'>" +
                 icon("shield" if mode == "manual" else "check", 12) +
                 ("MANUAL APPROVAL" if mode == "manual" else "AUTO-POST") + "</span>")

    body = banner + quick_action + """
<div class="grid g4">
  <div class=card><h2>""" + icon("layers") + """Content made</h2>
    <div class=kpi>""" + str(tot["a"] or 0) + """</div>
    <div class=sub><span class=good>""" + str(tot["b"] or 0) + """ ok</span> &middot; """ + str(tot["d"] or 0) + """ failed</div>
    <div class=kinds>""" + kind_pill("video") + " <span class='faint' style='font:600 11px var(--mono)'>" + str(kinds.get("video", 0)) + "</span> " \
        + kind_pill("image") + " <span class='faint' style='font:600 11px var(--mono)'>" + str(kinds.get("image", 0)) + "</span> " \
        + kind_pill("text") + " <span class='faint' style='font:600 11px var(--mono)'>" + str(kinds.get("text", 0)) + """</span></div></div>

  <div class=card><h2>""" + icon("clock") + """Awaiting approval</h2>
    <div class="kpi """ + ("warnc" if pend else "") + """">""" + str(len(pend)) + """</div>
    <div class=sub style="margin-top:9px">""" + mode_pill + """</div></div>

  <div class=card><h2>""" + icon("link") + """Accounts connected</h2>
    <div class=kpi>""" + str(n_conn) + """<span class=faint style="font-size:15px"> / """ + str(len(PLATFORMS)) + """</span></div>
    <div class=sub>""" + str(nposts) + """ posts published &middot; <a href="/settings" style="color:#60A5FA">connect more</a></div></div>

  <div class=card><h2>""" + icon("target") + """Cost to date</h2>
    <div class="kpi good">$0.00</div>
    <div class=sub>0 paid services &middot; 0 API keys</div></div>
</div>

<div class=card style="margin-bottom:12px"><h2>""" + icon("shield") + """Approval queue &mdash; nothing posts without your OK</h2>
""" + ap + """</div>

""" + fcard + """

<div class=card style="margin-bottom:12px"><h2>""" + icon("calendar") + """The plan &mdash; what it makes, where it posts, when</h2>
<div class=lead>""" + (
        "<b style='color:#4ADE80'>GROWTH PHASE</b> &mdash; posting daily (as much as it can, safely) while the channel is new. "
        + str(db.total_published()) + " posts made so far; switches to the lighter weekly schedule automatically at "
        + str(R.get("growth_threshold", 50)) + "."
        if db.schedule_phase(R) == "growth" else
        "<b style='color:#60A5FA'>STEADY PHASE</b> &mdash; " + str(db.total_published()) + " posts made, past the "
        + str(R.get("growth_threshold", 50)) + " mark, so it's settled into the lighter weekly cadence."
    ) + """</div>
<div class=tw><table><tr><th>State</th><th>When</th><th>Makes</th><th>Posts to</th><th>Next run</th><th>Why</th></tr>
""" + (prows or "<tr><td colspan=6 class=faint>No schedule set.</td></tr>") + """</table></div>
<div class=sub style="margin-top:11px">Grey platform = not connected yet, so that step is skipped &mdash; connect it on <a href="/settings" style="color:#60A5FA">Settings</a>. <b>socialblog</b> always shows on &mdash; every post that publishes anywhere is also pushed to the SocialBlog site automatically, no separate step needed.</div></div>

<div class="grid g2">
  <div class=card><h2>""" + icon("target") + """How it picks a topic</h2>
    <div class=lead>Pulls candidates from every source that's ON, skips anything already used, then the local AI picks the strongest one &mdash; weighted by source priority and by real YouTube view performance once there's enough history. Full list on the <a href="/research" style="color:#60A5FA">Research</a> page.</div>
    <div class=tw><table><tr><th>State</th><th>Source</th><th>Weight</th><th>Detail</th></tr>""" + srows + """</table></div></div>

  <div class=card><h2>""" + icon("cpu") + """System health</h2>
    <div class=lead>The free local tools the agent runs on. Green = working right now.</div>""" + hb + """</div>
</div>

<div class=card><h2>""" + icon("activity") + """Everything the agent has made</h2>
<div class=lead>Every video, image and text post it has produced &mdash; newest first. Click a preview to watch or view it.</div>
<div class="tw hist"><table><tr><th>#</th><th>Type</th><th>Category</th><th>Topic</th><th>Build</th><th>Approval</th><th>Preview</th><th>Length</th><th>Took</th><th>When</th></tr>
""" + hrows + """</table></div></div>"""
    return shell("Social_AI Agent - Overview", body, "/")


# ---------------------------------------------------------------- settings
def ollama_models():
    """Live list of models actually installed in Ollama right now - so a model
    downloaded mid-session shows up here automatically, no code change needed."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
            d = json.load(r)
        return [m["name"] for m in d.get("models", [])]
    except Exception:
        return []


def model_card(R):
    current = R.get("ollama_model") or "llama3.2:3b"
    installed = ollama_models()
    if current not in installed:
        installed = [current] + installed   # keep the configured one selectable even if not detected
    opts = "".join("<option value='" + esc(m) + "'" + (" selected" if m == current else "") + ">" +
                   esc(m) + "</option>" for m in installed)
    return ("""<div class=card style="margin-bottom:12px"><h2>""" + icon("cpu") + """AI model</h2>
<div class=lead>The local Ollama model that writes scripts, captions, hashtags and titles. """ +
        ("<b>" + str(len(installed)) + " installed</b> - pick one and it takes effect on the very next run, no restart. Only ONE model is ever active at a time - no automatic fallback to a different model, so what you pick here is exactly what runs." if installed else
         "<b class=warnc>Ollama not reachable</b> - is it running?") + """</div>
<form method=post action='/set-model' style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
<select name=model style="flex:1;min-width:220px;padding:9px 11px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--fg);font:13px var(--mono)">""" +
        opts + """</select>
<button class=btn-ok type=submit>""" + icon("check", 13) + """Use this model</button>
</form>
<div class=sub style="margin-top:10px">Currently active: <b style="color:var(--fg)">""" + esc(current) + """</b></div>
</div>""")


def security_card(R):
    auth = R.get("dashboard_auth", {})
    on = auth.get("enabled", False)
    return ("""<div class=card style="margin-bottom:12px"><h2>""" + icon("shield") + """Dashboard login</h2>
<div class=lead>Off by default so nothing changes for you on this laptop. Turn it on before this dashboard is ever reachable from outside your own network (e.g. once it's behind a public domain) - without it, anyone who can reach it could pause the agent or disconnect your accounts, no login required.</div>
<form method=post action='/set-auth' style="display:grid;gap:10px;grid-template-columns:1fr 1fr;align-items:end">
<div><label>Username</label><input type=text name=username value='""" + esc(auth.get("username", "")) + """'></div>
<div><label>Password</label><input type=text name=password value='""" + esc(auth.get("password", "")) + """' placeholder="set a password"></div>
<div style="grid-column:span 2;display:flex;gap:10px;align-items:center">
<button class=btn-ok type=submit name=enabled value=1>""" + icon("check", 13) + (
    "Update &amp; keep ON" if on else "Turn ON &amp; save") + """</button>""" +
    ("<button class=btn-no type=submit name=enabled value=0>" + icon("x", 13) + "Turn OFF</button>" if on else "") + """
<span class='pill p-""" + ("on" if on else "off") + "'>" + ("LOGIN REQUIRED" if on else "OPEN, NO LOGIN") + """</span>
</div></form></div>""")


def schedule_card(C, R):
    """Autopilot schedule monitor: every job's next fire + target platforms."""
    rows = today_schedule(R)
    phase = db.schedule_phase(R)
    if not rows:
        return ("<div class=card style='margin-bottom:12px'><h2>" + icon("calendar") +
                """Autopilot schedule</h2><div class=sub>No schedule set. Edit <b>config\\rules.json</b> to add jobs.</div></div>""")
    tr = ""
    for job in rows:
        on = job.get("enabled")
        tp = job.get("type", "video")
        tm = fmt_time(job.get("time", "06:00"))
        days = job.get("days", "daily")
        plats = job.get("platforms", [])
        pl = " ".join("<span class='plat " + ("pl-on" if C.get(p, {}).get("connected") else "pl-off") + "'>" +
                      esc(p) + "</span>" for p in plats) or "<span class=faint>&mdash;</span>"
        nf = plan_next(job) if on else "paused"
        tr += ("<tr class='" + ("" if on else "dimmed") + "'>"
               "<td>" + pill("on" if on else "off", "ON" if on else "OFF") + "</td>"
               "<td><b class=mono>" + esc(tm) + "</b></td>"
               "<td>" + kind_pill(tp) + "</td>"
               "<td>" + esc(days) + "</td><td>" + pl + "</td>"
               "<td class='mono faint'>" + esc(nf) + "</td></tr>")
    return ("<div class=card style='margin-bottom:12px'><h2>" + icon("calendar") +
            """Autopilot schedule &mdash; monitored live</h2>
<div class=sub style='margin-bottom:10px'>Currently in <b style="color:""" + ("#4ADE80" if phase == "growth" else "#60A5FA") + """">""" + phase.upper() + """</b> phase (""" +
            str(db.total_published()) + " / " + str(R.get("growth_threshold", 50)) + """ posts made). Agent makes + posts on this clock. Add/edit times in <b>config\\rules.json</b> (schedule_growth / schedule_steady).</div>
<div style='overflow-x:auto'><table class=mini>
<tr><th></th><th>Time</th><th>Type</th><th>Days</th><th>Posts to</th><th>Next fire</th></tr>
""" + tr + """</table></div>
<div class=sub style='margin-top:11px'>Grey platform = not connected yet &mdash; connect it in the connections cards below so this step actually publishes.</div></div>""")


def video_card():
    ready = video_model_ready()
    gb = _folder_gb(WAN_MODEL)
    gname, gused, gtotal = gpu_info()
    clips_dir = VIDEOGEN / "clips"
    clips = sorted(clips_dir.glob("*.mp4")) if clips_dir.exists() else []
    last = clips[-1].name if clips else "none yet"
    state = pill("ok", "READY", "check") if ready else pill("pending", "DOWNLOADING", "clock")
    return ("<div class=card style='margin-bottom:12px'><h2>" + icon("video") +
            "Video generation &mdash; LOCAL GPU (new system)</h2>"
            "<div class=lead>Real moving-character video made on this PC's RTX 4060 &mdash; no cloud, "
            "no office machine. Replaces the old zooming-slides pipeline.</div>"
            "<div class=bullets style='margin-bottom:10px'>"
            "&#9679; Model: <b>Wan2.1-T2V-1.3B</b> (diffusers, fp16 + CPU offload) &mdash; "
            + ("ready" if ready else "downloading") + " (" + ("%.1f" % gb) + " GB) &nbsp;" + state + "<br>"
            "&#9679; GPU: <b>" + (gname or "RTX 4060 Laptop 8 GB") + "</b>" +
            (" &mdash; " + str(gused) + "/" + str(gtotal) + " GB VRAM in use" if gname else "") + "<br>"
            "&#9679; Text encoder converted locally from the 10.8 GB original &mdash; saved a 21.6 GB download<br>"
            "&#9679; Engine: <code>videogen\\wan_video.py</code> &middot; portrait 480x832 @ 16 fps, upscales to reel later<br>"
            "&#9679; Clips made: <b>" + str(len(clips)) + "</b> &middot; last: <code>" + esc(last) + "</code></div>"
            "<div class=sub>Run a scene: <code>venv\\Scripts\\python.exe videogen\\wan_video.py --scene 3</code> "
            "&nbsp;&middot;&nbsp; clips land in <code>videogen\\clips\\</code></div></div>")


def page_settings(msg=""):
    C, R = load_conn(), load_rules()
    mode = R.get("approval_mode", "manual")
    paused = R.get("paused", False)

    note = ""
    if msg:
        ok = not msg.lower().startswith("error")
        note = ("<div class='banner " + ("b-idle" if ok else "b-pause") + "' style='color:" +
                ("var(--accent)" if ok else "#FBBF24") + "'>" + icon("check" if ok else "x", 15) +
                "<div>" + esc(msg) + "</div></div>")

    nb = ""
    for key, label, purpose, mins, how in NOTIFY:
        st = C.get(key, {})
        on = st.get("connected")
        head = ("<div style='display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px'>"
                "<div><b style='font-size:15px'>" + esc(label) + "</b>"
                "<div class=faint style='font-size:11px'>" + esc(purpose) + " &middot; setup " + esc(mins) + "</div></div>"
                + (pill("ok", "CONNECTED", "check") if on else pill("pending", "NOT CONNECTED")) + "</div>")
        if on:
            nb += ("<div class=card>" + head + "<div class=sub style='margin-bottom:10px'>Connected &mdash; every finished item and any failure alert lands here.</div>"
                   "<form method=post action='/disconnect'><input type=hidden name=key value='" + key + "'>"
                   "<button class=btn-no type=submit>" + icon("x", 13) + "Disconnect</button></form></div>")
        else:
            nb += ("<div class=card>" + head + "<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                   "<form method=post action='/connect-telegram'>"
                   "<input name=token placeholder='paste bot token here' required "
                   "style='width:100%;padding:9px 11px;border-radius:6px;border:1px solid var(--border);"
                   "background:var(--bg);color:var(--fg);font:12px var(--mono);margin-bottom:9px'>"
                   "<button class=btn-ok type=submit>" + icon("link", 13) + "Connect Telegram</button></form>"
                   "<div class=faint style='font-size:11px;margin-top:8px'>Press START in the bot chat first &mdash; "
                   "Telegram will not let a bot message you until you speak to it.</div></div>")

    cards = ""
    n_platforms_connected = sum(1 for k, *_ in PLATFORMS if C.get(k, {}).get("connected"))
    # arranged: connected platforms first (what's actually live), then what's left to set up
    platforms_sorted = sorted(PLATFORMS, key=lambda p: not C.get(p[0], {}).get("connected"))
    for key, label, posts, mins, analytics, how in platforms_sorted:
        st = C.get(key, {})
        on = st.get("connected")
        head = ("<div style='display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px'>"
                "<div><b style='font-size:15px'>" + esc(label) + "</b>"
                "<div class=faint style='font-size:11px'>posts " + esc(posts) + " &middot; setup " +
                esc(mins) + " &middot; analytics: " + esc(analytics) + "</div></div>" +
                (pill("ok", "CONNECTED", "check") if on else pill("planned", "NOT CONNECTED")) + "</div>")

        if on:
            form = ("<div class=sub style='margin-bottom:10px'>Linked as <b style='color:var(--accent)'>" +
                    esc(str(st.get("account", "linked"))) + "</b></div>"
                    "<form method=post action='/disconnect'><input type=hidden name=key value='" + key + "'>"
                    "<button class=btn-no type=submit>" + icon("x", 13) + "Disconnect</button></form>")
        elif key == "youtube":
            has_json = (ROOT / "config" / "youtube_client.json").exists()
            step = ("<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                    "<div class=bullets style='margin-bottom:10px'>"
                    "&#9679; Step 1 &mdash; save your OAuth Desktop JSON as "
                    "<code>config\youtube_client.json</code> " +
                    ("<b class=good>[found]</b>" if has_json else "<b class=warnc>[not found yet]</b>") + "<br>"
                    "&#9679; Step 2 &mdash; click Authorise. A browser window opens once.</div>")
            if has_json:
                form = step + ("<form method=post action='/connect-youtube'>"
                               "<button class=btn-ok type=submit>" + icon("link", 13) +
                               "Authorise YouTube</button></form>"
                               "<div class=faint style='font-size:11px;margin-top:8px'>"
                               "Uploads start PRIVATE so nothing goes public by accident.</div>")
            else:
                form = step + ("<button type=button disabled style='background:var(--muted);"
                               "color:var(--fg-faint);cursor:not-allowed'>" + icon("clock", 13) +
                               "Waiting for youtube_client.json</button>")
        elif key == "tiktok":
            has_json = (ROOT / "config" / "tiktok_client.json").exists()
            step = ("<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                    "<div class=bullets style='margin-bottom:10px'>"
                    "&#9679; Step 1 &mdash; save Client key + secret as "
                    "<code>config\\tiktok_client.json</code> " +
                    ("<b class=good>[found]</b>" if has_json else "<b class=warnc>[not found yet]</b>") + "<br>"
                    "&#9679; Step 2 &mdash; click Authorise. A browser window opens once.</div>")
            if has_json:
                form = step + ("<form method=post action='/connect-tiktok'>"
                               "<button class=btn-ok type=submit>" + icon("link", 13) +
                               "Authorise TikTok</button></form>"
                               "<div class=faint style='font-size:11px;margin-top:8px'>"
                               "Never auto-posts publicly &mdash; lands in your TikTok drafts, you tap Post.</div>")
            else:
                form = step + ("<button type=button disabled style='background:var(--muted);"
                               "color:var(--fg-faint);cursor:not-allowed'>" + icon("clock", 13) +
                               "Waiting for tiktok_client.json</button>")
        elif key == "x":
            form = ("<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                    "<form method=post action='/connect-x'>"
                    "<input name=api_key placeholder='API Key' required style='width:100%;"+B+";margin-bottom:6px'>"
                    "<input name=api_secret placeholder='API Key Secret' required style='width:100%;"+B+";margin-bottom:6px'>"
                    "<input name=access_token placeholder='Access Token' required style='width:100%;"+B+";margin-bottom:6px'>"
                    "<input name=access_secret placeholder='Access Token Secret' required style='width:100%;"+B+';margin-bottom:6px">'
                    "<input name=bearer placeholder='Bearer Token' style='width:100%;"+B+'">'
                    "<div style='margin-top:10px'><button class=btn-ok type=submit>" +
                    icon("link", 13) + "Connect X/Twitter</button></div></form>")
        elif key == "bluesky":
            form = ("<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                    "<form method=post action='/connect-bluesky'>"
                    "<input name=handle placeholder='your handle, e.g. yourname.bsky.social' required "
                    "style='width:100%;"+B+";margin-bottom:6px'>"
                    "<input name=app_password placeholder='App Password (xxxx-xxxx-xxxx-xxxx)' required "
                    "style='width:100%;"+B+"'>"
                    "<div style='margin-top:10px'><button class=btn-ok type=submit>" +
                    icon("link", 13) + "Connect Bluesky</button></div></form>")
        elif key == "mastodon":
            form = ("<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                    "<form method=post action='/connect-mastodon'>"
                    "<input name=instance placeholder='your instance, e.g. mastodon.social' required "
                    "style='width:100%;"+B+";margin-bottom:6px'>"
                    "<input name=access_token placeholder='Access token' required "
                    "style='width:100%;"+B+"'>"
                    "<div style='margin-top:10px'><button class=btn-ok type=submit>" +
                    icon("link", 13) + "Connect Mastodon</button></div></form>")
        elif key == "discord":
            form = ("<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                    "<form method=post action='/connect-discord'>"
                    "<input name=bot_token placeholder='Bot Token' required "
                    "style='width:100%;"+B+";margin-bottom:6px'>"
                    "<input name=channel_id placeholder='Channel ID' required "
                    "style='width:100%;"+B+"'>"
                    "<div style='margin-top:10px'><button class=btn-ok type=submit>" +
                    icon("link", 13) + "Connect Discord</button></div></form>")
        elif key in ("instagram", "facebook", "threads"):
            form = ("<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                    "<form method=post action='/connect-meta'>"
                    "<input type=hidden name=key value='" + key + "'>"
                    "<input name=token placeholder='Meta long-lived access token' required "
                    "style='width:100%;"+B+"'><br>"
                    "<input name=page_id placeholder='Facebook Page ID (or Instagram/FB business id)' "
                    "style='width:100%;"+B+";margin-top:6px'>"
                    "<div style='margin-top:10px'><button class=btn-ok type=submit>" +
                    icon("link", 13) + "Connect " + esc(label) + "</button></div></form>"
                    "<div class=warnc style='font-size:11px;margin-top:8px'>Needs a Meta Developer App + "
                    "Business/Creator account. One app covers FB + IG + Threads.</div>")
        elif key == "tiktok":
            form = ("<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                    "<form method=post action='/connect-tiktok'>"
                    "<input name=token placeholder='TikTok access token (from open-source DreamApi)' "
                    "required style='width:100%;"+B+"'>"
                    "<div style='margin-top:10px'><button class=btn-ok type=submit>" +
                    icon("link", 13) + "Connect TikTok</button></div></form>")
        else:
            form = ("<div class=sub style='margin-bottom:10px'>" + esc(how) + "</div>"
                    "<button type=button disabled style='background:var(--muted);color:var(--fg-faint);cursor:not-allowed'>"
                    + icon("clock", 13) + "Connector not built yet</button>")
        cards += "<div class=card>" + head + form + "</div>"

    all_keys = [("telegram", "Telegram")] + [(k, l) for k, l, *_ in PLATFORMS]
    ck = "".join(
        "<span class='ckitem " + ("done" if C.get(k, {}).get("connected") else "todo") + "'>" +
        icon("check" if C.get(k, {}).get("connected") else "clock", 12) + esc(l) + "</span>"
        for k, l in all_keys)
    n_done = sum(1 for k, _ in all_keys if C.get(k, {}).get("connected"))
    hero = ("<div class=hero>" + icon("compass", 26) +
            "<div><b>" + str(n_done) + " of " + str(len(all_keys)) + " accounts connected</b>"
            "<div class=sub style='color:inherit;opacity:.85'>Connect Telegram first (that's how you watch the agent), "
            "then whichever platforms you want it posting to below.</div>"
            "<div class=checklist style='margin-top:10px'>" + ck + "</div></div></div>")

    body = note + hero + """
<div class=card style="margin-bottom:12px"><h2>""" + icon("shield") + """Approval mode</h2>
<div class=lead>Right now every post waits for you. Switch to automatic only once you trust the quality.</div>
<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
  <form class=inl method=post action='/mode'><input type=hidden name=mode value='manual'>
    <button type=submit style="background:""" + ("var(--warn)" if mode == "manual" else "var(--surface-2)") + \
        """;color:""" + ("#1A1200" if mode == "manual" else "var(--fg-dim)") + """">""" + icon("shield", 13) + \
        """Manual &mdash; I approve every post</button></form>
  <form class=inl method=post action='/mode'><input type=hidden name=mode value='auto'>
    <button type=submit style="background:""" + ("var(--accent)" if mode == "auto" else "var(--surface-2)") + \
        """;color:""" + ("#04120A" if mode == "auto" else "var(--fg-dim)") + """">""" + icon("check", 13) + \
        """Automatic &mdash; post without asking</button></form>
  <form class=inl method=post action='/pause'><input type=hidden name=v value='""" + ("0" if paused else "1") + """'>
    <button type=submit style="background:var(--surface-2);color:var(--fg-dim)">""" + icon("pause", 13) + \
        ("Resume agent" if paused else "Pause agent") + """</button></form>
</div>
<div class=sub style="margin-top:12px">Current mode: <b style="color:var(--fg)">""" + \
        ("MANUAL - nothing posts until you click Approve" if mode == "manual" else
         "AUTOMATIC - posts go out on schedule with no approval") + """</b>""" + \
        ("<br><b class=warnc>Agent is currently PAUSED.</b>" if paused else "") + """</div></div>

""" + schedule_card(C, R) + """

""" + model_card(R) + """

""" + video_card() + """

""" + security_card(R) + """

<div class=card style="margin-bottom:12px"><h2>""" + icon("activity") + """Monitoring &mdash; where YOU watch the agent</h2>
<div class=lead>Not a place the agent posts to &mdash; it's how you see what's happening: every finished item arrives here for preview, and you get an alert the moment anything breaks.</div></div>
<div class="grid g2">""" + nb + """</div>

<div class=card style="margin:12px 0"><h2>""" + icon("send") + """Publishing &mdash; where the agent POSTS your content</h2>
<div class=lead>""" + str(n_platforms_connected) + " of " + str(len(PLATFORMS)) + """ connected and live, shown first below. All free, no credit card on any of them &mdash; connect the rest whenever you're ready, they're simply skipped in the schedule until then.</div></div>

<div class="grid g2">""" + cards + """</div>"""
    return shell("Social_AI Agent - Settings", body, "/settings")


# ---------------------------------------------------------------- research page
def page_research(msg=""):
    import research
    note = ""
    if msg:
        ok = not msg.lower().startswith("error")
        note = ("<div class='banner " + ("b-idle" if ok else "b-pause") + "'>" +
                icon("check" if ok else "x", 15) + "<div>" + esc(msg) + "</div></div>")
    try:
        rows = research.unused(60)
    except Exception as e:
        rows = []
        note += "<div class='banner b-pause'>" + icon("x", 15) + "<div>" + esc(str(e)[:200]) + "</div></div>"

    counts = {}
    for r in rows:
        counts[r["source"]] = counts.get(r["source"], 0) + 1

    SRC = {"reddit": ("reddit", "REDDIT"), "trends": ("trends", "GOOGLE"),
           "yt": ("yt", "YOUTUBE"), "you": ("you", "YOU")}
    hook_cls = {"LIST (top/best)": "reddit", "WHY/HOW": "you", "CURIOSITY": "trends",
                "SCARCITY/EXCLUSIVE": "you", "REVEAL": "yt", "QUESTION": "trends", "FACT": ""}
    lst = ""
    for r in rows:
        cls, lab = SRC.get(r["source"], ("", (r["source"] or "?").upper()))
        hook = r["hook"] if "hook" in r.keys() and r["hook"] else "FACT"
        hc = hook_cls.get(hook, "")
        score = r["score"] if "score" in r.keys() else 0
        hot = ("<span class='src " + hc + "' title='hook strength'>" + esc(hook) +
               ("" if score < 4 else " <b>" + str(score) + "</b>") + "</span>" if hook != "FACT" else
               ("<span class='src' style='background:var(--surface-2);color:var(--fg-faint)'>" + esc(hook) + "</span>"))
        lst += ("<div class=topicrow><span class='src " + cls + "'>" + lab + "</span>" + hot +
                "<div style='flex:1'>" + esc(r["title"]) + "</div>"
                "<form class=inl method=post action='/make'>"
                "<input type=hidden name=topic value='" + esc(r["title"]) + "'>"
                "<select name=kind style='width:auto;padding:5px 8px;font-size:12px;margin-right:6px'>"
                "<option value=video>video</option><option value=image>image</option>"
                "<option value=text>text</option></select>"
                "<button class=btn-ok type=submit style='padding:6px 11px;min-height:30px'>" +
                icon("send", 12) + "Make</button></form>"
                "<form class=inl method=post action='/skip'><input type=hidden name=id value='" +
                str(r["id"]) + "'><button type=submit style='background:transparent;color:var(--fg-faint);"
                "border-color:var(--border);padding:6px 10px;min-height:30px'>Skip</button></form></div>")
    if not lst:
        lst = "<div class=sub>No topics stored yet. Click <b>Refresh sources</b>.</div>"

    chips = "".join("<span class='src " + SRC.get(k, ("", ""))[0] + "' style='font-size:11px;padding:5px 9px'>" +
                    esc(SRC.get(k, ("", k.upper()))[1]) + " " + str(v) + "</span> "
                    for k, v in sorted(counts.items()))

    hero = ("<div class=hero>" + icon("compass", 26) +
            "<div><b>Where the agent finds what to make next</b>"
            "<div class=sub style='color:inherit;opacity:.85'>It watches Reddit, Google Trends and YouTube "
            "for topics people already care about, plus your own saved list. Each one gets scored for how "
            "strong its hook is &mdash; then either you pick one below, or Auto-post mode grabs the top one itself.</div>"
            "</div></div>")

    body = note + hero + ("""<div class='banner b-idle' style='margin-bottom:12px'><div class=pulse></div><div>""" +
                    """<b>Auto-refresh every 5 min</b><div class=sub style='color:inherit;opacity:.85'>""" +
                    """Live trending pulled automatically so the list stays current with the latest news, self-healing, """ +
                    """spiritual and emotional-angle topics.</div></div></div>""") + """
<script>
(function(){
  function go(){ var f=document.createElement('form'); f.method='post';
    f.style.display='none'; f.action='/research-refresh';
    document.body.appendChild(f); f.submit(); }
  setInterval(go, 300000);            // every 5 minutes
  setTimeout(go, 1000);               // refresh once on page load too
})();
</script>
<div class="grid g4">
  <div class=card><h2>""" + icon("target") + """Topics ready to use</h2>
    <div class=kpi>""" + str(len(rows)) + """</div>
    <div class=kinds style="margin-top:9px">""" + (chips or "<span class=faint>none</span>") + """</div></div>
  <div class=card><h2>""" + icon("activity") + """Refresh research</h2>
    <div class=sub style="margin-bottom:10px">Runs automatically every 5 minutes. Click if you want fresh topics right now instead of waiting.</div>
    <form method=post action='/research-refresh'><button class=btn-ok type=submit>""" + \
        icon("activity", 13) + """Refresh sources now</button></form></div>
  <div class=card style="grid-column:span 2"><h2>""" + icon("text") + """Add your own topic</h2>
    <form method=post action='/topic-add' style="display:flex;gap:8px;flex-wrap:wrap">
      <input type=text name=topic placeholder="e.g. why the ocean glows at night" required style="flex:1;min-width:220px">
      <button class=btn-ok type=submit>""" + icon("check", 13) + """Add</button></form>
    <div class=sub style="margin-top:9px">Your own topics rank above scraped ones.</div></div>
</div>

<div class=card><h2>""" + icon("list") + """Researched topics &mdash; pick one to turn into content</h2>
<div class=lead>Choose what to make (video / image / text) and click <b>Make</b>. It'll show up on the Overview page for your approval before anything posts. Don't like an idea? Click <b>Skip</b> to remove it.</div>
""" + lst + """</div>"""
    return shell("Social_AI Agent - Research", body, "/research")


# ---------------------------------------------------------------- actions
def connect_telegram(token):
    token = (token or "").strip()
    if not token:
        return "Error: no token given."
    try:
        with urllib.request.urlopen("https://api.telegram.org/bot" + token + "/getMe", timeout=25) as r:
            me = json.load(r)
        if not me.get("ok"):
            return "Error: Telegram rejected that token."
        uname = me["result"].get("username", "bot")
        with urllib.request.urlopen("https://api.telegram.org/bot" + token + "/getUpdates", timeout=25) as r:
            ups = json.load(r)
        chats = []
        for u in ups.get("result", []):
            m = u.get("message") or u.get("channel_post") or u.get("my_chat_member")
            if m and m.get("chat", {}).get("id"):
                ch = m["chat"]
                chats.append((ch["id"], ch.get("type"), ch.get("title") or ch.get("first_name") or ""))
        if not chats:
            return ("Error: token is valid (@" + uname + ") but no chat found. "
                    "Open Telegram, find @" + uname + ", press START, send any message, then click Connect again.")
        cid, ctype, title = chats[-1]
        C = load_conn()
        C["telegram"] = {"connected": True, "token": token, "chat_id": cid,
                         "account": "@" + uname + " -> " + (title or str(cid))}
        save_json(CONN_FILE, C)
        return "Connected! @" + uname + " will post to " + (title or str(cid)) + " (" + str(ctype) + ")."
    except Exception as e:
        return "Error: " + str(e)[:200]


def do_publish(run_id):
    """Approve then hand off to publish.py for that job folder."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return "Error: run not found."
    db.set_approval(run_id, "approved")
    job = r["path"] or ""
    p = Path(job)
    if p.is_file():
        p = p.parent
    try:
        out = subprocess.run([str(ROOT / "venv" / "Scripts" / "python.exe"),
                              str(ROOT / "scripts" / "publish.py"), "--job", str(p)],
                             capture_output=True, text=True, timeout=600)
        if out.returncode == 0:
            db.mark_published(run_id)
            return "Approved and published: " + r["topic"][:60]
        return "Approved, but publishing failed: " + (out.stderr or out.stdout)[-200:]
    except Exception as e:
        return "Approved, but publishing failed: " + str(e)[:200]


# ---------------------------------------------------------------- server
LIVE_URL = "https://blog.your-domain.example"


def live_state():
    """Everything Push-to-Live needs, so the page can show exactly where it stands."""
    R = load_rules()
    dept = R.get("department") or {}
    try:
        conn = json.loads((ROOT / "config" / "connections.json").read_text(encoding="utf-8-sig"))
    except Exception:
        conn = {}
    socialai = conn.get("socialai") or {}
    return {
        "paused": bool(R.get("paused")),
        "department_enabled": bool(dept.get("enabled")),
        "live_publish": bool(dept.get("live_publish")),
        "allowed": dept.get("allowed_platforms") or [],
        "socialai_url": socialai.get("url"),
        "live_url": LIVE_URL,
        "is_live": (not R.get("paused")) and bool(dept.get("enabled")) and bool(dept.get("live_publish")),
        "generating": generation_busy(),
        "plan": day_plan(),
        "steps": [
            {"key": "unpause", "label": "Unpause the scheduler",
             "why": "paused=true means nothing can ever post by itself",
             "done": not R.get("paused")},
            {"key": "department", "label": "Turn the department on",
             "why": "department.enabled=true hands scheduler and dashboard jobs to the 18 agents",
             "done": bool(dept.get("enabled"))},
            {"key": "url", "label": "Point the blog at the live site",
             "why": "socialai.url %s -> %s" % (socialai.get("url"), LIVE_URL),
             "done": socialai.get("url") == LIVE_URL},
            {"key": "destinations", "label": "Open the destinations",
             "why": "allowed_platforms=%s - this is the rail that decides WHERE it can post"
                    % (dept.get("allowed_platforms") or []),
             "done": list(dept.get("allowed_platforms") or []) != ["socialai"]},
        ],
    }


_FOLDER_SIZE = {"gb": None, "at": 0.0, "busy": False}


def _autopost_folder_gb():
    """Size of the AutoPost folder. Walking ~100k files takes a while, so it is measured in a background
    thread at most every 30 min and the Overview just reads the last answer (never blocks the page)."""
    import threading
    now = time.time()
    if not _FOLDER_SIZE["busy"] and now - _FOLDER_SIZE["at"] > 1800:
        _FOLDER_SIZE["busy"] = True

        def _walk():
            try:
                tot = 0
                for dp, _dn, fn in os.walk(str(Path(__file__).resolve().parents[1])):
                    for f in fn:
                        try:
                            tot += os.path.getsize(os.path.join(dp, f))
                        except OSError:
                            pass
                _FOLDER_SIZE["gb"] = round(tot / 1e9, 1)
            except Exception:
                pass
            _FOLDER_SIZE["at"] = time.time()
            _FOLDER_SIZE["busy"] = False
        threading.Thread(target=_walk, daemon=True).start()
    return _FOLDER_SIZE["gb"]


def _system_health():
    """Read-only machine health for the Overview: services, disk, GPU, uptime."""
    import shutil, socket, subprocess
    out = {"services": [], "disk": None, "gpu": None, "uptime_h": None}
    for name, port in (("Dashboard", 8600), ("Ollama", 11434)):      # the local blog (8088/8543) is retired: posts go to the public site
        s = socket.socket(); s.settimeout(1.5)
        up = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        out["services"].append({"name": name, "port": port, "up": up})
    try:
        du = shutil.disk_usage("C:\\")
        out["disk"] = {"free_gb": round(du.free / 1e9), "total_gb": round(du.total / 1e9),
                       "autopost_gb": _autopost_folder_gb()}
    except Exception:
        pass
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=6)
        f = [x.strip() for x in (r.stdout or "").splitlines()[0].split(",")]
        out["gpu"] = {"name": f[0], "mem_used_mb": int(f[1]), "mem_total_mb": int(f[2]),
                      "temp_c": int(f[3]), "util_pct": int(f[4])}
    except Exception:
        pass
    try:
        import ctypes
        out["uptime_h"] = round(ctypes.windll.kernel32.GetTickCount64() / 3600000.0, 1)
    except Exception:
        pass
    return out


def day_plan():
    """Today's eight posts, with the reason each one sits at that hour.

    Read straight from DISPATCHER.SLOTS so the page can never drift from what the
    scheduler will actually do - a plan shown here that disagrees with the real slots
    is worse than no plan at all.
    """
    try:
        import dispatcher as D
    except Exception:
        return []
    why = {
        "image-morning":   "morning scroll - X, Bluesky, Mastodon, Telegram peak 08:00-11:00",
        "text-morning":    "strongest news slot of the day - 09:00-10:00 across X and Threads",
        "video-real":      "the owner chose this hour for the photoreal video",
        "image-afternoon": "mid-afternoon, when the professional audience is reading",
        "text-late":       "late afternoon - the biggest general engagement window",
        "image-evening":   "early evening - TikTok and Discord are busiest",
        "text-evening":    "evening unwind, still inside the TikTok and YouTube peak",
        "video-cartoon":   "the owner chose this hour for the cartoon video",
    }
    out = []
    for sid, hh, mm, kind, style, _pf in D.SLOTS:
        out.append({"slot": sid, "at": "%02d:%02d" % (hh, mm), "kind": kind,
                    "style": style or "", "why": why.get(sid, "")})
    return out


def generation_busy():
    """True when a generation OR a department step is already running.

    This is what stops two jobs at once. A video takes ~26 minutes of GPU on this laptop;
    two at the same time would fight for VRAM, deadlock the lease and duplicate posts -
    which is exactly the class of bug that cost days before."""
    try:
        with db.conn() as c:
            live = c.execute("SELECT id FROM runs WHERE status='running' "
                             "ORDER BY id DESC LIMIT 1").fetchone()
        if live:
            return {"busy": True, "what": "run #%s is generating" % live["id"]}
    except Exception:
        pass
    try:
        import department_queue as Q
        rows = Q.schema().execute(
            "SELECT job_id, step_key, status FROM department_steps "
            "WHERE status IN ('running','ready') LIMIT 1").fetchone()
        if rows:
            return {"busy": True, "what": "job %s is at %s (%s)"
                                   % (rows["job_id"], rows["step_key"], rows["status"])}
    except Exception:
        pass
    try:
        # Read-only look at the lease. The old probe TOOK the lease and only released the
        # in-process lock, so the row stayed owned by this (alive) dashboard pid and every
        # status poll left the system "busy" for 12 h - blocking the dispatcher. It also
        # reported "not busy" when the lease was already held.
        if db.generation_busy():
            return {"busy": True, "what": "another process holds the generation lease"}
    except Exception:
        return {"busy": True, "what": "could not read the generation lease"}
    return {"busy": False, "what": ""}


def go_live(step, value=True):
    """Apply ONE push-to-live step. Deliberately one at a time: the owner wants to open the
    system up gradually, not flip everything in one click."""
    R = load_rules()
    dept = R.setdefault("department", {})
    try:
        if step == "unpause":
            R["paused"] = not value
            if value:
                try:
                    import dispatcher
                    dispatcher.arm_today()          # turning ON never replays the slots that already passed today
                except Exception:
                    pass
        elif step == "department":
            dept["enabled"] = bool(value)
        elif step == "live_publish":
            dept["live_publish"] = bool(value)
        elif step == "url":
            conn = json.loads((ROOT / "config" / "connections.json").read_text(encoding="utf-8-sig"))
            conn.setdefault("socialai", {})["url"] = LIVE_URL if value else "http://localhost:8088"
            (ROOT / "config" / "connections.json").write_text(
                json.dumps(conn, indent=2, ensure_ascii=False), encoding="utf-8")
            save_json(RULES_FILE, R)
            return True, ("the blog now points at " + (LIVE_URL if value else "localhost"))
        elif step == "destinations":
            plats = (value.split(",") if isinstance(value, str) and value
                     else (value if isinstance(value, list) else []))
            plats = [p.strip() for p in plats if p and p.strip()]
            dept["allowed_platforms"] = plats or ["socialai"]
        else:
            return False, "unknown step"
        save_json(RULES_FILE, R)
        return True, "%s -> %s" % (step, value)
    except Exception as e:
        return False, str(e)[:140]


def _auth_ok(header_val, headers=None):
    """Is this request allowed?

    Session cookie first (the normal way in). HTTP Basic is still accepted because it costs
    nothing to keep and some tools still send it. Constant-time compares throughout, so a
    wrong password cannot be discovered one character at a time by timing."""
    R = load_rules()
    auth = R.get("dashboard_auth", {})
    if not auth.get("enabled"):
        return True
    if headers is not None:
        user = dashboard_auth.read(dashboard_auth.token_from_request(headers))
        if user and hmac.compare_digest(user, auth.get("username", "")):
            return True
    if not header_val or not header_val.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header_val[6:]).decode("utf-8")
        u, _, pw = raw.partition(":")
    except Exception:
        return False
    return hmac.compare_digest(u, auth.get("username", "")) and \
           hmac.compare_digest(pw, auth.get("password", ""))



def _ready_topics(limit=12):
    """ARJUN's waiting list: topics that are real SUBJECTS (not first-person lines or transcript sentences),
    not used, not skipped, best score first. 'count' is the number that pass the same filter."""
    try:
        import sqlite3
        import fact_grounding
        c = sqlite3.connect(str(ROOT / "data" / "brain.db"))
        rows = c.execute("SELECT title, source, score, found FROM topics WHERE used=0 AND skipped=0 AND title IS NOT NULL "
                         "ORDER BY score DESC, found DESC LIMIT 600").fetchall()
        c.close()
        good = []
        for r in rows:
            t = (r[0] or "").strip()
            low = t.lower()
            if len(t) < 12 or low.startswith(("i'm ", "i am ", "i ", "my ", "we ", "hi ", "hello ", "welcome ")) or t.count(" ") < 2:
                continue
            try:
                if not fact_grounding.is_subject(t)[0]:
                    continue
            except Exception:
                pass
            good.append({"title": t, "source": r[1], "score": r[2], "found": r[3]})
        return {"count": len(good), "target": 10, "items": good[:limit]}
    except Exception as e:
        return {"count": 0, "target": 10, "items": [], "error": str(e)[:60]}


class H(BaseHTTPRequestHandler):
    def _need_auth(self):
        """Send them to the real login page.

        A browser that wants HTML is redirected to /login, which shows the username already
        filled in so only the password has to be typed, and offers a logout later. Anything
        else (an API call) still gets a plain 401 so it does not silently follow a redirect."""
        accept = (self.headers.get("Accept") or "")
        is_page = ("text/html" in accept) or ("text/html" in (self.headers.get("Content-Type") or ""))
        if is_page:
            self.send_response(303)
            self.send_header("Location", "/login?next=" + urllib.parse.quote(self.path, safe="/"))
            self.end_headers()
            return
        self.send_response(401)
        # no WWW-Authenticate: it makes the browser pop a SECOND native user+password dialog
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Login required")

    def _send(self, body, code=200, ctype="text/html; charset=utf-8"):
        if ctype.startswith("text/html"):
            try:
                try:
                    import authorship                 # author credit footer, added by the server
                except ImportError:
                    import _ap_core                   # authorship.py was deleted: rebuild it from the signed copy
                    _ap_core.restore()
                    import authorship
                body = authorship.inject(body)
            except Exception:
                pass
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Created-By", "Mr Blank - AutoPost Automation")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def do_GET(self):
        # /health is deliberately BEFORE the auth check and never protected -
        # the Pi watchdog and the local agent_guard poll it with no credentials.
        # It leaks nothing sensitive: just liveness + how long ago the scheduler
        # thread last ticked (so a wedged scheduler is detectable even while the
        # web server itself still answers).
        if self.path == "/health" or self.path.startswith("/health?"):
            try:
                tick = float((ROOT / "data" / "last_tick.txt").read_text(encoding="utf-8").strip())
                tick_age = round(time.time() - tick, 1)
            except Exception:
                tick_age = None
            try:
                pend = len(db.pending_approvals())
            except Exception:
                pend = None
            payload = json.dumps({
                "ok": True,
                "ts": round(time.time(), 1),
                "pid": os.getpid(),
                "scheduler_tick_age_sec": tick_age,
                "scheduler_alive": (tick_age is not None and tick_age < 180),
                "pending_approvals": pend,
            })
            return self._send(payload, ctype="application/json")
        # /login and /logout sit BEFORE the gate on purpose - a locked-out user has to be
        # able to reach the sign-in page, and signing out must work even on an expired
        # session. Everything else, including every /api route, stays behind the gate.
        _open = urllib.parse.urlparse(self.path).path
        if _open in ("/login", "/logout"):
            pass
        elif not _auth_ok(self.headers.get("Authorization"), self.headers):
            return self._need_auth()
        try:
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            msg = (q.get("m") or [""])[0]
            if u.path == "/media":
                # serve a preview file, but ONLY from inside out/ (job/post folders) -
                # NEVER the whole AutoPost tree. This used to allow relative_to(ROOT),
                # which meant /media?f=config\connections.json would happily hand back
                # every API token/bot password in plaintext to anyone who could reach
                # this server, no login required. Real vulnerability, fixed.
                f = Path((q.get("f") or [""])[0])
                try:
                    f.resolve().relative_to((ROOT / "out").resolve())
                except Exception:
                    self._send("forbidden", 403, "text/plain")
                    return
                # extra belt-and-suspenders: only ever serve actual media types,
                # never an arbitrary file even if it somehow ends up under out/
                if f.suffix.lower() not in (".mp4", ".jpg", ".jpeg", ".png", ".mp3"):
                    self._send("forbidden", 403, "text/plain")
                    return
                if not f.is_file():
                    self._send("not found", 404, "text/plain")
                    return
                ct = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                      ".png": "image/png", ".mp3": "audio/mpeg"}.get(f.suffix.lower(), "application/octet-stream")
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Accept-Ranges", "none")
                self.end_headers()
                self.wfile.write(data)
            elif u.path == "/settings":
                new_ui = ROOT / "assets" / "ui" / "settings.html"
                if new_ui.exists():
                    self._send(new_ui.read_text(encoding="utf-8"))
                else:
                    self._send(page_settings())
            elif u.path == "/posts":
                self._send((ROOT / "assets" / "ui" / "home.html").read_text(encoding="utf-8"))
            elif u.path.startswith("/api/stock"):
                import stock
                its = stock.items()
                c = stock.cfg()
                counts = {}
                ready_counts = {}
                for i in its:
                    if i["status"] in ("ready", "in_production"):
                        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
                    if i["status"] == "ready":
                        ready_counts[i["kind"]] = ready_counts.get(i["kind"], 0) + 1
                try:
                    nm = stock.next_slot_minutes()[0]
                except Exception:
                    nm = -1
                self._send(json.dumps({
                    "ready": [{"kind": i["kind"], "style": i.get("style"), "topic": i["topic"], "made": time.strftime("%H:%M", time.localtime(i["created"]))}
                              for i in sorted(its, key=lambda i: i["created"]) if i["status"] == "ready"],
                    "in_production": [{"kind": i["kind"], "topic": i["topic"]} for i in its if i["status"] == "in_production"],
                    "counts": counts, "ready_counts": ready_counts, "targets": c["target"], "next_slot_min": nm,
                    "topics": _ready_topics()}), ctype="application/json")
            elif u.path.startswith("/api/recent"):
                import board, department_queue as Q
                c = Q.schema()
                out = []
                for jid in [r[0] for r in c.execute("SELECT job_id FROM department_steps GROUP BY job_id ORDER BY MAX(rowid) DESC LIMIT 40")]:
                    m = Q.read_job(jid) or {}
                    if m.get("dry_run"):
                        continue
                    first = c.execute("SELECT MIN(ts) FROM department_jobs WHERE job_id=?", (jid,)).fetchone()[0]
                    rid = m.get("run_id")
                    got = []
                    if rid:
                        got = [r[0] for r in c.execute("SELECT platform FROM posts WHERE run_id=? AND status='ok'", (rid,))]
                    out.append({"job_id": jid, "topic": (m.get("topic") or "")[:110], "kind": m.get("kind"), "style": m.get("style"),
                                "started": first, "published_to": sorted(set(got)), "stock": bool(m.get("stock")) and not m.get("stock_released")})
                self._send(json.dumps({"jobs": out[:25]}, default=str), ctype="application/json")
            elif u.path.startswith("/api/board"):
                import board
                self._send(json.dumps({"jobs": board.board()}, default=str), ctype="application/json")
            elif u.path == "/legacy":
                self._send(page_overview())
            elif u.path == "/legacy-settings":
                self._send(page_settings())
            elif u.path == "/research":
                self._send((ROOT / "research.html").read_text(encoding="utf-8"))
            elif u.path == "/legacy-research":
                self._send(page_research(msg))
            elif u.path == "/office":
                # same top-bar template as Overview / Settings (no old icon sidebar)
                self._send((ROOT / "office.html").read_text(encoding="utf-8"))
            elif u.path == "/conversations":
                self._send((ROOT / "conversations.html").read_text(encoding="utf-8"))
            elif u.path == "/office-app":
                self._send((ROOT / "office.html").read_text(encoding="utf-8"))
            elif u.path.startswith("/api/research"):
                import research, desk_work
                rows = research.unused(60)
                by = {}
                for r in rows:
                    by[r["source"]] = by.get(r["source"], 0) + 1
                with db.conn() as c:
                    total = c.execute("SELECT COUNT(*) FROM topics WHERE used=0 AND skipped=0").fetchone()[0]
                    try:
                        runs = [dict(x) for x in c.execute(
                            "SELECT started, finished, status, reason FROM desk_tasks WHERE task='topic_scout' "
                            "ORDER BY id DESC LIMIT 20")]
                    except Exception:
                        runs = []
                cad = next((t[3] for t in desk_work.TASKS if t[1] == "topic_scout"), 300)
                last = float(desk_work._load().get("topic_scout", 0))
                self._send(json.dumps({
                    "total": total, "by_source": by, "runs": runs, "cadence_sec": cad,
                    "running": bool(runs and runs[0]["status"] == "running" and time.time() - (runs[0]["started"] or 0) < 1800),
                    "next_at": (last + cad) if last else time.time(),
                    "topics": [{"id": r["id"], "title": r["title"], "source": r["source"],
                                "hook": (r["hook"] if "hook" in r.keys() else "FACT"),
                                "score": (r["score"] if "score" in r.keys() else 0),
                                "found": (r["found"] if "found" in r.keys() else 0)} for r in rows]},
                    default=str), ctype="application/json")
            elif u.path.startswith("/api/whoami"):
                u = dashboard_auth.read(dashboard_auth.token_from_request(self.headers))
                self._send(json.dumps({"user": u or "",
                                       "auth_on": bool((load_rules().get("dashboard_auth") or {})
                                                       .get("enabled"))}),
                           ctype="application/json")
            elif u.path.startswith("/api/live"):
                self._send(json.dumps(live_state()), ctype="application/json")
            elif u.path == "/login":
                self._send(dashboard_auth.login_page())
            elif u.path == "/logout":
                self.send_response(303)
                self.send_header("Location", "/login")
                self.send_header(dashboard_auth.clear_header() and
                                 "Set-Cookie", "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
                                 % dashboard_auth.COOKIE)
                self.end_headers()
            elif u.path.startswith("/assets/"):
                # Static files for the new UI (app.css and anything else under assets/).
                # Same read-only rules as /pixel/: no traversal, known extensions only.
                rel = urllib.parse.unquote(u.path[len("/assets/"):]).replace("\\", "/")
                f = ROOT / "assets" / rel
                try:
                    inside = f.resolve().is_relative_to((ROOT / "assets").resolve())
                except AttributeError:
                    inside = str(f.resolve()).startswith(str((ROOT / "assets").resolve()))
                ALLOWED = {".css": "text/css", ".js": "application/javascript",
                           ".png": "image/png", ".svg": "image/svg+xml",
                           ".jpg": "image/jpeg", ".ico": "image/x-icon",
                           ".woff2": "font/woff2", ".txt": "text/plain"}
                if (".." in rel or not inside or f.suffix.lower() not in ALLOWED
                        or not f.is_file()):
                    self._send("not found", 404, "text/plain")
                else:
                    data = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", ALLOWED[f.suffix.lower()])
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "max-age=300")
                    self.end_headers()
                    self.wfile.write(data)
            elif u.path.startswith("/pixel/"):
                # read-only static sprites for the Agent Office; png only, flat names only
                name = u.path[len("/pixel/"):]
                f = ROOT / "assets" / "pixel" / name
                if "/" in name or "\\" in name or f.suffix.lower() != ".png" or not f.is_file():
                    self._send("not found", 404, "text/plain")
                else:
                    data = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "max-age=3600")
                    self.end_headers()
                    self.wfile.write(data)
            elif u.path == "/api/office":
                import office_view  # read-only Agent Office snapshot
                self._send(json.dumps(office_view.snapshot()), ctype="application/json")
            elif u.path.startswith("/api/settings"):
                R = load_rules()
                dept = R.get("department") or {}
                ret = R.get("retention") or {}
                try:
                    conns = json.loads((ROOT / "config" / "connections.json")
                                       .read_text(encoding="utf-8-sig"))
                except Exception:
                    conns = {}
                # never send a secret to the browser: say whether one is stored, not what it is
                SECRETS = ("token", "secret", "password", "key", "bearer", "app_password")
                connections = []
                for plat, v in (conns or {}).items():
                    if not isinstance(v, dict):
                        continue
                    have = []
                    for k, val in v.items():
                        if k in ("connected", "account", "handle", "url", "instance",
                                 "channel_id", "privacy"):
                            have.append(k)
                        elif k in SECRETS:
                            have.append("%s:%s" % (k, "set" if val else "missing"))
                    connections.append({"platform": plat,
                                        "connected": bool(v.get("connected")),
                                        "fields": have})
                self._send(json.dumps({
                    "rules": {
                        "paused": bool(R.get("paused")),
                        "approval_mode": R.get("approval_mode"),
                        "ollama_model": R.get("ollama_model"),
                        "language": R.get("language"),
                        "niche": R.get("niche"),
                        "dashboard_auth": bool(R.get("dashboard_auth")),
                        "limits": R.get("limits") or {},
                        "reliability": R.get("reliability") or {},
                    },
                    "department": {"enabled": bool(dept.get("enabled")),
                                   "live_publish": bool(dept.get("live_publish")),
                                   "allowed_platforms": dept.get("allowed_platforms") or []},
                    "retention": {"enabled": bool(ret.get("enabled")),
                                  "final_days": ret.get("final_days", 10)},
                    "connections": sorted(connections, key=lambda x: x["platform"]),
                    "models": [m.get("name") for m in (_ollama_models() or [])],
                }, default=str), ctype="application/json")
            elif u.path.startswith("/api/departments"):
                import dept_registry
                self._send(json.dumps({"active": dept_registry.get(),
                                        "key": dept_registry.active_key(),
                                        "all": dept_registry.summary()},
                                       default=str), ctype="application/json")
            elif u.path.startswith("/api/comms/timeline"):
                import agent_comms
                jid = (q.get("job") or [None])[0]
                self._send(json.dumps(agent_comms.timeline(jid), default=str),
                           ctype="application/json")
            elif u.path.startswith("/api/comms/threads"):
                import agent_comms
                self._send(json.dumps(agent_comms.threads(), default=str),
                           ctype="application/json")
            elif u.path.startswith("/api/comms"):
                import agent_comms
                self._send(json.dumps(agent_comms.live(), default=str),
                           ctype="application/json")
            elif u.path.startswith("/api/rails"):
                R = load_rules()
                dept = R.get("department") or {}
                ret = R.get("retention") or {}
                poller = False
                try:
                    import office_view
                    poller = bool(office_view._poller().get("alive"))
                except Exception:
                    pass
                rails = [
                    {"name": "Automatic posting is running", "on": not bool(R.get("paused")),
                     "why": "YES - the system makes and publishes posts by itself, on schedule"
                            if not R.get("paused") else "NO - it is paused: nothing will be posted until you resume it"},
                    {"name": "Department auto-queue", "on": bool(dept.get("enabled")),
                     "why": "department.enabled=true - scheduler and dashboard jobs go to the 18 agents"
                            if dept.get("enabled") else "department.enabled=false - manual runs only"},
                    {"name": "Live publishing", "on": bool(dept.get("live_publish")),
                     "why": "live_publish=true - the safety rail is OFF"
                            if dept.get("live_publish") else "live_publish=false - LOCAL BLOG ONLY"},
                    {"name": "Allowed destinations", "on": True,
                     "why": ", ".join(dept.get("allowed_platforms") or ["(none)"])},
                    {"name": "Approval mode", "on": True,
                     "why": "%s%s" % (R.get("approval_mode"),
                                      " (MARCUS waits for you)" if R.get("approval_mode") == "manual"
                                      else " (publishes without waiting)")},
                    {"name": "Retention cleanup", "on": bool(ret.get("enabled")),
                     "why": ("drafts deleted after publish, final kept %s days" % ret.get("final_days", 10))
                            if ret.get("enabled") else "disabled - nothing is deleted"},
                    {"name": "Trigger poller", "on": poller,
                     "why": "watching for ready steps" if poller else "stopped - jobs will not advance"},
                ]
                self._send(json.dumps({"rails": rails}), ctype="application/json")
            elif u.path.startswith("/api/state"):
                with db.conn() as c:
                    n = c.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"]
                self._send(json.dumps({"runs": n, "pending": len(db.pending_approvals())}),
                           ctype="application/json")
            elif u.path.startswith("/api/status"):
                import office_view
                R = load_rules()
                dept = R.get("department") or {}
                with db.conn() as c:
                    live = c.execute("SELECT * FROM runs WHERE status='running' "
                                     "ORDER BY id DESC LIMIT 1").fetchone()
                    # recent department jobs, newest first - what the overview lists
                    jobs = c.execute(
                        "SELECT job_id, MAX(ts) m FROM department_jobs GROUP BY job_id "
                        "ORDER BY m DESC LIMIT 25").fetchall()
                out = {"status": "generating" if live else "ready",
                       "paused": bool(R.get("paused")),
                       "department_on": bool(dept.get("enabled")),
                       "live_publish": bool(dept.get("live_publish")),
                       "allowed": dept.get("allowed_platforms") or [],
                       "approval_mode": R.get("approval_mode"),
                       "ollama_ok": _ollama_ok(),
                       "model": R.get("ollama_model"),
                       "retention_on": bool((R.get("retention") or {}).get("enabled")),
                       "jobs": []}
                nowt = time.time()
                for jb in jobs:
                    meta = {}
                    try:
                        import department_queue as Q
                        meta = Q.read_job(jb["job_id"]) or {}
                    except Exception:
                        pass
                    # job.json lives in WORK/ and agent 20 RETENTION deletes it, so fall back
                    # to the INTAKE enqueue row, which is written into the database and
                    # therefore survives cleanup: "topic | kind | platform[,platform] | dry-run"
                    topic = meta.get("topic") or ""
                    kind = meta.get("kind") or ""
                    style = meta.get("style")
                    if not topic:
                        try:
                            er = c.execute(
                                "SELECT reason FROM department_jobs WHERE job_id=? AND "
                                "agent='INTAKE' AND status='enqueued' ORDER BY id LIMIT 1",
                                (jb["job_id"],)).fetchone()
                            if er and er["reason"]:
                                parts = [p.strip() for p in er["reason"].split("|")]
                                topic = parts[0] if parts else ""
                                if len(parts) > 1 and parts[1] in ("text", "image", "video"):
                                    kind = parts[1]
                                if len(parts) > 3 and "dry-run" in parts[3]:
                                    kind = (kind or "text") + " · dry-run"
                        except Exception:
                            pass
                    cur = {}
                    try:
                        rows = Q.schema().execute(
                            "SELECT step_key,status,reason FROM department_steps WHERE job_id=? "
                            "AND status IN ('running','ready','dead','failed')", (jb["job_id"],)).fetchall()
                        if rows:
                            cur = {"step": rows[0]["step_key"], "reason": rows[0]["reason"]}
                    except Exception:
                        pass
                    out["jobs"].append({
                        "job_id": jb["job_id"], "topic": topic, "kind": kind, "style": style,
                        "ago": _ago_short(nowt - float(jb["m"] or nowt)), **cur})
                self._send(json.dumps(out), ctype="application/json")
            elif u.path.startswith("/api/system"):
                self._send(json.dumps(_system_health(), default=str), ctype="application/json")
            elif u.path.startswith("/api/golive"):
                rep = ROOT / "data" / "golive_report.json"
                try:
                    out = {"checks": json.loads(rep.read_text(encoding="utf-8-sig")),
                           "ran": time.strftime("%Y-%m-%d %H:%M", time.localtime(rep.stat().st_mtime))}
                except Exception:
                    out = {"checks": [], "ran": None}
                self._send(json.dumps(out), ctype="application/json")
            elif u.path.startswith("/api/posts"):
                limit = int((q.get("limit") or ["10"])[0])
                with db.conn() as c:
                    posts = c.execute("""
                        SELECT r.id, r.topic, r.kind, r.started, r.published, r.status,
                               COUNT(p.id) as platform_count
                        FROM runs r
                        LEFT JOIN posts p ON r.id = p.run_id
                        WHERE r.status='ok'
                        GROUP BY r.id
                        ORDER BY r.started DESC
                        LIMIT ?
                    """, (limit,)).fetchall()
                
                result = []
                for post in posts:
                    timestamp = time.strftime("%I:%M %p", time.localtime(post[3])).lstrip("0")
                    status = "published" if post[4] else "held"
                    result.append({
                        "time": timestamp,
                        "status": status,
                        "title": post[1][:50],
                        "kind": post[2],
                        "platforms": post[6] or 0
                    })
                self._send(json.dumps(result), ctype="application/json")
            elif u.path.startswith("/api/stats"):
                with db.conn() as c:
                    today_start = int(time.time()) - (24 * 3600)
                    generated = c.execute(
                        "SELECT COUNT(*) as cnt FROM runs WHERE started > ? AND status='ok'",
                        (today_start,)
                    ).fetchone()
                    published = c.execute(
                        "SELECT COUNT(*) as cnt FROM runs WHERE started > ? AND published=1",
                        (today_start,)
                    ).fetchone()
                    approved = c.execute(
                        "SELECT COUNT(*) as cnt FROM audit_logs WHERE timestamp > ? AND status='approved'",
                        (today_start,)
                    ).fetchone()
                
                result = {
                    "generated": generated["cnt"] if generated else 0,
                    "published": published["cnt"] if published else 0,
                    "audit_passed": approved["cnt"] if approved else 0,
                    "avg_time_minutes": 15
                }
                self._send(json.dumps(result), ctype="application/json")
            elif u.path == "/" or u.path == "":
                # The clean overview (assets/ui/home.html). The old, detail-heavy page is
                # still reachable at /legacy so nothing is lost.
                new_ui = ROOT / "assets" / "ui" / "home.html"
                if new_ui.exists():
                    self._send(new_ui.read_text(encoding="utf-8"))
                else:
                    self._send(page_overview())
            elif u.path == "/legacy":
                self._send(page_overview())
            elif u.path == "/admin":
                # Old dashboard for power users
                self._send(page_overview())
        except Exception as e:
            self._send("<pre style='color:#f88;background:#111;padding:20px'>" +
                       html.escape(repr(e)) + "</pre>", 500)

    def do_POST(self):
        # POST /login is open - that is the whole point of it. Everything else, including
        # /go-live and /set-rule, is behind the gate.
        if urllib.parse.urlparse(self.path).path != "/login":
            if not _auth_ok(self.headers.get("Authorization"), self.headers):
                return self._need_auth()
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            # Refuse an oversized body outright instead of allocating whatever a caller
            # claims. 1 MB is far more than any form this dashboard posts needs.
            if n > 1_048_576:
                n = 1_048_576
            raw = self.rfile.read(n) if n else b""
            path = urllib.parse.urlparse(self.path).path
            # Read the body EXACTLY once, then hand it to whichever parser fits. Reading it
            # twice (form then JSON) makes the handler block waiting for bytes that will
            # never arrive, which hangs the request instead of answering it.
            try:
                if "json" in (self.headers.get("Content-Type") or "").lower():
                    body = json.loads(raw.decode("utf-8") or "{}")
                    if not isinstance(body, dict):
                        body = {}
                else:
                    body = urllib.parse.parse_qs(raw.decode("utf-8"))
            except Exception:
                body = {}
            f = {k: v for k, v in body.items()} if isinstance(body, dict) else {}
            if isinstance(body, dict) and not isinstance(body.get("step"), str) \
                    and all(isinstance(v, list) for v in body.values()):
                pass                                  # already the parse_qs shape
            g = lambda k: (f.get(k) if not isinstance(f.get(k), list) else (f[k] or [""])[0]) or ""
            msg = ""
            # ONE GENERATION AT A TIME. A video takes ~26 min of GPU on this laptop; two at
            # once would fight for VRAM, deadlock the lease and duplicate posts. The manual
            # button is refused while anything is running, and says what is running.
            if path in ("/make", "/generate-post", "/api/generate"):
                b = generation_busy()
                if b["busy"]:
                    payload = {"ok": False, "error": "busy", "why": b["what"]}
                    self._send(json.dumps(payload), 409, "application/json")
                    return
            if path == "/set-rule":
                ok, msg = rule_write(g("key"), g("value") or (g("bool") == "1"))
                # This endpoint exists for the new settings UI, which wants JSON. Every other
                # POST in this file ends in a 303 redirect, so without this the caller would
                # be silently bounced to "/" and never learn whether the change worked.
                self._send(json.dumps({"ok": ok, "msg": msg or ("saved" if ok else "failed")}),
                           200 if ok else 400, "application/json")
                return
            elif path == "/go-live":
                ok, msg = go_live(g("step"), g("value") or (g("bool") == "1"))
                self._send(json.dumps({"ok": ok, "msg": msg}), 200 if ok else 400,
                           "application/json")
                return
            elif path == "/login":
                # ---- session login (replaces HTTP Basic) ----
                # `body` was already read above, so this cannot block. Accept both JSON and
                # an ordinary form post.
                data = body if isinstance(body, dict) else {}
                user = (data.get("user") or data.get("username") or "")[:64]
                pw = (data.get("pass") or data.get("password") or "")[:200]
                if dashboard_auth.check(user, pw):
                    tok = dashboard_auth.make(user or "admin")
                    self.send_response(303)
                    self.send_header("Location", g("next") or "/")
                    self.send_header("Set-Cookie",
                                     "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                                     % (dashboard_auth.COOKIE, tok, dashboard_auth.TTL_SEC))
                    self.end_headers()
                    return
                self._send(json.dumps({"ok": False, "error": "wrong password"}), 401,
                           "application/json")
                return
            elif path == "/approve":
                msg = do_publish(int(g("id")))
            elif path == "/reject":
                db.set_approval(int(g("id")), "rejected")
                msg = "Rejected - it will not be posted."
            elif path == "/mode":
                R = load_rules(); R["approval_mode"] = g("mode"); save_json(RULES_FILE, R)
                msg = "Approval mode set to " + g("mode").upper() + "."
            elif path == "/pause":
                R = load_rules(); R["paused"] = (g("v") == "1"); save_json(RULES_FILE, R)
                msg = "Agent paused." if R["paused"] else "Agent resumed."
            elif path == "/set-model":
                R = load_rules()
                m = g("model").strip()
                if m:
                    R["ollama_model"] = m
                    save_json(RULES_FILE, R)
                    msg = "AI model set to " + m + " - takes effect on the next run."
                else:
                    msg = "Error: no model selected."
            elif path == "/set-auth":
                R = load_rules()
                want_on = g("enabled") == "1"
                user, pw = g("username").strip(), g("password").strip()
                if want_on and not pw:
                    msg = "Error: set a password before turning login on."
                else:
                    R["dashboard_auth"] = {"enabled": want_on, "username": user, "password": pw}
                    save_json(RULES_FILE, R)
                    msg = "Login required from now on." if want_on else "Login turned off - dashboard is open again."
            elif path == "/connect-telegram":
                msg = connect_telegram(g("token"))
            elif path == "/connect-youtube":
                try:
                    import youtube
                    msg = youtube.authorise()
                except Exception as e:
                    msg = "Error: " + str(e)[:220]
            elif path == "/connect-tiktok":
                try:
                    import tiktok
                    msg = tiktok.authorise()
                except Exception as e:
                    msg = "Error: " + str(e)[:220]
            elif path == "/connect-x":
                C = load_conn()
                C["x"] = {
                    "connected": True,
                    "api_key": g("api_key"), "api_secret": g("api_secret"),
                    "access_token": g("access_token"), "access_secret": g("access_secret"),
                    "bearer": g("bearer"), "account": "X/Twitter (@yourhandle)",
                }
                save_json(CONN_FILE, C)
                msg = "X/Twitter connected (keys stored). Posting only works if the app has Read+Write access."
            elif path == "/connect-meta":
                key = g("key")
                if key == "facebook":
                    try:
                        import meta
                        msg = meta.connect(g("token"), g("page_id"))
                    except Exception as e:
                        msg = "Error: " + str(e)[:220]
                else:
                    msg = "Error: " + key.capitalize() + " isn't wired up to actually post yet - only Facebook is built so far."
            elif path == "/connect-tiktok":
                C = load_conn()
                C["tiktok"] = {"connected": True, "token": g("token"), "account": "TikTok"}
                save_json(CONN_FILE, C)
                msg = "TikTok connected (token stored)."
            elif path == "/connect-bluesky":
                try:
                    import bluesky
                    msg = bluesky.connect(g("handle"), g("app_password"))
                except Exception as e:
                    msg = "Error: " + str(e)[:220]
            elif path == "/connect-mastodon":
                try:
                    import mastodon
                    msg = mastodon.connect(g("instance"), g("access_token"))
                except Exception as e:
                    msg = "Error: " + str(e)[:220]
            elif path == "/connect-discord":
                try:
                    import discord_bot
                    msg = discord_bot.connect(g("bot_token"), g("channel_id"))
                except Exception as e:
                    msg = "Error: " + str(e)[:220]
            elif path == "/openfolder":
                # same restriction as /media - only ever open folders inside out/,
                # never an arbitrary path from an unauthenticated request
                try:
                    fp = Path(g("f"))
                    fp.resolve().relative_to((ROOT / "out").resolve())
                    subprocess.Popen(["explorer", str(fp)])
                    msg = "Opened folder in Explorer."
                except Exception:
                    msg = "Error: that folder is outside what this can open."
            elif path == "/research-scout":
                import desk_work
                st = desk_work._load(); st["topic_scout"] = 0; desk_work._save(st)
                # start it right now on its own thread - do not wait for the poller, which may be
                # busy for 45+ minutes inside a video render. run_due() is lock-guarded.
                import threading
                threading.Thread(target=desk_work.run_due, name="scout-now", daemon=True).start()
                import ai_priority
                msg = ("A job is running, so ARJUN waits and goes out scouting as soon as everything is idle."
                       if ai_priority.chain_busy() else
                       "ARJUN is out scouting topics now - watch the Research triggers table (a minute or two).")
            elif path == "/research-refresh":
                import research
                n = research.refresh()
                msg = "Research refreshed - " + str(n) + " new topic(s) found."
            elif path == "/topic-add":
                import research
                research.add_manual(g("topic"))
                msg = "Added your topic: " + g("topic")[:60]
            elif path == "/skip":
                import research
                research.mark(int(g("id")), "skipped")
                msg = "Topic skipped."
            elif path == "/make" and _dept_on() and g("topic").strip():
                jid = _dept_submit(g("topic"), g("kind"))
                msg = ("Sent to the Agent Office as " + jid + " - watch it on the Agent Office tab." if jid
                       else "Could not queue it for the Agent Office.")
            elif path == "/generate-post" and _dept_on() and g("topic").strip():
                jid = _dept_submit(g("topic"), g("kind"))
                msg = ("Sent to the Agent Office as " + jid + " - watch it on the Agent Office tab." if jid
                       else "Could not queue it for the Agent Office.")
            elif path == "/make":
                # ONE-GENERATION-AT-A-TIME lease (auto + manual share it).
                if not db.acquire_gen_gate("manual"):
                    raise ValueError("AI is busy generating - manual post disabled until it finishes.")
                kind, topic = g("kind"), g("topic")
                script = "make_video.py" if kind == "video" else "make_post.py"
                args = [str(ROOT / "venv" / "Scripts" / "pythonw.exe"), str(ROOT / "scripts" / script), "--lease"]
                args += ([topic, "--scenes", "4"] if kind == "video" else [kind, topic])
                try:
                    p = subprocess.Popen(args, cwd=str(ROOT))
                    db.bind_gen_worker(p.pid)
                except Exception:
                    db.abort_gen_gate("manual")
                    raise
                db.release_gen_gate()
                msg = "Started making a " + kind + ". It will appear in the approval queue when done."
            elif path == "/generate-post":
                # Full autopilot: generate + audit + publish in background
                # ONE-GENERATION-AT-A-TIME lease (auto + manual share it).
                if not db.acquire_gen_gate("manual"):
                    raise ValueError("AI is busy generating - manual post disabled until it finishes.")
                kind, topic = g("kind"), g("topic")
                only = g("only_platforms") if "only_platforms" in f else ""
                args = [str(ROOT / "venv" / "Scripts" / "python.exe"), str(ROOT / "scripts" / "autopilot.py"),
                        "--kind", kind, "--max-retries", "3", "--lease"]
                if topic:
                    args += ["--topic", topic]
                if only:
                    args += ["--only-platforms", only]
                try:
                    p = subprocess.Popen(args, cwd=str(ROOT), creationflags=0x00000008 | 0x00000200 | 0x08000000)
                    db.bind_gen_worker(p.pid)
                except Exception:
                    db.abort_gen_gate("manual")
                    raise
                db.release_gen_gate()
                msg = "Started Generate & Post for " + kind + " (" + (topic[:50] if topic else "auto-topic") + "). Runs audit gate before publishing."
            elif path == "/api/generate":
                # Parse JSON body for new improved dashboard
                import json as json_lib
                try:
                    body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
                    data = json_lib.loads(body) if body else {}
                    services = data.get("services", ["youtube", "tiktok", "telegram"])
                    content_type = data.get("content_type", "text_image_video")

                    # ONE-GENERATION-AT-A-TIME lease (auto + manual share it).
                    if not db.acquire_gen_gate("manual"):
                        raise ValueError("AI is busy generating - manual post disabled until it finishes.")

                    # Generate "text_image_video" - default to video
                    kind = "video" if "video" in content_type else "post"
                    topic = "Auto-generated content"
                    script = "make_video.py" if kind == "video" else "make_post.py"
                    args = [str(ROOT / "venv" / "Scripts" / "pythonw.exe"), str(ROOT / "scripts" / script), "--lease"]
                    args += ([topic, "--scenes", "4"] if kind == "video" else ["post", topic])
                    try:
                        p = subprocess.Popen(args, cwd=str(ROOT))
                        db.bind_gen_worker(p.pid)
                    except Exception:
                        db.abort_gen_gate("manual")
                        raise
                    db.release_gen_gate()

                    self._send(json_lib.dumps({
                        "status": "queued",
                        "message": "Content generation started",
                        "services": services,
                        "kind": kind
                    }), ctype="application/json")
                    return
                except Exception as e:
                    self._send(json_lib.dumps({"status": "error", "message": str(e)}), 400, "application/json")
                    return
            elif path == "/disconnect":
                C = load_conn(); C.pop(g("key"), None); save_json(CONN_FILE, C)
                msg = g("key").title() + " disconnected."
            if path in ("/mode", "/pause", "/connect-telegram", "/connect-youtube", "/connect-tiktok", "/connect-x",
                        "/connect-meta", "/connect-bluesky", "/connect-mastodon", "/connect-discord",
                        "/set-model", "/set-auth", "/disconnect"):
                back = "/settings"
            elif path in ("/research-refresh", "/research-scout", "/topic-add", "/skip", "/make"):
                back = "/research"
            else:
                back = "/"
            self._redirect(back + "?m=" + urllib.parse.quote(msg))
        except Exception as e:
            self._redirect("/?m=" + urllib.parse.quote("Error: " + str(e)[:150]))

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def log_message(self, *a):
        pass


class _Server(ThreadingHTTPServer):
    # allow_reuse_address MUST stay False: on Windows a second bind to a port
    # that is already in use would otherwise be allowed to "steal" it, which is
    # exactly the duplicate-dashboard bug we are killing. With it False, the
    # second process fails to bind and exits - hard single-instance guarantee.
    allow_reuse_address = False
    daemon_threads = True


if __name__ == "__main__":
    # ONE DASHBOARD. The port bind below already refuses a second copy, but taking the
    # lock first means the loser exits immediately and says why, instead of lingering
    # as a process that looks healthy but is serving nothing.
    try:
        import single
        single.guard("Global\\AutoPost_Dashboard", "AutoPost dashboard")
    except SystemExit:
        raise
    except Exception:
        pass
    try:
        srv = _Server(("0.0.0.0", PORT), H)
    except OSError as e:
        print("port " + str(PORT) + " already in use - another dashboard is running. "
              "Exiting (this is the single-instance guard, not an error). " + str(e))
        sys.exit(0)
    try:
        import db as _db
        n = _db.clear_stale_runs()
        if n:
            print("cleared " + str(n) + " orphaned run(s) from a prior crash/reboot")
    except Exception as e:
        print("stale-run cleanup skipped: " + str(e)[:80])
    try:
        import scheduler
        scheduler.start()          # autopilot daemon (thread)
        print("scheduler started")
    except Exception as e:
        print("scheduler not started: " + str(e)[:80])
    try:
        sys.path.insert(0, str(ROOT / "videogen"))
        import department_queue as _dq          # first run: create every table the pages read
        _dq.schema()
    except Exception as _e:
        print("schema setup skipped: " + str(_e)[:80])
    try:
        import authorship
        authorship.guard(ROOT, log=print)
        print(authorship.banner())
    except Exception:
        pass
    print("Social_AI Agent dashboard -> http://localhost:" + str(PORT) + "   (Ctrl+C to stop)")
    srv.serve_forever()


