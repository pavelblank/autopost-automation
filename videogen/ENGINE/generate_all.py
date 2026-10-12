# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Batch-generate all screenplay scenes with the pipeline loaded ONCE.
Skips clips that already exist. Logs progress per scene.
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from wan_video import load_pipe, generate, SCREENPLAY, OUTDIR  # noqa: E402


def main():
    sc = json.loads(SCREENPLAY.read_text(encoding="utf-8"))
    order = [int(x) for x in sys.argv[1:]] or [1, 2, 4, 5, 6]
    t0 = time.time()
    load_pipe()  # warm up once
    done = []
    for s in sc["scenes"]:
        if s["n"] not in order:
            continue
        out = OUTDIR / ("scene%d.mp4" % s["n"])
        if out.exists() and out.stat().st_size > 100000:
            print("[skip] %s exists" % out.name, flush=True)
            done.append(out)
            continue
        frames = max(17, ((round(s["seconds"] * 16) - 1) // 4) * 4 + 1)
        print("[scene %d] %ss -> %d frames" % (s["n"], s["seconds"], frames), flush=True)
        generate(s["t2v_prompt"], str(out), frames=frames, steps=25,
                 width=480, height=832, seed=42 + s["n"])
        done.append(out)
        print("[elapsed] %.0f min total" % ((time.time() - t0) / 60), flush=True)
    print("ALL DONE:", ", ".join(p.name for p in done), flush=True)


if __name__ == "__main__":
    main()
