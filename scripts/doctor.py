# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Readiness report. Shows what you have, what is missing, why, and exactly how to fix it.

    venv\\Scripts\\python.exe scripts\\doctor.py          (or double click DOCTOR.bat)

Every problem names its CAUSE, so you can see at once whether it is something to install,
something about your computer, your internet, or a step only you can do (like an account login).
"""
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# cause labels
NOT_INSTALLED = "not installed yet"
NOT_SET = "not set up yet"
COMPUTER = "your computer"
INTERNET = "internet"
YOU = "a step only you can do"

items = []          # name -> dict


def add(key, name, ok, detail="", why="", cause="", fix=""):
    items.append({"key": key, "name": name, "ok": ok, "detail": detail, "why": why, "cause": cause, "fix": fix})


def get(key):
    return next((i for i in items if i["key"] == key), None)


def reachable(url, timeout=6):
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AutoPostAutomation-check"}), timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True          # the server answered, so the internet works
    except Exception:
        return False


def gpu_info():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        if out:
            name, mem = [x.strip() for x in out[0].split(",")]
            return name, int(mem) / 1024.0
    except Exception:
        pass
    return None, 0.0


def collect():
    cores = os.cpu_count() or 0
    try:
        import psutil
        ram = psutil.virtual_memory().total / 1024 ** 3
    except Exception:
        ram = 0.0
    free = shutil.disk_usage(str(ROOT)).free / 1024 ** 3
    gname, gmem = gpu_info()
    spec = {"cores": cores, "ram": ram, "free": free, "gpu": gname, "gmem": gmem}

    # ---- the program itself
    v = sys.version_info
    add("python", "Python 3.11 or newer", v >= (3, 11), "found %d.%d.%d" % v[:3],
        "runs the whole program", NOT_INSTALLED, "run INSTALL.bat (it installs Python for you)")
    missing = []
    for mod, pipname in (("requests", "requests"), ("psutil", "psutil"), ("PIL", "Pillow"), ("cryptography", "cryptography"),
                         ("edge_tts", "edge-tts"), ("faster_whisper", "faster-whisper"), ("yt_dlp", "yt-dlp"),
                         ("googleapiclient", "google-api-python-client"), ("google_auth_oauthlib", "google-auth-oauthlib")):
        try:
            __import__(mod)
        except Exception:
            missing.append(pipname)
    add("packages", "Python packages", not missing, ("missing: " + ", ".join(missing)) if missing else "all installed",
        "libraries the program uses", NOT_INSTALLED,
        "run INSTALL.bat, or: venv\\Scripts\\python.exe -m pip install -r requirements.txt")

    ff = shutil.which("ffmpeg")
    if not ff:
        try:
            for l in (ROOT / "config" / "paths.toml").read_text(encoding="utf-8").splitlines():
                if l.strip().startswith("ffmpeg_bin"):
                    d = l.split("=", 1)[1].strip().strip('"')
                    if (Path(d) / "ffmpeg.exe").exists():
                        ff = str(Path(d) / "ffmpeg.exe")
        except Exception:
            pass
    add("ffmpeg", "FFmpeg", bool(ff), ff or "not found", "makes the audio and video files", NOT_INSTALLED,
        "run INSTALL.bat, or: winget install Gyan.FFmpeg")

    # ---- settings
    rules, rules_ok = {}, False
    try:
        rules = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig"))
        rules_ok = True
    except Exception:
        pass
    add("rules", "Settings file (config\\rules.json)", rules_ok, "valid" if rules_ok else "missing or broken",
        "holds your main settings", NOT_SET, "run INSTALL.bat, or restore config\\rules.json from the download")
    conn, conn_ok = {}, False
    try:
        conn = json.loads((ROOT / "config" / "connections.json").read_text(encoding="utf-8-sig"))
        conn_ok = True
    except Exception:
        pass
    add("connections", "Accounts file (config\\connections.json)", conn_ok, "exists" if conn_ok else "not created yet",
        "holds your account details, kept on this computer only", NOT_SET, "run INSTALL.bat")
    pw_default = (rules.get("dashboard_auth") or {}).get("password") == "change-me"
    add("password", "Dashboard password changed", rules_ok and not pw_default,
        "still change-me" if pw_default else "changed", "keeps strangers out of your dashboard", YOU,
        "open config\\rules.json, find dashboard_auth, write your own user name and password")

    # ---- text model
    model = rules.get("ollama_model", "")
    names, up = [], False
    try:
        tags = json.loads(urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4).read())
        names = [m["name"] for m in tags.get("models", [])]
        up = True
    except Exception:
        pass
    add("ollama_up", "Ollama is running", up, "answering on port 11434" if up else "no answer",
        "the program that runs the text model on your computer", NOT_INSTALLED if not shutil.which("ollama") and not up else NOT_SET,
        "open the Ollama app from the Start menu, or run INSTALL.bat")
    has_model = bool(model) and model in names
    add("model", "Text model installed (%s)" % (model or "none chosen"), has_model,
        "installed" if has_model else ("installed models: " + (", ".join(names) or "none")),
        "writes your posts", NOT_INSTALLED, "ollama pull %s   (or run INSTALL.bat)" % (model or "qwen2.5:7b"))
    answers = False
    detail = "not tested"
    if up and has_model:
        try:
            body = json.dumps({"model": model, "prompt": "Reply with the single word OK", "stream": False,
                               "options": {"num_predict": 8}}).encode()
            rq = urllib.request.Request("http://localhost:11434/api/generate", body, {"Content-Type": "application/json"})
            ans = json.loads(urllib.request.urlopen(rq, timeout=240).read()).get("response", "").strip()
            answers, detail = True, "it answered: '%s'" % ans[:20]
        except Exception as e:
            detail = "no answer in time (%s)" % str(e)[:50]
    add("model_answers", "Text model answers", answers, detail, "proves the model really works", COMPUTER,
        "the first answer can be slow. Try again. If it keeps failing the model may be too big for your memory: use a smaller one")

    # ---- internet
    net = reachable("https://commons.wikimedia.org")
    add("internet", "Internet connection", net, "reachable" if net else "cannot reach the internet",
        "research, pictures and voices come from the internet", INTERNET, "connect this computer to the internet")
    voice = net and reachable("https://speech.platform.bing.com")
    add("voice", "Voice service reachable", voice, "reachable" if voice else "cannot reach it",
        "spoken narration for video", INTERNET, "check your internet or firewall")

    # ---- video
    add("gpu", "NVIDIA graphics card, 8 GB or more", gmem >= 7.5,
        ("%s, %.0f GB" % (gname, gmem)) if gname else "no NVIDIA card found",
        "makes video on your own computer", COMPUTER,
        "local video needs this hardware. Text and image posts work without it")
    cuda = False
    tdet = "not installed"
    try:
        import torch
        cuda = bool(torch.cuda.is_available())
        tdet = "torch %s, CUDA %s" % (torch.__version__, "ready" if cuda else "not available")
    except Exception:
        pass
    add("torch", "PyTorch with CUDA", cuda, tdet, "runs the video model on the graphics card",
        NOT_INSTALLED if "not installed" in tdet else COMPUTER, "run INSTALL-VIDEO.bat")
    vm = (ROOT / "models" / "Wan2.1-T2V-1.3B-Diffusers").exists()
    add("videomodel", "Video model files", vm, "found" if vm else "not downloaded", "the model that draws the video",
        NOT_INSTALLED, "run INSTALL-VIDEO.bat")

    # ---- machine size
    add("ram", "Memory (RAM)", ram >= 8, "%.0f GB" % ram, "the text model needs room", COMPUTER,
        "8 GB is the minimum, 16 GB is comfortable. Use a smaller model if you have less")
    add("disk", "Free disk space", free >= 15, "%.0f GB free" % free, "models, posts and videos need space", COMPUTER,
        "free up space on this drive (15 GB minimum, 100 GB for local video)")

    # ---- accounts
    allowed = (rules.get("department") or {}).get("allowed_platforms") or []
    accounts = []
    for k, label in (("telegram", "Telegram"), ("telegram_channel", "Telegram channel"), ("bluesky", "Bluesky"),
                     ("discord", "Discord"), ("mastodon", "Mastodon"), ("youtube", "YouTube"), ("tiktok", "TikTok"),
                     ("x", "X"), ("facebook", "Facebook"), ("threads", "Threads"), ("linkedin", "LinkedIn"),
                     ("wordpress", "WordPress"), ("webhook", "Webhook"), ("socialai", "Your own blog")):
        c = (conn.get(k) or {}) if isinstance(conn, dict) else {}
        accounts.append((k, label, bool(c.get("connected")), k in allowed))
    return spec, accounts, rules


def has(key):
    i = get(key)
    return bool(i and i["ok"])


def report():
    spec, accounts, rules = collect()
    W = 74
    print("")
    print("=" * W)
    print(" AutoPost Automation 3.0  |  readiness report  |  created by Mr Blank")
    print("=" * W)
    print("")
    print(" YOUR COMPUTER")
    print("   %s, %d processor threads, %.0f GB memory, %.0f GB free disk" % (platform.system() + " " + platform.release(), spec["cores"], spec["ram"], spec["free"]))
    print("   graphics card: %s" % (("%s, %.0f GB" % (spec["gpu"], spec["gmem"])) if spec["gpu"] else "none found"))
    print("   reference computer this was built on: Windows 11, Intel Core i9-14900HX,")
    print("   32 GB memory, NVIDIA RTX 4060 Laptop 8 GB.")
    print("")
    print(" CHECKLIST")
    for i in items:
        tag = "[HAVE]   " if i["ok"] else "[MISSING]"
        print("   %s %s  (%s)" % (tag, i["name"], i["detail"]))
        if not i["ok"]:
            print("             why it matters : " + i["why"])
            print("             cause          : " + i["cause"])
            print("             how to fix     : " + i["fix"])
    print("")
    print(" WHAT YOU CAN DO RIGHT NOW")

    def verdict(label, need, extra=None):
        miss = [get(k) for k in need if not has(k)]
        if not miss:
            print("   [READY]      " + label)
            return True
        print("   [NOT READY]  " + label)
        for m in miss:
            print("                because: %s (%s). Fix: %s" % (m["name"], m["cause"], m["fix"]))
        return False

    t = verdict("Write text posts", ["python", "packages", "ollama_up", "model", "model_answers", "internet"])
    im = verdict("Make image posts", ["python", "packages", "ollama_up", "model", "model_answers", "internet"])
    vd = verdict("Make video on this computer", ["python", "packages", "ollama_up", "model_answers", "ffmpeg", "voice", "gpu", "torch", "videomodel"])
    on = [lab for k, lab, c, a in accounts if c and a]
    conn_only = [lab for k, lab, c, a in accounts if c and not a]
    notc = [lab for k, lab, c, a in accounts if not c]
    if on:
        print("   [READY]      Publish to: " + ", ".join(on))
    else:
        print("   [NOT READY]  Publish anywhere")
        if conn_only:
            print("                because: connected but switched off: %s (%s). Fix: CONNECT.bat on <name>" % (", ".join(conn_only), YOU))
        else:
            print("                because: no account connected yet (%s). Fix: run CONNECT.bat" % YOU)
    if conn_only and on:
        print("                connected but switched off: " + ", ".join(conn_only) + "  (CONNECT.bat on <name>)")
    if notc:
        print("   [NOT CONNECTED] " + ", ".join(notc) + "   (run CONNECT.bat to add)")
    paused = rules.get("paused", True)
    dept = (rules.get("department") or {}).get("enabled", False)
    if paused or not dept:
        print("   [OFF]        Automation is switched off. Posts are only made when you turn it on.")
        print("                (Dashboard, Settings, Live status, Turn automation on.) This is the safe default.")
    else:
        print("   [ON]         Automation is on.")
    print("")
    bad = [i for i in items if not i["ok"]]
    core_bad = [i for i in bad if i["key"] in ("python", "packages", "ollama_up", "model", "model_answers", "rules")]
    print("=" * W)
    print(" RESULT: %d of %d checks have what they need." % (len(items) - len(bad), len(items)))
    if core_bad:
        print(" NEXT: fix the first item marked [MISSING] above, then run DOCTOR.bat again.")
    elif not on:
        print(" NEXT: run CONNECT.bat to connect an account, then START.bat.")
    else:
        print(" NEXT: run START.bat and open http://localhost:8600")
    print(" Items about your computer or your internet are not faults of the program.")
    print("=" * W)
    return 1 if core_bad else 0


if __name__ == "__main__":
    sys.exit(report())
