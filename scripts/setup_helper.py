# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Small helper used by INSTALL.bat. Keeps settings edits safe (real JSON, never text replace)."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config"


def say(t):
    print("   " + t)


def init_config(ffbin):
    CFG.mkdir(exist_ok=True)
    for src, dst in (("connections.example.json", "connections.json"), ("paths.example.toml", "paths.toml")):
        if not (CFG / dst).exists() and (CFG / src).exists():
            shutil.copy2(CFG / src, CFG / dst)
            say("created config\\" + dst)
        else:
            say("config\\" + dst + " already there, left as it is")
    if ffbin:
        p = CFG / "paths.toml"
        txt = p.read_text(encoding="utf-8") if p.exists() else ""
        line = 'ffmpeg_bin = "%s"' % ffbin.replace("\\", "/")
        out, done = [], False
        for l in txt.splitlines():
            if l.strip().startswith("ffmpeg_bin"):
                out.append(line)
                done = True
            else:
                out.append(l)
        if not done:
            out.insert(0, line)
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        say("FFmpeg folder saved in config\\paths.toml")


def pick_model(mem_gb, have_csv):
    mem = float(mem_gb)
    have = [h.strip() for h in have_csv.split(",") if h.strip()]
    if mem >= 28:
        order = ["qwen2.5:14b", "qwen3:14b", "ministral-3:14b", "qwen2.5:7b", "llama3.1:8b", "qwen2.5:3b"]
    elif mem >= 14:
        order = ["qwen2.5:7b", "llama3.1:8b", "qwen2.5:3b"]
    else:
        order = ["qwen2.5:3b", "llama3.2:3b"]
    for m in order:
        if m in have:
            print(m)
            return
    print(order[0])


def set_model(name):
    p = CFG / "rules.json"
    if not p.exists():
        say("rules.json not found")
        return
    r = json.loads(p.read_text(encoding="utf-8-sig"))
    r["ollama_model"] = name
    r["ollama_fallbacks"] = []
    p.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    say("text model set to " + name)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "init-config":
        init_config(a[1] if len(a) > 1 else "")
    elif a and a[0] == "pick-model":
        pick_model(a[1], a[2] if len(a) > 2 else "")
    elif a and a[0] == "set-model":
        set_model(a[1])
    else:
        print("usage: setup_helper.py init-config [ffmpeg_bin] | pick-model MEM_GB HAVE_CSV | set-model NAME")
