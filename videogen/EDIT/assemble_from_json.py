# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Assemble any screenplay JSON into a finished vertical social video.

Usage: python assemble_from_json.py --json screenplay_2minrule.json --clips clips_2minrule --out final/x.mp4

Reads from the JSON: narrator voice/rate, per-scene voice + screen text, title_card, cta.
Adds: one-line on-screen script (68 px), end title, CTA swap, unsharp after upscale,
narration + soft pad + two blips, compressor + loudness normalise (-15 LUFS).
Script & screenplay rule: this file never invents content - it only renders what the JSON says.
"""
import argparse, json, subprocess, sys, shutil
from pathlib import Path

HERE = Path(__file__).parent          # videogen/EDIT  (where this code lives)
VG   = Path(__file__).parent.parent   # videogen/      (where all DATA lives)
sys.path.insert(0, str(HERE))
from assemble import FF, FP, EDGE, FONT, WORK, run, probe_dur, wrap, drawtext  # noqa: E402

HOLD = 0.9
SCREEN_FONT = 68
SCREEN_WRAP = 24
UNSHARP = "unsharp=5:5:0.6:3:3:0.0"
BLIPS = {2: (1.2, 2.4, 3.6)}     # scene -> blip times (seconds from scene start)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--clips", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    SC = json.loads(Path(a.json).read_text(encoding="utf-8"))
    CLIPS = Path(a.clips)
    OUT = Path(a.out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    scenes = {s["n"]: s for s in SC["scenes"]}
    last = max(scenes)
    voice = (SC.get("narrator") or {}).get("voice", "en-US-JennyNeural")
    rate = (SC.get("narrator") or {}).get("rate", "+5%")
    tag = OUT.stem

    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copy(FONT, WORK / "font.ttf")

    # ---- pass 1: narration ----
    vos = {}
    for n in sorted(scenes):
        vo = WORK / ("%s_vo%d.mp3" % (tag, n))
        r = subprocess.run([str(EDGE), "--voice", voice, "--rate", rate,
                            "--text", scenes[n]["voice"], "--write-media", str(vo)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not vo.exists():
            print("[vo %d] edge-tts failed: %s" % (n, r.stderr[-200:]), flush=True)
            sys.exit(1)
        vos[n] = (vo, probe_dur(vo))
        print("[vo %d] %.2fs  %s" % (n, vos[n][1], scenes[n]["voice"][:70]), flush=True)

    # ---- pass 2: stretch + on-screen script ----
    segs = []
    for n in sorted(scenes):
        src = CLIPS / ("scene%d.mp4" % n)
        if not src.exists() or src.stat().st_size < 5000:
            sys.exit("missing clip %s - generate it first" % src)
        d0 = probe_dur(src)
        target = max(d0, vos[n][1] + HOLD)
        k = target / d0
        print("[scene %d] %.2fs -> %.2fs (x%.2f)" % (n, d0, target, k), flush=True)

        vf = "scale=1080:1920:flags=lanczos,%s,fps=30,setsar=1,setpts=%.5f*PTS" % (UNSHARP, k)
        (WORK / ("%s_s%d.txt" % (tag, n))).write_text(wrap(scenes[n]["screen"], SCREEN_WRAP), encoding="utf-8")
        if n == last and SC.get("cta"):
            cut = max(0.0, target - 3.0)
            vf += drawtext("%s_s%d.txt" % (tag, n), SCREEN_FONT, "h*0.66", ":enable='lt(t,%.2f)'" % cut)
            (WORK / ("%s_cta.txt" % tag)).write_text(SC["cta"], encoding="utf-8")
            vf += drawtext("%s_cta.txt" % tag, SCREEN_FONT, "h*0.66", ":enable='gte(t,%.2f)'" % cut)
        else:
            vf += drawtext("%s_s%d.txt" % (tag, n), SCREEN_FONT, "h*0.66")

        if n == last and SC.get("title_card"):
            (WORK / ("%s_title.txt" % tag)).write_text(wrap(SC["title_card"], 20), encoding="utf-8")
            vf += drawtext("%s_title.txt" % tag, 62, "h*0.14")

        seg = WORK / ("%s_seg%d.mp4" % (tag, n))
        run([str(FF), "-y", "-v", "error", "-i", str(src), "-vf", vf, "-an", str(seg)], cwd=str(WORK))
        d = probe_dur(seg)
        segs.append((n, seg, d))
        print("[seg %d] %.2fs" % (n, d), flush=True)

    # ---- pass 3: concat ----
    lst = WORK / ("%s_list.txt" % tag)
    lst.write_text("".join("file '%s'\n" % s.as_posix() for _, s, _ in segs), encoding="utf-8")
    base = WORK / ("%s_base.mp4" % tag)
    run([str(FF), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(base)])
    total = sum(d for _, _, d in segs)
    print("[concat] total %.2fs" % total, flush=True)

    # ---- pass 4: audio ----
    starts, o = {}, 0.0
    for n, seg, d in segs:
        starts[n] = o
        o += d
    inputs = []
    for n in sorted(scenes):
        inputs += ["-i", str(vos[n][0])]
    pad_idx = 1 + len(scenes)
    inputs += ["-f", "lavfi", "-t", "%.2f" % total, "-i", "sine=frequency=98:sample_rate=44100"]
    blips = [(n, t) for n, ts in BLIPS.items() if n in scenes for t in ts]
    blip_start = pad_idx + 1
    for _ in blips:
        inputs += ["-f", "lavfi", "-t", "0.14", "-i", "sine=frequency=1046:sample_rate=44100"]

    filters, mixin = [], []
    for i, n in enumerate(sorted(scenes)):
        delay = int(max(0.0, starts[n] + 0.25) * 1000)
        filters.append("[%d:a]adelay=%d|%d,volume=1.55,aformat=sample_fmts=fltp[a%d]"
                       % (i + 1, delay, delay, i))
        mixin.append("[a%d]" % i)
    filters.append("[%d:a]volume=0.022,afade=t=in:d=1.5,afade=t=out:st=%.2f:d=2.0[pad]"
                   % (pad_idx, max(0.0, total - 2.0)))
    mixin.append("[pad]")
    for j, (n, t) in enumerate(blips):
        ms = int(max(0.0, starts[n] + t) * 1000)
        filters.append("[%d:a]volume=0.07,afade=t=out:st=0.06:d=0.08,adelay=%d|%d[bl%d]"
                       % (blip_start + j, ms, ms, j))
        mixin.append("[bl%d]" % j)

    fc = (";".join(filters) + ";" + "".join(mixin) +
          "amix=inputs=%d:duration=longest:dropout_transition=0,"
          "acompressor=threshold=0.05:ratio=3:attack=8:release=200,"
          "loudnorm=I=-15:TP=-1.5:LRA=11[aout]" % len(mixin))
    run([str(FF), "-y", "-v", "error", "-i", str(base), *inputs, "-filter_complex", fc,
         "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-ar", "44100", "-shortest", str(OUT)])
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()
