# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Batch-generate the 2D-cartoon screenplay (screenplay_cartoon.json).

Pipeline loaded ONCE, clips generated one at a time (never two GPU jobs).
Output: videogen/clips_cartoon/sceneN.mp4
Usage:  python generate_cartoon.py [scene ...]   (default: all scenes)
"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from wan_video import load_pipe, generate  # noqa: E402

HERE = Path(__file__).parent          # code lives here
VG   = Path(__file__).parent.parent   # data lives in videogen/
SC = VG / "SCREENPLAYS" / "screenplay_cartoon.json"
OUT = HERE / "clips_cartoon"

DEFAULT_NEG = ("photorealistic, 3d render, realistic skin, live action, photograph, blurry, low quality, "
               "distorted, deformed, extra limbs, watermark, text, logo, static image")


def main():
    sc = json.loads(SC.read_text(encoding="utf-8"))
    want = [int(x) for x in sys.argv[1:]] or [s["n"] for s in sc["scenes"]]
    OUT.mkdir(parents=True, exist_ok=True)
    load_pipe()
    for s in sc["scenes"]:
        if s["n"] not in want:
            continue
        out = OUT / ("scene%d.mp4" % s["n"])
        if out.exists() and out.stat().st_size > 5000:
            print("[skip] %s exists" % out.name, flush=True)
            continue
        frames = max(17, ((round(s["seconds"] * 16) - 1) // 4) * 4 + 1)
        print("[cartoon %d] %ss -> %d frames" % (s["n"], s["seconds"], frames), flush=True)
        generate(s["t2v_prompt"], str(out), frames=frames, steps=25,
                 width=480, height=832, seed=100 + s["n"],
                 negative=s.get("negative") or DEFAULT_NEG)
        print("[cartoon %d] saved %s" % (s["n"], out), flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
