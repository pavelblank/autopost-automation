# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Cartoon cut - DETAIL FIRST (no time cap), same 6 cartoon clips.

Priorities per the owner: good quality output + more script/content detail.
- detailed narration (edge-tts en-US-JennyNeural), as long as the story needs (~40 s)
- one short on-screen line per scene (68 px, ONE line), end-card title + CTA swap
- sharper output: 2.25x lanczos upscale + mild unsharp for crisp lines
- sound: narration + soft pad + notification blips in the scrolling scene
Output: videogen/final/cartoon_brain_detail.mp4
Offline except edge-tts. No cloud, no dashboard/system change.
"""
import subprocess, sys, shutil
from pathlib import Path

HERE = Path(__file__).parent          # code lives here
VG   = Path(__file__).parent.parent   # data lives in videogen/
sys.path.insert(0, str(HERE))
from assemble import FF, FP, EDGE, FONT, WORK, run, probe_dur, wrap, drawtext  # noqa: E402

CLIPS = VG / "clips_cartoon"
OUT = VG / "final" / "cartoon_brain_detail.mp4"
VOICE = "en-US-JennyNeural"
RATE = "+5%"
HOLD = 0.9                    # breathing room; total length follows the script, not a clock
SCREEN_FONT = 68
SCREEN_WRAP = 24              # keeps each screen line to ONE line at this size
UNSHARP = "unsharp=5:5:0.6:3:3:0.0"   # mild crispening after the 2.25x upscale

# scene: (detailed spoken narration, short on-screen line)
NARR = {
    1: ("Meet Milo. It's almost midnight. He has been scrolling for hours, and even though his eyes "
        "are burning, his thumb keeps moving.",
        "11:47 PM - still scrolling"),
    2: ("One more clip. One more post. Every swipe is a tiny burst of stimulation, and each one tells "
        "his brain the same thing: stay awake, something interesting might be next.",
        "Every swipe says: stay awake"),
    3: ("So his mind never gets the signal to slow down. More input means less rest - and the harder "
        "he tries to fall asleep, the faster his thoughts race.",
        "More input. Less rest."),
    4: ("Then he does one small thing. He turns the phone face down, and moves it out of arm's reach. "
        "No apps, no notifications - just distance.",
        "He puts the phone down."),
    5: ("The room goes quiet. His breathing slows. Little by little, the noise in his head begins to "
        "settle, and his body remembers how to rest.",
        "Quiet. Breathe. Slow."),
    6: ("Here's the point. Your brain isn't tired - it's overstimulated. Give it ten minutes offline, "
        "and it will thank you.",
        "Overstimulated, not tired."),
}
TITLE = "WHY YOUR BRAIN WON'T SWITCH OFF"
CTA = "TRY 10 MINUTES OFFLINE"
BLIPS = (1.2, 2.4, 3.6, 4.8)   # notification blips inside scene 2


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copy(FONT, WORK / "font.ttf")

    # ---- pass 1: narration ----
    vos = {}
    for n in sorted(NARR):
        vo = WORK / ("dvo%d.mp3" % n)
        r = subprocess.run([str(EDGE), "--voice", VOICE, "--rate", RATE,
                            "--text", NARR[n][0], "--write-media", str(vo)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not vo.exists():
            print("[vo %d] edge-tts failed: %s" % (n, r.stderr[-200:]), flush=True)
            sys.exit(1)
        vos[n] = (vo, probe_dur(vo))
        print("[vo %d] %.2fs  %s" % (n, vos[n][1], NARR[n][0][:70]), flush=True)

    # ---- pass 2: stretch clips, burn script line (+ title / CTA on scene 6) ----
    segs = []
    for n in sorted(NARR):
        src = CLIPS / ("scene%d.mp4" % n)
        if not src.exists() or src.stat().st_size < 5000:
            sys.exit("missing clip %s - generate it first" % src)
        d0 = probe_dur(src)
        target = max(d0, vos[n][1] + HOLD)
        k = target / d0
        print("[scene %d] %.2fs -> %.2fs (x%.2f)" % (n, d0, target, k), flush=True)

        vf = ("scale=1080:1920:flags=lanczos,%s,fps=30,setsar=1,setpts=%.5f*PTS" % (UNSHARP, k))
        (WORK / ("dscreen%d.txt" % n)).write_text(wrap(NARR[n][1], SCREEN_WRAP), encoding="utf-8")
        if n == 6:
            cut = max(0.0, target - 3.0)          # key line swaps to the call-to-action
            vf += drawtext("dscreen6.txt", SCREEN_FONT, "h*0.66", ":enable='lt(t,%.2f)'" % cut)
            (WORK / "dcta.txt").write_text(CTA, encoding="utf-8")
            vf += drawtext("dcta.txt", SCREEN_FONT, "h*0.66", ":enable='gte(t,%.2f)'" % cut)
        else:
            vf += drawtext("dscreen%d.txt" % n, SCREEN_FONT, "h*0.66")

        if n == 6:
            (WORK / "dtitle.txt").write_text(wrap(TITLE, 20), encoding="utf-8")
            vf += drawtext("dtitle.txt", 62, "h*0.14")

        seg = WORK / ("dseg%d.mp4" % n)
        run([str(FF), "-y", "-v", "error", "-i", str(src), "-vf", vf, "-an", str(seg)],
            cwd=str(WORK))
        d = probe_dur(seg)
        segs.append((n, seg, d))
        print("[seg %d] %.2fs" % (n, d), flush=True)

    # ---- pass 3: concat ----
    lst = WORK / "dlist.txt"
    lst.write_text("".join("file '%s'\n" % s.as_posix() for _, s, _ in segs), encoding="utf-8")
    base = WORK / "dbase.mp4"
    run([str(FF), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(base)])
    total = sum(d for _, _, d in segs)
    print("[concat] total %.2fs" % total, flush=True)

    # ---- pass 4: voice + pad + blips, compressed + normalised ----
    starts, o = {}, 0.0
    for n, seg, d in segs:
        starts[n] = o
        o += d

    inputs = []
    for n in sorted(NARR):
        inputs += ["-i", str(vos[n][0])]
    pad_idx = 1 + len(NARR)
    inputs += ["-f", "lavfi", "-t", "%.2f" % total, "-i", "sine=frequency=98:sample_rate=44100"]
    blip_start = pad_idx + 1
    for _ in BLIPS:
        inputs += ["-f", "lavfi", "-t", "0.14", "-i", "sine=frequency=1046:sample_rate=44100"]

    filters, mixin = [], []
    for i, n in enumerate(sorted(NARR)):
        delay = int(max(0.0, starts[n] + 0.25) * 1000)
        filters.append("[%d:a]adelay=%d|%d,volume=1.55,aformat=sample_fmts=fltp[a%d]"
                       % (i + 1, delay, delay, i))
        mixin.append("[a%d]" % i)
    filters.append("[%d:a]volume=0.022,afade=t=in:d=1.5,afade=t=out:st=%.2f:d=2.0[pad]"
                   % (pad_idx, max(0.0, total - 2.0)))
    mixin.append("[pad]")
    for j, t in enumerate(BLIPS):
        ms = int(max(0.0, starts[2] + t) * 1000)
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
