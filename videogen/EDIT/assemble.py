# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Assemble the 6 Wan clips into one 1080x1920 social video with:
  - voiceover (edge-tts, per the screenplay's lines)
  - the spoken line burned on screen as a subtitle, timed to the voice
  - title card (scene 5) + end card "Listen." (scene 6)
  - a soft deep hum bed (the screenplay's audio design)
Output: videogen/final/silence_real.mp4  (previous cut kept in final/_work/)
Runs offline except edge-tts. No cloud, no system changes.
"""
import json, subprocess, sys, shutil
from pathlib import Path

HERE = Path(__file__).parent          # videogen/EDIT  (where this code lives)
VG   = Path(__file__).parent.parent   # videogen/      (where all DATA lives)
CLIPS = VG / "clips"
WORK = VG / "final" / "_work"
OUT = VG / "final" / "silence_real.mp4"

FFDIR = Path(r"C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin")
FF = FFDIR / "ffmpeg.exe"
FP = FFDIR / "ffprobe.exe"
if not FF.exists():
    FF = Path(r"C:\Program Files\FFmpeg\bin\ffmpeg.exe")
    FP = Path(r"C:\Program Files\FFmpeg\bin\ffprobe.exe")
EDGE = (Path(__file__).resolve().parents[2] / "venv" / "Scripts" / "edge-tts.exe")
FONT = r"C:\Windows\Fonts\arialbd.ttf"
VOICE = "en-US-AriaNeural"
RATE = "+6%"          # brisk-but-calm read so each line fits its scene
VO_START = 0.35       # seconds into a scene before the voice begins

# spoken line per scene (from the screenplay). None = no line.
VO = {
    1: "My mind won't stop.",
    2: "In a world full of noise...",
    3: "...we offer advice. We try to fix.",
    4: "But silence heals the brain...",
    5: "...more than words ever can.",
    6: None,
}
# on-screen headline per scene
TITLE = {5: "THE POWER OF SILENCE", 6: "Listen."}


def run(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print("FFMPEG FAIL:", " ".join(str(a) for a in args)[:400], flush=True)
        print(r.stderr[-1500:], flush=True)
        sys.exit(1)
    return r


def probe_dur(p):
    r = subprocess.run([str(FP), "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def wrap(text, width=24):
    """Word-wrap so every subtitle line stays readable on a phone screen."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def drawtext(txtfile, size, y, extra=""):
    # relative paths + cwd=WORK avoid Windows path-escaping pain
    return (",drawtext=fontfile='font.ttf':textfile='%s':fontcolor=white:fontsize=%d:"
            "line_spacing=10:x=(w-text_w)/2:y=%s:box=1:boxcolor=black@0.68:boxborderw=18:"
            "shadowcolor=black@0.8:shadowx=3:shadowy=3%s" % (txtfile, size, y, extra))


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copy(FONT, WORK / "font.ttf")

    n_segs = 6
    # ---- pass 1: voice tracks ----
    vofiles = {}
    for n in range(1, n_segs + 1):
        if not VO.get(n):
            continue
        vo = WORK / ("vo%d.mp3" % n)
        r = subprocess.run([str(EDGE), "--voice", VOICE, "--rate", RATE,
                            "--text", VO[n], "--write-media", str(vo)],
                           capture_output=True, text=True)
        if r.returncode == 0 and vo.exists():
            vofiles[n] = (vo, probe_dur(vo))
            print("[vo %d] %.2fs  %s" % (n, vofiles[n][1], VO[n]), flush=True)
        else:
            print("[vo %d] edge-tts failed: %s" % (n, r.stderr[-200:]), flush=True)

    # ---- pass 2: normalise each clip, burn the script on screen ----
    segs = []
    for n in range(1, n_segs + 1):
        src = CLIPS / ("scene%d.mp4" % n)
        if not src.exists() or src.stat().st_size < 5000:
            sys.exit("missing clip %s - generate it first" % src)
        vf = "scale=1080:1920:flags=lanczos,fps=30,setsar=1"

        if n in TITLE:
            (WORK / ("title%d.txt" % n)).write_text(TITLE[n], encoding="utf-8")
            if n == 6:                                  # "Listen." - big, centred
                vf += drawtext("title6.txt", 92, "(h-text_h)/2")
            else:                                       # title - upper area
                vf += drawtext("title5.txt", 66, "h*0.18")

        if n in vofiles:
            vd = vofiles[n][1]
            (WORK / ("sub%d.txt" % n)).write_text(wrap(VO[n]), encoding="utf-8")
            # show the spoken line only while it is being spoken
            t0 = VO_START - 0.15
            t1 = VO_START + vd + 0.55
            vf += drawtext("sub%d.txt" % n, 62, "h*0.66",
                           ":enable='between(t,%.2f,%.2f)'" % (max(0.0, t0), t1))

        seg = WORK / ("seg%d.mp4" % n)
        run([str(FF), "-y", "-v", "error", "-i", str(src), "-vf", vf, "-an", str(seg)],
            cwd=str(WORK))
        d = probe_dur(seg)
        segs.append((n, seg, d))
        print("[seg %d] %.2fs" % (n, d), flush=True)

    # ---- pass 3: concat ----
    lst = WORK / "list.txt"
    lst.write_text("".join("file '%s'\n" % s.as_posix() for _, s, _ in segs), encoding="utf-8")
    base = WORK / "base.mp4"
    run([str(FF), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(base)])
    total = sum(d for _, _, d in segs)
    print("[concat] total %.2fs" % total, flush=True)

    if not vofiles:
        if OUT.exists():
            shutil.move(str(OUT), str(WORK / "prev_silence_real.mp4"))
        shutil.copy(base, OUT)
        print("WROTE", OUT, flush=True)
        return

    # ---- pass 4: mix voiceover + soft hum bed (matches the screenplay) ----
    starts = {}
    o = 0.0
    for n, seg, d in segs:
        starts[n] = o
        o += d

    vsorted = sorted(vofiles.items())
    inputs = []
    for n, (vo, vd) in vsorted:
        inputs += ["-i", str(vo)]
    hum_idx = 1 + len(vsorted)                     # 0 = video
    inputs += ["-f", "lavfi", "-t", "%.2f" % total, "-i", "sine=frequency=110:sample_rate=44100"]

    filters, mixin = [], []
    for i, (n, (vo, vd)) in enumerate(vsorted):
        delay = int(max(0.0, starts[n] + VO_START) * 1000)
        # voice lifted well above the bed so it is always clear
        filters.append("[%d:a]adelay=%d|%d,volume=1.55,aformat=sample_fmts=fltp[a%d]"
                       % (i + 1, delay, delay, i))
        mixin.append("[a%d]" % i)
    filters.append("[%d:a]volume=0.03,afade=t=in:d=1.5,afade=t=out:st=%.2f:d=2.0[hum]"
                   % (hum_idx, max(0.0, total - 2.0)))
    mixin.append("[hum]")
    # amix keeps voice + bed separate, then compress + loudness-normalise for phone speakers
    fc = (";".join(filters) + ";" + "".join(mixin) +
          "amix=inputs=%d:duration=longest:dropout_transition=0,"
          "acompressor=threshold=0.05:ratio=3:attack=8:release=200,"
          "loudnorm=I=-15:TP=-1.5:LRA=11[aout]" % len(mixin))
    print("[audio] " + fc, flush=True)

    if OUT.exists():
        shutil.move(str(OUT), str(WORK / "prev_silence_real.mp4"))
    run([str(FF), "-y", "-v", "error", "-i", str(base), *inputs, "-filter_complex", fc,
         "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-ar", "44100", "-shortest", str(OUT)])
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()
