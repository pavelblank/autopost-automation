# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Second cut: THE POWER OF SILENCE with a NARRATOR explaining the script.

Same 6 Wan clips, but each scene is held/slowed just long enough for one
narrator line, the narration is spoken (edge-tts) and also shown on screen
so the viewer follows the story on a phone.

Output: videogen/final/silence_narrated.mp4   (never touches silence_real.mp4)
Runs offline except edge-tts. No cloud, no dashboard/system change.
"""
import subprocess, sys, shutil
from pathlib import Path

HERE = Path(__file__).parent          # code lives here
VG   = Path(__file__).parent.parent   # data lives in videogen/
sys.path.insert(0, str(HERE))
from assemble import (FF, FP, EDGE, FONT, CLIPS, WORK,  # noqa: E402
                      run, probe_dur, wrap, drawtext)

OUT = VG / "final" / "silence_narrated.mp4"
VOICE = "en-GB-RyanNeural"   # calm male narrator, distinct from the dialogue cut
RATE = "+8%"
HOLD = 0.85                  # lets a scene breathe after the line ends

# narrator line per scene - this is the script being explained
NARR = {
    1: "Meet Maya. She looks fine, but her mind never stops.",
    2: "Everyone has advice - friends, feeds, endless noise.",
    3: "But advice is just more noise on top.",
    4: "Silence is what actually heals the brain.",
    5: "In silence, her mind finally gets to rest.",
    6: "Silence isn't empty. It's where we heal. Listen.",
}
# headline shown on screen for a whole scene
TITLE = {5: "THE POWER OF SILENCE", 6: "Listen."}


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copy(FONT, WORK / "font.ttf")

    # ---- pass 1: narrator voice ----
    vos = {}
    for n in range(1, 7):
        vo = WORK / ("nvo%d.mp3" % n)
        r = subprocess.run([str(EDGE), "--voice", VOICE, "--rate", RATE,
                            "--text", NARR[n], "--write-media", str(vo)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not vo.exists():
            print("[vo %d] edge-tts failed: %s" % (n, r.stderr[-200:]), flush=True)
            sys.exit(1)
        vos[n] = (vo, probe_dur(vo))
        print("[vo %d] %.2fs  %s" % (n, vos[n][1], NARR[n]), flush=True)

    # ---- pass 2: stretch each clip to fit its line, burn the script + title ----
    segs = []
    for n in range(1, 7):
        src = CLIPS / ("scene%d.mp4" % n)
        if not src.exists() or src.stat().st_size < 5000:
            sys.exit("missing clip %s" % src)
        d0 = probe_dur(src)
        target = max(d0, vos[n][1] + HOLD)
        k = target / d0
        print("[scene %d] native %.2fs -> %.2fs (x%.2f slower)" % (n, d0, target, k), flush=True)

        vf = ("scale=1080:1920:flags=lanczos,fps=30,setsar=1,setpts=%.4f*PTS" % k)
        (WORK / ("ntxt%d.txt" % n)).write_text(wrap(NARR[n], 24), encoding="utf-8")
        vf += drawtext("ntxt%d.txt" % n, 60, "h*0.68")
        if n in TITLE:
            (WORK / ("ntitle%d.txt" % n)).write_text(TITLE[n], encoding="utf-8")
            if n == 6:
                vf += drawtext("ntitle6.txt", 92, "(h-text_h)/2")
            else:
                vf += drawtext("ntitle5.txt", 66, "h*0.18")

        seg = WORK / ("nseg%d.mp4" % n)
        run([str(FF), "-y", "-v", "error", "-i", str(src), "-vf", vf, "-an", str(seg)],
            cwd=str(WORK))
        d = probe_dur(seg)
        segs.append((n, seg, d, d0))
        print("[seg %d] %.2fs" % (n, d), flush=True)

    # ---- pass 3: concat ----
    lst = WORK / "nlist.txt"
    lst.write_text("".join("file '%s'\n" % s.as_posix() for _, s, _, _ in segs), encoding="utf-8")
    base = WORK / "nbase.mp4"
    run([str(FF), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(base)])
    total = sum(d for _, _, d, _ in segs)
    print("[concat] total %.2fs" % total, flush=True)

    # ---- pass 4: narration (full) + faint hum bed, compressed + loudness-normalised ----
    starts, o = {}, 0.0
    for n, seg, d, d0 in segs:
        starts[n] = o
        o += d
    inputs = []
    for n in range(1, 7):
        inputs += ["-i", str(vos[n][0])]
    hum_idx = 1 + 6
    inputs += ["-f", "lavfi", "-t", "%.2f" % total, "-i", "sine=frequency=96:sample_rate=44100"]

    filters, mixin = [], []
    for i, n in enumerate(range(1, 7)):
        delay = int(max(0.0, starts[n] + 0.30) * 1000)
        filters.append("[%d:a]adelay=%d|%d,volume=1.5,aformat=sample_fmts=fltp[a%d]"
                       % (i + 1, delay, delay, i))
        mixin.append("[a%d]" % i)
    filters.append("[%d:a]volume=0.022,afade=t=in:d=1.5,afade=t=out:st=%.2f:d=2.0[hum]"
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
