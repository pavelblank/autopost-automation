# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Prove the Ken Burns detector separates a camera-move-over-a-still from real motion.
Test data is SYNTHETIC (no GPU) plus one real AutoPost Ken Burns video."""
import subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import videogen_real as V
import numpy as np

TMP = Path(r"C:\Users\user\AppData\Local\Temp\opencode\motiontest")
TMP.mkdir(parents=True, exist_ok=True)
FF = V.ffmpeg_bin()
S = 320


def make_zoom(still, out):
    """EXACTLY what make_video.py does: one still image + zoompan."""
    subprocess.run([FF, "-hide_banner", "-y", "-loop", "1", "-i", str(still),
                    "-t", "4", "-r", "16",
                    "-vf", "scale=800:-2,zoompan=z='min(zoom+0.0009,1.35)':d=64:s=320x568:fps=16",
                    "-pix_fmt", "yuv420p", str(out)], capture_output=True, timeout=300)
    return out


def make_moving(out):
    """A person-shaped object walks across a STATIC background - what real generation gives."""
    bg = np.full((S * 2, S, 3), 200, np.uint8)
    frames = []
    for i in range(64):
        f = bg.copy()
        x = 40 + int(i * 3.2) % (S - 80)
        y = 260
        f[y:y + 150, x:x + 70] = (60, 60, 200)      # moving "body"
        f[y - 60:y - 10, x + 10:x + 60] = (40, 40, 40)   # moving "head"
        frames.append(f)
    raw = TMP / "raw.rgb"
    raw.write_bytes(b"".join(f.tobytes() for f in frames))
    subprocess.run([FF, "-hide_banner", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{S}x{S*2}", "-r", "16", "-i", str(raw),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   capture_output=True, timeout=300)
    return out


def still_image(p):
    """A plain PNG still - the same input make_video.py feeds to zoompan."""
    from PIL import Image
    img = np.full((S * 2, S, 3), 200, np.uint8)
    for i in range(0, S * 2, 40):
        img[i:i + 12, :, :] = 120
    Image.fromarray(img).save(str(p))
    return p


print("=" * 74)
print("BUILDING TEST CLIPS")
print("=" * 74)
st = still_image(TMP / "base.png")
zoom = make_zoom(st, TMP / "kenburns.mp4")
move = make_moving(TMP / "realmotion.mp4")
real_kb = (Path(__file__).resolve().parents[2] / "out" / "job_20261010_022218" / "final.mp4")
for lbl, f in (("synthetic KEN BURNS (zoom over a still)", zoom),
               ("synthetic REAL MOTION (object moves, bg still)", move),
               ("REAL AutoPost Ken Burns video", real_kb if real_kb.exists() else None)):
    if not f or not Path(f).exists():
        print(f"  {lbl}: MISSING - skipped"); continue
    bad, m = V.looks_like_ken_burns(f)
    print(f"\n  {lbl}")
    print(f"     file          : {Path(f).name}")
    print(f"     mean diff     : {m.get('mean')}")
    print(f"     static_frac   : {m.get('static_frac')}   (fraction of frame that HOLDS STILL)")
    print(f"     verdict       : {'*** KEN BURNS DETECTED ***' if bad else 'real motion - accepted'}")
    if "KEN BURNS" in lbl.upper():
        assert bad, f"FAIL: detector missed {lbl}"
    else:
        assert not bad, f"FAIL: detector wrongly flagged {lbl}"

print("\n=== MOTION DETECTOR VALIDATED ===")
print("threshold STATIC_FRAC_MIN =", V.STATIC_FRAC_MIN)
print("Ken Burns (camera over still)  -> flagged")
print("Real motion (bg holds still)   -> accepted")

