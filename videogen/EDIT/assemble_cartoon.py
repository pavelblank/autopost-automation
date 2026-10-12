# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Assemble the 2D-cartoon scenes into one narrated social video.

Reads screenplay_cartoon.json:
  - voice  = spoken narration (edge-tts)
  - screen = short line burned on screen for the whole scene, ONE line, bigger text
  - title  = end card (scene 6)
Output: videogen/final/cartoon_brain.mp4
Runs offline except edge-tts. No cloud, no system change.
"""
import json, subprocess, sys, shutil
from pathlib import Path

HERE = Path(__file__).parent          # code lives here
VG   = Path(__file__).parent.parent   # data lives in videogen/
sys.path.insert(0, str(HERE))
from assemble import FF, FP, EDGE, FONT, WORK, run, probe_dur, wrap, drawtext  # noqa: E402

SC = json.loads((VG / "SCREENPLAYS" / "screenplay_cartoon.json").read_text(encoding="utf-8"))
CLIPS = VG / "clips_cartoon"
OUT = VG / "final" / "cartoon_brain.mp4"
VOICE = "en-US-JennyNeural"   # warm, clear narrator
RATE = "+8%"
HOLD = 0.75

SCREEN_FONT = 68           # a bit bigger than before, readable on a phone
SCREEN_WRAP = 24           # keeps the line to ONE line at this size
TITLE = {6: "WHY YOUR BRAIN WON'T SWITCH OFF"}


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copy(FONT, WORK / "font.ttf")
    scenes = {s["n"]: s for s in SC["scenes"]}

    # ---- pass 1: narration ----
    vos = {}
    for n in sorted(scenes):
        vo = WORK / ("cvo%d.mp3" % n)
        r = subprocess.run([str(EDGE), "--voice", VOICE, "--rate", RATE,
                            "--text", scenes[n]["voice"], "--write-media", str(vo)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not vo.exists():
            print("[vo %d] edge-tts failed: %s" % (n, r.stderr[-200:]), flush=True)
            sys.exit(1)
        vos[n] = (vo, probe_dur(vo))
        print("[vo %d] %.2fs  %s" % (n, vos[n][1], scenes[n]["voice"]), flush=True)

    # ---- pass 2: stretch + burn screen text + title ----
    segs = []
    for n in sorted(scenes):
        src = CLIPS / ("scene%d.mp4" % n)
        if not src.exists() or src.stat().st_size < 5000:
            sys.exit("missing clip %s - generate it first" % src)
        d0 = probe_dur(src)
        target = max(d0, vos[n][1] + HOLD)
        k = target / d0
        print("[scene %d] %.2fs -> %.2fs (x%.2f)" % (n, d0, target, k), flush=True)

        vf = "scale=1080:1920:flags=lanczos,fps=30,setsar=1,setpts=%.5f*PTS" % k
        (WORK / ("cscreen%d.txt" % n)).write_text(wrap(scenes[n]["screen"], SCREEN_WRAP), encoding="utf-8")
        vf += drawtext("cscreen%d.txt" % n, SCREEN_FONT, "h*0.66")
        if n in TITLE:
            (WORK / ("ctitle%d.txt" % n)).write_text(wrap(TITLE[n], 20), encoding="utf-8")
            vf += drawtext("ctitle%d.txt" % n, 64, "h*0.16")

        seg = WORK / ("cseg%d.mp4" % n)
        run([str(FF), "-y", "-v", "error", "-i", str(src), "-vf", vf, "-an", str(seg)],
            cwd=str(WORK))
        d = probe_dur(seg)
        segs.append((n, seg, d))
        print("[seg %d] %.2fs" % (n, d), flush=True)

    # ---- pass 3: concat ----
    lst = WORK / "clist.txt"
    lst.write_text("".join("file '%s'\n" % s.as_posix() for _, s, _ in segs), encoding="utf-8")
    base = WORK / "cbase.mp4"
    run([str(FF), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(base)])
    total = sum(d for _, _, d in segs)
    print("[concat] total %.2fs" % total, flush=True)

    # ---- pass 4: narration + light music-less bed, compressed + normalised ----
    starts, o = {}, 0.0
    for n, seg, d in segs:
        starts[n] = o
        o += d
    inputs = []
    for n in sorted(scenes):
        inputs += ["-i", str(vos[n][0])]
    hum_idx = 1 + len(scenes)
    inputs += ["-f", "lavfi", "-t", "%.2f" % total, "-i", "sine=frequency=104:sample_rate=44100"]

    filters, mixin = [], []
    for i, n in enumerate(sorted(scenes)):
        delay = int(max(0.0, starts[n] + 0.30) * 1000)
        filters.append("[%d:a]adelay=%d|%d,volume=1.5,aformat=sample_fmts=fltp[a%d]"
                       % (i + 1, delay, delay, i))
        mixin.append("[a%d]" % i)
    filters.append("[%d:a]volume=0.02,afade=t=in:d=1.5,afade=t=out:st=%.2f:d=2.0[hum]"
                   % (hum_idx, max(0.0, total - 2.0)))
    mixin.append("[hum]")
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
