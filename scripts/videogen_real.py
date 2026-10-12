# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""REAL VIDEO - Wan2.1-T2V-1.3B text-to-video, one clip per scene.

This REPLACES the Ken Burns zoom that make_video.py does on a still image. There is no
zoompan anywhere in this file: every frame comes out of a video model.

Called by NOAH (scripts/department.py) when the job carries a style tag:
    real    -> photoreal live-action human  (DISPATCHER slot 12:00)
    cartoon -> 2D vector / cel animation    (DISPATCHER slot 23:00)
The tag and every character detail come from the department's own approved artefacts:
AISHA visual_spec (age, dress, face, background) -> DANIEL storyboard -> SOPHIE script.

COST, measured on this laptop (RTX 4060 8GB): ~360 s per 3-second 480x832 clip.
A 4-scene video is therefore ~25 minutes of GPU. That is fine unattended and is why
DISPATCHER never starts a second video while one is in flight.

Usage:
    python scripts/videogen_real.py --plan plan.json --out <folder> [--style real]
    python scripts/videogen_real.py --plan plan.json --prompts-only     # no GPU
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import argparse, json, math, os, subprocess, sys, time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))

MODEL = Path(__file__).resolve().parents[1] / "models" / "Wan2.1-T2V-1.3B-Diffusers"
PY    = ROOT / "venv" / "Scripts" / "python.exe"

W, H, FPS   = 480, 832, 16          # portrait reel
STEPS       = 20                    # measured: 25 -> 360 s/clip, 20 is ~15% faster
FRAMES      = 49                    # 3.0 s at 16 fps  (must be 4k+1)

# What makes Wan2.1 move a character instead of producing a still.
NEGATIVE = ("static image, still frame, no motion, zoom, ken burns, camera pan over a photo, "
            "blurry, low quality, distorted face, deformed hands, extra limbs, watermark, "
            "text, logo, subtitles, duplicate person, frozen pose, morphing face")

STYLE = {
    "real": ("cinematic live-action film still, photorealistic human, beautiful photogenic young adult, natural skin texture, "
             "flattering soft realistic lighting, shallow depth of field, 35mm, high detail"),
    "cartoon": ("2D hand-drawn cartoon animation, bold clean black outlines, flat cel shading, "
                "bright saturated colours, simple expressive shapes, storybook style"),
}


def log(m):
    print("[video %s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


# ----------------------------------------------------------------- prompt build
def _txt(d, *keys, default=""):
    """First non-empty string among keys."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def character_block(visual, script=None):
    """AISHA's visual spec flattened into one reusable description so the SAME person
    appears in every scene: same age, same face, same clothes, same background.

    The real artefacts use characters[{name,age,dress,look}], backgrounds[...] and
    palette[...] - so background/palette arrive as LISTS and must be joined."""
    chars = visual.get("characters") or []
    if not chars and isinstance(visual.get("character"), dict):
        chars = [visual["character"]]
    if not chars and script:
        chars = script.get("characters") or []
    out = []
    for c in chars:
        if isinstance(c, str):
            out.append(c)
            continue
        if not isinstance(c, dict):
            continue
        name = _txt(c, "name", "character", default="the protagonist")
        age = _txt(c, "age", "years")
        look = _txt(c, "look", "appearance", "face", "features", "description")
        dress = _txt(c, "dress", "outfit", "clothes", "wardrobe", "costume", "clothing")
        parts = [name]
        if age:
            parts.append("age %s" % age)
        if look:
            parts.append(look)
        if dress:
            parts.append("wearing %s" % dress)
        out.append(", ".join(parts))

    def _join(v):
        if isinstance(v, list):
            return "; ".join(str(x) for x in v if str(x).strip())
        return str(v) if v else ""

    bg = _join(visual.get("backgrounds")) or _txt(visual, "background", "setting", "location")
    if not bg and script:
        bg = _txt(script, "background", "setting")
    pal = _join(visual.get("palette")) or _txt(visual, "palette", "colour_palette")
    # CONSISTENCY: every clip is drawn separately, so a second named character (or a different setting per
    # scene) makes the model invent a new face each time. One main character, described identically in every
    # shot, and ONE setting.
    main = (out[0] + " - the same person in every shot, same face, same hair, same clothes") if out \
        else "a young adult protagonist - the same person in every shot"
    bg = (bg.split(";")[0].strip() if bg else bg)
    return {
        "characters": main,
        "background": bg or "a clean simple interior with soft natural light",
        "palette": pal or "warm natural colours",
    }


def scene_prompt(scene, ctx, style):
    """One Wan2.1 prompt per shot. Motion words are mandatory - without them the model
    tends to return a near-still frame, which is exactly the Ken Burns failure.

    The real storyboard scenes are {n, beat, framing, transition}: beat is what HAPPENS,
    framing is the CAMERA. Both go in, plus this scene's slice of the movement list."""
    tag = STYLE.get(style, STYLE["real"])
    who = ctx["characters"]
    where = ctx["background"]
    beat = _txt(scene, "beat", "visual", "action", "description", "shot", "prompt")
    framing = _txt(scene, "framing", "camera", "shot_type")
    emo = _txt(scene, "emotion", "mood", "feeling")
    motion = _txt(scene, "motion", "movement")
    if not motion:
        motion = ("the characters breathe and move naturally, natural body language, "
                  "subtle head and hand movement")
    bits = [tag]
    bits.append("%s, %s." % (who, beat or "standing naturally in the scene"))
    if framing:
        bits.append("Framing: %s." % framing)
    bits.append("Setting: %s." % where)
    if emo:
        bits.append("Mood: %s." % emo)
    bits.append("Motion: %s." % motion)
    bits.append("Colour palette: %s. Vertical composition, whole body in frame." % ctx["palette"])
    return " ".join(bits)


def dialogues(scene):
    """Lines with a speaker. The artefacts spell this key 'dialogs' (older ones
    'dialogue'/'dialog'); a bare string is narration."""
    out = []
    raw = (scene.get("dialogs") or scene.get("dialogue") or scene.get("dialog")
           or scene.get("lines") or [])
    if isinstance(raw, str):
        raw = [{"speaker": "Narrator", "line": raw}]
    for line in raw:
        if isinstance(line, dict):
            who = _txt(line, "speaker", "character", "who", default="Narrator")
            txt = _txt(line, "line", "text", "say", "words", "dialog", "dialogue")
        elif isinstance(line, str):
            who, txt = "Narrator", line
        else:
            continue
        if txt:
            out.append((who, txt))
    if not out:
        nar = _txt(scene, "narration", "voiceover", "vo")
        if nar:
            out.append(("Narrator", nar))
    return out


def build(plan_path, style="real", limit=0):
    """Turn the department's approved artefacts into per-scene Wan prompts + VO lines.

    Dialogue and movement are SPLICED across the scenes so each shot gets its own lines
    and its own action - otherwise all four prompts come out identical, which is the
    other half of the Ken Burns problem (one idea repeated as a slideshow)."""
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    sc = plan.get("script") or {}
    sb = plan.get("storyboard") or {}
    vis = plan.get("visual") or {}
    emo = plan.get("emotion") or {}
    ctx = character_block(vis, sc)

    scenes = sb.get("scenes") or sc.get("scenes") or []
    if not scenes:
        n = 4 if plan.get("kind") == "video" else 1
        nar = _txt(sc, "narration", "hook", default="a calm opening shot")
        scenes = [{"n": i + 1, "beat": nar} for i in range(n)]
    if limit:
        scenes = scenes[:limit]

    all_dialogs = (sb.get("dialogs") or sc.get("dialogs") or [])
    all_move = (sb.get("movement") or sc.get("movement") or [])
    arc = sb.get("story_arc") or sc.get("story_arc") or {}
    n_sc = max(1, len(scenes))

    out = []
    for i, s in enumerate(scenes, 1):
        s = dict(s) if isinstance(s, dict) else {"beat": str(s)}
        s.setdefault("n", i)
        if not dialogues(s) and all_dialogs:
            n_d = len(all_dialogs)     # every written line is used exactly once (the old "per = n // scenes" dropped the remainder)
            s["dialogs"] = all_dialogs[((i - 1) * n_d) // n_sc:(i * n_d) // n_sc]
        if all_move:
            n_m = len(all_move)
            s["motion"] = "; ".join(
                "%s %s" % (_txt(m, "character", "who", default="the character"),
                           _txt(m, "action", "movement", default="moves"))
                for m in all_move[((i - 1) * n_m) // n_sc:(i * n_m) // n_sc] if isinstance(m, dict))
        if not _txt(s, "emotion") and emo:
            s["emotion"] = _txt(emo, "emotion", "mood", "overall", "tone", "atmosphere")
        if not _txt(s, "emotion") and arc:
            s["emotion"] = _txt(arc, "insight", "tension", "problem")
        s["t2v_prompt"] = scene_prompt(s, ctx, style)
        s["dialogue"] = [{"speaker": w, "line": t} for w, t in dialogues(s)]
        s["seconds"] = float(s.get("seconds") or s.get("duration") or 3.0)
        out.append(s)
    return {"topic": plan.get("topic"), "style": style, "context": ctx,
            "scenes": out, "music": _txt(plan, "music", default="calm")}


# --------------------------------------------------------------------- generate
_PIPE = None


def pipe():
    global _PIPE
    if _PIPE is not None:
        return _PIPE
    import torch
    from diffusers import WanPipeline
    if not MODEL.exists():
        raise RuntimeError("Wan2.1 model missing at %s" % MODEL)
    t0 = time.time()
    log("loading Wan2.1 (fp16, cpu-offload) ...")
    p = WanPipeline.from_pretrained(str(MODEL), dtype=torch.float16)
    p.enable_model_cpu_offload()
    try:
        p.enable_vae_tiling()
    except Exception:
        pass
    p.set_progress_bar_config(disable=True)
    _PIPE = p
    log("loaded in %.0fs" % (time.time() - t0))
    return p


def gen_clip(prompt, out_path, seed=42, steps=STEPS, frames=FRAMES):
    """One 3-second moving clip. THIS is the whole point - real motion, not a pan."""
    import torch
    p = pipe()
    n = max(17, ((int(frames) - 1) // 4) * 4 + 1)
    g = torch.Generator("cpu").manual_seed(seed)
    t0 = time.time()
    log("Wan %dx%d %df %d steps -> %s" % (W, H, n, steps, Path(out_path).name))
    res = p(prompt=prompt, negative_prompt=NEGATIVE, num_frames=n,
            num_inference_steps=steps, guidance_scale=5.0,
            height=H, width=W, generator=g)
    from diffusers.utils import export_to_video
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    export_to_video(res.frames[0], str(out_path), fps=FPS)
    log("clip done in %.0fs" % (time.time() - t0))
    return out_path


# ------------------------------------------------------------------------ voice
VOICE = {"narrator": "en-GB-SoniaNeural", "default": "en-US-AriaNeural",
         "male": "en-US-GuyNeural", "female": "en-US-JennyNeural"}


def speak(text, mp3, voice="default", rate="+0%"):
    """One spoken line -> an audio file. NEVER returns a path that does not exist.

    1. Microsoft Edge neural voice (edge-tts, online) - 3 tries. edge-tts 7.x made save() a
       coroutine; the old call returned instantly and wrote nothing, so no video ever got a
       voice and the join step crashed on the missing files.
    2. Windows built-in voice (System.Speech, offline) - so a network hiccup never costs the
       voice-over.
    Raises only if both fail; the caller then skips that one line and says so in the log."""
    import asyncio
    mp3 = Path(mp3)
    mp3.parent.mkdir(parents=True, exist_ok=True)
    v = VOICE.get(voice, VOICE["default"])
    last = None
    try:
        import edge_tts
        for attempt in range(3):
            try:
                asyncio.run(edge_tts.Communicate(text, v, rate=rate).save(str(mp3)))
                if mp3.exists() and mp3.stat().st_size > 1000:
                    return mp3
            except Exception as e:
                last = e
                time.sleep(2 * (attempt + 1))
    except Exception as e:
        last = e
    # offline fallback: Windows SAPI -> wav -> mp3
    wav = mp3.with_suffix(".wav")
    safe = text.replace("'", " ").replace('"', " ")
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$s.SetOutputToWaveFile('%s'); $s.Speak('%s'); $s.Dispose()" % (str(wav), safe))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120)
    if wav.exists() and wav.stat().st_size > 1000:
        run(["-i", str(wav), "-c:a", "libmp3lame", "-q:a", "4", str(mp3)])
        if mp3.exists() and mp3.stat().st_size > 1000:
            log("voice: used the offline Windows voice for one line (edge-tts failed: %s)" % str(last)[:80])
            return mp3
    raise RuntimeError("no voice engine produced audio (edge-tts: %s; windows: %s)"
                       % (str(last)[:80], (r.stderr or "")[:80]))


def ffmpeg_bin():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args, timeout=1800):
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-y"] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: %s" % (r.stderr or "")[-400:])
    return r


def _duration(path):
    """Length in seconds. Uses `ffmpeg -i` (no ffprobe ships with imageio-ffmpeg): ffmpeg exits 1
    when given no output, but it still prints the duration, so parse it and ignore the exit code."""
    import re
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    if not m:
        raise RuntimeError("cannot read the length of %s" % path)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def assemble(clips, audio, out_path, transition=0.4, music=None):
    """Join the Wan clips with a short crossfade, then lay the voice track on top.

    Fixed 2026-10-10 - the first version never worked: it ran `ffmpeg -i clip` with no output
    (always exit 1), asked for audio streams that Wan clips do not have, and used xfade
    offset=0, which is wrong. Now: video-only crossfade chain with the correct offsets, then
    one mux step. If the voice is longer than the video, the last frame is held so the voice is
    never cut mid-sentence."""
    files = [str(c) for c in clips]
    if not files:
        raise RuntimeError("no clips to assemble")
    out_path = str(out_path)
    work = Path(out_path).parent / "_parts"
    work.mkdir(parents=True, exist_ok=True)
    durs = [_duration(f) for f in files]
    vout = str(work / "video.mp4")
    if len(files) == 1:
        run(["-i", files[0], "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", vout])
    else:
        T = min(transition, min(durs) / 3.0)
        args = []
        for f in files:
            args += ["-i", f]
        norm = "".join("[%d:v]fps=%d,format=yuv420p,setsar=1[n%d];" % (i, FPS, i) for i in range(len(files)))
        chain, prev, acc = "", "n0", 0.0
        for i in range(1, len(files)):
            acc += durs[i - 1] - T                      # where the fade into clip i starts
            out = "v%d" % i
            chain += "[%s][n%d]xfade=transition=fade:duration=%.3f:offset=%.3f[%s];" % (prev, i, T, acc, out)
            prev = out
        fc = (norm + chain).rstrip(";")
        run(args + ["-filter_complex", fc, "-map", "[%s]" % prev, "-an",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", vout])
    vdur = _duration(vout)

    if audio and Path(audio).exists():
        adur = _duration(audio)
        extra = max(0.0, adur - vdur + 0.3)
        vf = ["-vf", "tpad=stop_mode=clone:stop_duration=%.2f" % extra] if extra > 0.05 else []
        total = max(vdur, adur + 0.3)
        # The VIDEO sets the length. A voice shorter than the picture is padded with silence (apad) -
        # the old "-shortest" cut the 11 s video down to a 1.9 s voice track. Never again.
        run(["-i", vout, "-i", str(audio)] + vf +
            ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-af", "apad", "-r", "30", "-c:a", "aac", "-b:a", "192k",   # 30 fps: TikTok rejects 16 fps ("frame_rate_check_failed")
            
             "-map", "0:v", "-map", "1:a", "-t", "%.2f" % total, out_path])
    else:
        run(["-i", vout, "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", out_path])
    log("assembled %d clip(s): video %.1fs%s" % (len(files), vdur,
        (", voice %.1fs" % _duration(audio)) if audio and Path(audio).exists() else ", no voice"))
    return out_path


# -------------------------------------------------------------------- main run
_NOT_SPEECH = {"line", "speaker", "text", "narrator", "none", "null", "dialogue", "dialog", ""}


def voice_parts(spec, hook=""):
    """The ordered list of (scene, speaker, sentence) the voice-over must speak.

    Bug fixed 2026-10-11: each scene's dialogue is a list of {"speaker","line"} DICTS, and the old loop unpacked each
    dict as (who, line) - which yields the dict's KEYS, so the narrator said the word "line" instead of the sentence
    ("line, line, line") and every written line was lost. Read the fields by name, and never speak a field name."""
    parts = []
    for i, s in enumerate(spec["scenes"], 1):
        for d in s.get("dialogue") or []:
            if isinstance(d, dict):
                who, line = d.get("speaker") or "Narrator", d.get("line")
            else:
                who, line = d[0], d[1]
            parts.append((i, who, str(line or "").strip()))
    parts = [p for p in parts if p[2].strip(" .!?:-").lower() not in _NOT_SPEECH]
    # a ~11 s video needs ~25 words of voice; when the dialogue is thinner, a narrator reads the post's hook first
    if hook and sum(len(p[2].split()) for p in parts) < 16:
        parts.insert(0, (0, "Narrator", hook))
    return parts


def produce(plan_path, out_dir, style="real", limit=0, make_voice=True):
    """The whole video job. Returns a dict NOAH can store on the run."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = build(plan_path, style=style, limit=limit)
    (out_dir / "video_plan.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    log("plan: %d scene(s), style=%s" % (len(spec["scenes"]), style))

    clips = []
    for i, s in enumerate(spec["scenes"], 1):
        dst = out_dir / ("clip%d.mp4" % i)
        sig = out_dir / ("clip%d.sig" % i)
        want = __import__("hashlib").sha1((s["t2v_prompt"] + "|%d|%d|%d" % (W, H, STEPS)).encode("utf-8")).hexdigest()
        have = sig.read_text(encoding="utf-8").strip() if sig.exists() else ""
        if dst.exists() and dst.stat().st_size > 50000 and have == want:
            log("clip %d already rendered with this exact prompt - reusing it (saves ~10 min)" % i)
        else:
            gen_clip(s["t2v_prompt"], dst, seed=1000)   # SAME seed for every clip: keeps one identity across scenes
            sig.write_text(want, encoding="utf-8")
        clips.append(dst)

    audio = None
    report = {"lines_planned": 0, "lines_spoken": 0, "rate": "+0%", "voice_s": 0.0, "video_s": 0.0, "ok": True}
    if make_voice:
        try:
            hook = ((json.loads(Path(plan_path).read_text(encoding="utf-8")).get("script") or {}).get("hook") or "").strip()
        except Exception:
            hook = ""
        parts = voice_parts(spec, hook)
        report["lines_planned"] = len(parts)
        # the picture length after the crossfades: the voice must fit inside it
        vid_s = max(1.0, sum(_duration(c) for c in clips) - 0.4 * max(0, len(clips) - 1))
        report["video_s"] = round(vid_s, 2)
        if parts:
            segs = []
            for rate in ("+0%", "+12%", "+25%"):          # speak naturally; speed up a little only if it would not fit
                segs, total, failed = [], 0.0, 0
                for k, (si, who, line) in enumerate(parts):
                    v = ("narrator" if str(who).lower() == "narrator" else "male" if str(who).lower().startswith(("dan", "noah", "mr", "he "))
                         else ("female" if str(who).lower().startswith(("may", "soph", "daniel"))
                               else "default"))
                    f = out_dir / ("vo%02d.mp3" % k)
                    try:
                        speak(line, f, v, rate=rate)
                        if f.exists() and f.stat().st_size > 1000:
                            segs.append(f)
                            total += _duration(f)
                        else:
                            failed += 1
                    except Exception as e:
                        failed += 1
                        log("VO failed on line %d: %s" % (k, str(e)[:90]))
                report.update(rate=rate, voice_s=round(total, 2), lines_spoken=len(segs))
                if failed or total <= vid_s + 0.3:
                    break
            if segs:
                lst = out_dir / "vo.txt"
                lst.write_text("".join("file '%s'\n" % p for p in segs), encoding="utf-8")
                audio = out_dir / "voice.m4a"
                run(["-f", "concat", "-safe", "0", "-i", str(lst),
                     "-c:a", "aac", "-b:a", "160k", str(audio)])
        report["ok"] = (report["lines_spoken"] == report["lines_planned"]) and report["lines_planned"] > 0
        log("voice-over: %d of %d line(s) spoken, %.1fs of voice for %.1fs of picture (rate %s)"
            % (report["lines_spoken"], report["lines_planned"], report["voice_s"], report["video_s"], report["rate"]))
        (out_dir / "voice_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    final = out_dir / "final.mp4"
    assemble(clips, audio, final)
    log("FINAL -> %s (%.1f MB)" % (final, final.stat().st_size / 1e6))
    return {"final": str(final), "clips": [str(c) for c in clips],
            "audio": str(audio) if audio else None,
            "scenes": len(spec["scenes"]), "style": style,
            "has_audio": bool(audio), "voice": report}


def motion_metrics(video, fps=4, grid=6, max_frames=40):
    """Tell REAL motion apart from a ZOOM/PAN over a still image.

    A naive frame-to-frame difference does NOT work: Ken Burns zoom also changes pixels,
    and a real clip measured 4.13 on the same scale - the first version of this check
    would have passed a Ken Burns video. So this uses the property that actually
    separates them:

      * real generated motion  - a person moves; most of the FRAME stays still
      * zoom / pan on a still  - EVERY pixel shifts a little, everywhere at once

    So we cut each frame into a grid and measure how much of the frame is quiet.
    static_frac near 0  => the whole image is moving => that is a camera move over a
                          still, NOT a character performing.
    static_frac high    => parts of the scene held still while other parts moved =>
                          that is real motion.
    """
    import imageio_ffmpeg
    import numpy as np
    tmp = Path(str(video) + ".motion.mp4")
    try:
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-y",
                        "-i", str(video), "-vf", "fps=%d,scale=240:-2,format=gray"
                        % fps, str(tmp)], capture_output=True, timeout=300)
        try:
            import imageio.v3 as iio
            frames = iio.imiter(tmp, plugin="pyav")
        except Exception:
            import imageio
            frames = imageio.mimread(str(tmp), memtest=False)
            frames = iter(frames)

        prev, mean_d, static_fr, n = None, [], [], 0
        for f in frames:
            a = np.asarray(f, dtype="float32")
            if a.ndim == 3:
                a = a.mean(axis=2)
            if prev is not None and a.shape == prev.shape:
                d = np.abs(a - prev)
                mean_d.append(float(d.mean()))
                h, w = d.shape
                bh, bw = max(1, h // grid), max(1, w // grid)
                blocks = d[:bh * grid, :bw * grid].reshape(grid, bh, grid, bw).mean(axis=(1, 3))
                thresh = 1.5
                static_fr.append(float((blocks < thresh).mean()))
                n += 1
            prev = a
            if n >= max_frames:
                break
        if not mean_d:
            return None
        return {"mean": round(sum(mean_d) / len(mean_d), 3),
                "static_frac": round(sum(static_fr) / len(static_fr), 3),
                "pairs": n}
    except Exception:
        return None
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


# A still-camera zoom leaves almost no quiet area. Real motion keeps plenty.
STATIC_FRAC_MIN = 0.25          # below this => camera move over a still (Ken Burns)


def motion_score(video):
    """Backwards-compatible scalar: the static fraction. Higher = more real motion."""
    m = motion_metrics(video)
    return None if m is None else m["static_frac"]


def looks_like_ken_burns(video):
    """(is_ken_burns, detail). Used by GRACE to refuse a zoomed still photo."""
    m = motion_metrics(video)
    if m is None:
        return False, {"checked": False}
    bad = m["static_frac"] < STATIC_FRAC_MIN
    return bad, m


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--style", default="real")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--prompts-only", action="store_true")
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    if a.prompts_only:
        print(json.dumps(build(a.plan, a.style, a.limit), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(produce(a.plan, a.out, a.style, a.limit,
                                 make_voice=not a.no_voice), indent=2))