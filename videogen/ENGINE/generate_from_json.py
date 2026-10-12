# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Generate clips from any screenplay JSON (script-first rule).

Usage: python generate_from_json.py --json screenplay_x.json --out-dir clips_x [--steps 28] [scene ...]

- pipeline loaded ONCE, clips generated one at a time (never two GPU jobs)
- skips clips that already exist
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from wan_video import load_pipe, generate  # noqa: E402

DEFAULT_NEG = ("photorealistic, 3d render, realistic skin, live action, photograph, blurry, low quality, "
               "distorted, deformed, extra limbs, watermark, text, logo, static image")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--seed-base", type=int, default=200)
    ap.add_argument("scenes", nargs="*", type=int)
    a = ap.parse_args()

    sc = json.loads(Path(a.json).read_text(encoding="utf-8"))
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    want = a.scenes or [s["n"] for s in sc["scenes"]]

    print("[screenplay] %s | %s" % (sc.get("title"), sc.get("topic")), flush=True)
    load_pipe()
    for s in sc["scenes"]:
        if s["n"] not in want:
            continue
        out = out_dir / ("scene%d.mp4" % s["n"])
        if out.exists() and out.stat().st_size > 5000:
            print("[skip] %s exists" % out.name, flush=True)
            continue
        frames = max(17, ((round(s["seconds"] * 16) - 1) // 4) * 4 + 1)
        print("[scene %d] %s | %ss -> %d frames, %d steps" % (s["n"], s.get("title", ""), s["seconds"],
                                                              frames, a.steps), flush=True)
        generate(s["t2v_prompt"], str(out), frames=frames, steps=a.steps,
                 width=480, height=832, seed=a.seed_base + s["n"],
                 negative=s.get("negative") or DEFAULT_NEG)
        print("[scene %d] saved %s" % (s["n"], out), flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
