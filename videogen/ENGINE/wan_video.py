# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Local GPU video engine — Wan2.1-T2V-1.3B on RTX 4060 (diffusers, cpu-offload).

Usage:
  python wan_video.py --prompt "scene description" --out clip.mp4
  python wan_video.py --scene 3                # uses screenplay scenes
  python wan_video.py --list                   # list screenplay scenes
Options: --frames N (default 81 = 5s@16fps, use 49=3s, 65=4s, 17=1s)
         --steps (25), --width 480 --height 832 (portrait reel), --seed, --negative "..."
Output: mp4 at 16 fps.
"""
import argparse, json, sys, time
from pathlib import Path

MODEL = Path(__file__).resolve().parents[2] / "models" / "Wan2.1-T2V-1.3B-Diffusers"
SCREENPLAY = Path(__file__).parent / "screenplay_silence.json"
OUTDIR = Path(__file__).parent / "clips"

_pipe = None


def load_pipe():
    global _pipe
    if _pipe is not None:
        return _pipe
    import torch
    from diffusers import WanPipeline

    t0 = time.time()
    print(f"[engine] loading Wan2.1 from {MODEL} ...", flush=True)
    pipe = WanPipeline.from_pretrained(str(MODEL), dtype=torch.float16)
    pipe.enable_model_cpu_offload()
    try:
        pipe.enable_vae_tiling()
    except Exception:
        pass
    pipe.set_progress_bar_config(disable=False)
    _pipe = pipe
    print(f"[engine] loaded in {time.time()-t0:.0f}s (fp16, cpu-offload)", flush=True)
    return pipe


def generate(prompt, out, frames=81, steps=25, width=480, height=832, seed=42,
             negative="blurry, low quality, distorted, deformed, watermark, text, logo, static image, jump cut"):
    import torch
    pipe = load_pipe()
    gen = torch.Generator("cpu").manual_seed(seed)
    t0 = time.time()
    print(f"[gen] {width}x{height} {frames}f {steps} steps -> {out}", flush=True)
    print(f"[gen] prompt: {prompt[:200]}", flush=True)
    result = pipe(
        prompt=prompt,
        negative_prompt=negative,
        num_frames=frames,
        num_inference_steps=steps,
        guidance_scale=5.0,
        height=height,
        width=width,
        generator=gen,
    )
    fps = 16
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    from diffusers.utils import export_to_video
    export_to_video(result.frames[0], str(out), fps=fps)
    dt = time.time() - t0
    print(f"[gen] DONE {dt:.0f}s -> {out} ({frames/fps:.1f}s clip)", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt")
    ap.add_argument("--scene", type=int, help="screenplay scene number 1-6")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--frames", type=int, default=81)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--height", type=int, default=832)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--negative", default=None)
    args = ap.parse_args()

    if args.list:
        sc = json.loads(SCREENPLAY.read_text(encoding="utf-8"))
        for s in sc["scenes"]:
            print(f"  scene {s['n']}: {s['time']}  {s['visual'][:80]}")
        return

    prompt = args.prompt
    out = args.out
    if args.scene:
        sc = json.loads(SCREENPLAY.read_text(encoding="utf-8"))
        scene = next((s for s in sc["scenes"] if s["n"] == args.scene), None)
        if not scene:
            sys.exit(f"no scene {args.scene}")
        prompt = prompt or scene["t2v_prompt"]
        out = out or str(OUTDIR / f"scene{scene['n']}.mp4")
        # frames from shot duration (sec * 16fps, rounded to 4k+1)
        if args.frames == 81:
            n = round(scene["seconds"] * 16)
            args.frames = max(17, ((n - 1) // 4) * 4 + 1)
    if not prompt:
        sys.exit("need --prompt or --scene")
    if not out:
        name = "clip_" + time.strftime("%Y%m%d_%H%M%S") + ".mp4"
        out = str(OUTDIR / name)

    kw = dict(frames=args.frames, steps=args.steps, width=args.width,
              height=args.height, seed=args.seed)
    if args.negative:
        kw["negative"] = args.negative
    generate(prompt, out, **kw)


if __name__ == "__main__":
    main()
