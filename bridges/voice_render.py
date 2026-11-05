"""Speech synthesis and forced alignment, run under the heavy voice env.

Mirrors the manim_render.py arrangement: the pipeline never imports torch.
This file is OUR code and lives in OUR repo, but it is executed by another
interpreter — the aarch64+CUDA venv at config.VOICE_ROOT, which already carries
torch 2.13.0+cu130, torchaudio 2.11.0+cu130, chatterbox and kokoro-onnx.

Protocol: one JSON request on stdin, one JSON response on stdout, subcommand in
argv[1]. Anything the libraries print on import goes to stderr, so stdout stays
a clean channel.

    echo '{"text":"hello","out":"/tmp/a.wav"}' | VOICE_PY voice_render.py synth
    echo '{"wav":"/tmp/a.wav","text":"hello"}' | VOICE_PY voice_render.py align
"""
import json
import os
import re
import sys
from pathlib import Path

# Model files the Reachy work already downloaded. Overridable so this bridge is
# not welded to one machine's layout.
CODE = Path(os.environ.get("CODE_ROOT", Path.home() / "Desktop" / "code"))
KOKORO_MODEL = Path(os.environ.get("KOKORO_MODEL", CODE / "reachy-voice-models" / "kokoro-v1.0.onnx"))
KOKORO_VOICES = Path(os.environ.get("KOKORO_VOICES", CODE / "reachy-voice-models" / "voices-v1.0.bin"))

# <bookmark mark='x'/> — manim-voiceover's syntax, adopted as a convention only.
# We resolve marks to timestamps ourselves; nothing here depends on that library.
BOOKMARK = re.compile(r"<\s*bookmark\s+mark\s*=\s*['\"]([^'\"]+)['\"]\s*/?\s*>")


def strip_bookmarks(text: str):
    """Return (clean_text, {mark: word_index_it_precedes}).

    The index is counted in SPOKEN words, so it survives alignment and can be
    turned into a timestamp once the words have spans.
    """
    marks, out, spoken = {}, [], 0
    for piece in re.split(r"(<\s*bookmark[^>]*>)", text):
        m = BOOKMARK.fullmatch(piece.strip()) if piece.strip() else None
        if m:
            marks[m.group(1)] = spoken
        else:
            out.append(piece)
            spoken += len(piece.split())
    return re.sub(r"\s+", " ", "".join(out)).strip(), marks


def normalize(text: str) -> list[str]:
    """Words as the CTC model spells them: uppercase A-Z plus apostrophe.

    Anything else is dropped rather than guessed at. A word that normalizes to
    nothing (a bare number, an emoji) is dropped entirely — keeping it would
    shift every subsequent word's alignment.
    """
    words = []
    for raw in text.split():
        w = re.sub(r"[^A-Z']", "", raw.upper())
        if w:
            words.append(w)
    return words


# ------------------------------------------------------------------- synth

def synth(req: dict) -> dict:
    import soundfile as sf

    text, out = req["text"], Path(req["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    backend = req.get("backend") or ("chatterbox" if req.get("reference") else "kokoro")

    if backend == "chatterbox":
        import torch
        from chatterbox.tts import ChatterboxTTS
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = ChatterboxTTS.from_pretrained(device=device)
        kw = {}
        if req.get("reference"):
            kw["audio_prompt_path"] = str(req["reference"])
        wav = model.generate(text, **kw)
        samples, rate = wav.squeeze(0).cpu().numpy(), model.sr
        model_id = "ResembleAI/chatterbox"
    elif backend == "kokoro":
        from kokoro_onnx import Kokoro
        k = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
        samples, rate = k.create(text, voice=req.get("voice", "am_michael"),
                                 speed=float(req.get("speed", 1.0)), lang="en-us")
        model_id = "hexgrad/Kokoro-82M"
    else:
        return {"ok": False, "error": f"unknown backend {backend!r}"}

    sf.write(str(out), samples, rate)
    return {"ok": True, "backend": backend, "model": model_id,
            "sample_rate": int(rate), "seconds": round(len(samples) / rate, 3)}


# ------------------------------------------------------------------- align

def align(req: dict) -> dict:
    """Word-level timings for text we already know. Forced alignment, not ASR.

    WAV2VEC2_ASR_BASE_960H, not MMS_FA: the MMS_FA weights are CC-BY-NC and
    this channel is meant to be monetisable. Same API, MIT weights.
    """
    import torch
    import torchaudio
    import torchaudio.functional as F

    clean, marks = strip_bookmarks(req["text"])
    words = normalize(clean)
    if not words:
        return {"ok": False, "error": "no alignable words in transcript"}

    device = "cuda" if torch.cuda.is_available() else "cpu"
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    model = bundle.get_model().to(device)
    labels = bundle.get_labels()
    lookup = {c: i for i, c in enumerate(labels)}

    # soundfile, not torchaudio.load: torchaudio 2.11 routes load() through
    # TorchCodec, which is not installed here. We only ever read WAV, and
    # soundfile is already a dependency of the synth path.
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(req["wav"], dtype="float32", always_2d=True)
    wave = torch.from_numpy(np.ascontiguousarray(data.T[:1]))  # forced_align is mono-only
    if sr != bundle.sample_rate:
        wave = F.resample(wave, sr, bundle.sample_rate)

    with torch.inference_mode():
        emission, _ = model(wave.to(device))
        # This bundle emits LOGITS. forced_align wants log-probabilities, and
        # merge_tokens' scores are only interpretable as such. The Viterbi path
        # is unchanged either way (log_softmax shifts each frame by a constant),
        # which is why the timings look fine while the scores are meaningless —
        # and the scores are what tells us an alignment went wrong.
        emission = torch.log_softmax(emission, dim=-1)

    unknown = sorted({c for w in words for c in w} - set(lookup))
    if unknown:
        return {"ok": False, "error": f"characters absent from the model: {unknown}"}

    tokens = [lookup[c] for w in words for c in w]
    targets = torch.tensor([tokens], dtype=torch.int32, device=device)
    aligned, scores = F.forced_align(emission, targets, blank=0)
    spans = F.merge_tokens(aligned[0], scores[0].exp())

    # One emission frame is this many seconds of audio.
    per_frame = (wave.size(1) / emission.size(1)) / bundle.sample_rate

    out, cursor = [], 0
    for w in words:
        chunk = spans[cursor:cursor + len(w)]
        cursor += len(w)
        if not chunk:
            continue
        out.append({
            "word": w,
            "start": round(chunk[0].start * per_frame, 3),
            "end": round(chunk[-1].end * per_frame, 3),
            "score": round(float(sum(s.score for s in chunk) / len(chunk)), 4),
        })

    return {
        "ok": True,
        "seconds": round(wave.size(1) / bundle.sample_rate, 3),
        "model": "torchaudio/WAV2VEC2_ASR_BASE_960H",
        "words": out,
        # A mark sits before word N; if it trails the last word it lands at the end.
        "bookmarks": {
            name: (out[i]["start"] if i < len(out) else out[-1]["end"]) if out else 0.0
            for name, i in marks.items()
        },
        "speech_start": out[0]["start"] if out else 0.0,
        "speech_end": out[-1]["end"] if out else 0.0,
    }


# ------------------------------------------------------------------- image

# Backplates and texture only. Whatever a diffusion model draws of a graph, a
# proof or an equation will be confidently wrong, and on this channel a wrong
# visual is a factual error rather than a style choice — that is exactly why
# the diagrams are Manim.
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "stable-diffusion-v1-5/stable-diffusion-v1-5")


def image(req: dict) -> dict:
    import torch
    from diffusers import StableDiffusionPipeline

    out = Path(req["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipe = StableDiffusionPipeline.from_pretrained(
        IMAGE_MODEL,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        safety_checker=None)
    pipe = pipe.to(device)
    # Seeded so a rebuild reproduces the same picture; an unseeded generator
    # would make the asset change every time doit re-ran the beat.
    generator = torch.Generator(device=device).manual_seed(int(req.get("seed", 0)))
    result = pipe(req["prompt"],
                  num_inference_steps=int(req.get("steps", 30)),
                  width=int(req.get("width", 768)), height=int(req.get("height", 512)),
                  generator=generator)
    result.images[0].save(str(out))
    return {"ok": True, "model": IMAGE_MODEL, "seed": int(req.get("seed", 0)),
            "steps": int(req.get("steps", 30))}


# ----------------------------------------------------------------- animate

# Monocular depth. Small is deliberate: this drives a defocus, not a
# measurement, and the Small checkpoint is already on this machine at 95MB
# while Large is 1.3GB for a blur nobody can tell apart.
DEPTH_MODEL = os.environ.get("DEPTH_MODEL", "depth-anything/Depth-Anything-V2-Small-hf")

# How many pre-blurred copies the defocus interpolates between. Real per-pixel
# variable blur means a different kernel at every pixel, which is enormously
# slow; compositing a small pyramid by circle-of-confusion is the standard
# approximation and is visually indistinguishable at these radii.
BLUR_LEVELS = 5


def animate(req: dict) -> dict:
    """Turn one still into a moving clip using its depth map.

    kinds:
      defocus   the focal plane sits at a depth and everything else softens.
                focus_from -> focus_to over the clip racks the focus, which is
                the shot that most reads as "a camera was here".
    """
    import numpy as np
    import torch
    import torch.nn.functional as F
    from PIL import Image
    from transformers import pipeline as hf_pipeline

    still = Path(req["still"])
    out = Path(req["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = int(req.get("width", 1920)), int(req.get("height", 1080))
    fps = int(req.get("fps", 30))
    seconds = float(req.get("seconds", 5.0))
    frames = max(2, int(round(fps * seconds)))
    dev = "cuda" if torch.cuda.is_available() else "cpu"

    img = Image.open(still).convert("RGB")
    # The still already sits in the project frame (imagery letterboxed it), so
    # it is resized, never re-cropped — cropping here would silently undo the
    # "fit, don't crop" decision the imagery node makes deliberately.
    img = img.resize((W, H), Image.LANCZOS)
    t = (torch.from_numpy(np.asarray(img)).float().permute(2, 0, 1)[None] / 255.0).to(dev)

    depth = hf_pipeline("depth-estimation", model=DEPTH_MODEL,
                        device=0 if dev == "cuda" else -1)(img)["depth"]
    d = torch.from_numpy(np.asarray(depth).astype("float32"))[None, None].to(dev)
    d = F.interpolate(d, size=(H, W), mode="bilinear", align_corners=False)
    d = (d - d.min()) / (d.max() - d.min() + 1e-8)          # 1 near, 0 far
    # Soften the depth edges. Raw monocular depth has hard steps at object
    # boundaries, and a hard step in a BLUR map shows up as a visible outline
    # around the subject — the classic "cut-out" portrait-mode artefact.
    d = F.avg_pool2d(d, 31, stride=1, padding=15)

    strength = float(req.get("strength", 12.0))
    f0 = float(req.get("focus_from", 0.8))
    f1 = float(req.get("focus_to", f0))
    push = float(req.get("push", 0.0))

    # Blur pyramid, built once and reused for every frame.
    levels = [t]
    for i in range(1, BLUR_LEVELS):
        sigma = strength * (i / (BLUR_LEVELS - 1))
        k = max(3, int(sigma * 3) | 1)
        xs = torch.arange(k, device=dev, dtype=torch.float32) - k // 2
        g = torch.exp(-(xs ** 2) / (2 * sigma ** 2)); g = (g / g.sum()).view(1, 1, 1, k)
        b = F.conv2d(F.pad(t, (k // 2,) * 2 + (0, 0), mode="reflect"),
                     g.expand(3, 1, 1, k), groups=3)
        b = F.conv2d(F.pad(b, (0, 0) + (k // 2,) * 2, mode="reflect"),
                     g.transpose(2, 3).expand(3, 1, k, 1), groups=3)
        levels.append(b)
    stack = torch.cat(levels, 0)                              # [L,3,H,W]

    ys, xs = torch.meshgrid(torch.linspace(-1, 1, H, device=dev),
                            torch.linspace(-1, 1, W, device=dev), indexing="ij")
    base = torch.stack((xs, ys), -1)[None]

    import subprocess
    # The caller knows which ffmpeg the project uses (it prefers the system one
    # and falls back to the Manim env's NVENC-capable build); this env may have
    # neither on PATH, so it is passed in rather than guessed at.
    ff = req.get("ffmpeg") or os.environ.get("FFMPEG", "ffmpeg")
    proc = subprocess.Popen(
        [ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
         *(["-c:v", "libx264", "-preset", "medium", "-crf", "17"] if os.environ.get("VIDEO_ENCODER") == "libx264"
           else ["-c:v", "h264_nvenc", "-preset", "p6", "-cq", "17"]), "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.PIPE)

    for i in range(frames):
        u = i / (frames - 1)
        plane = f0 + (f1 - f0) * u
        # circle of confusion: distance from the focal plane, 0..1
        coc = (d - plane).abs().clamp(0, 1)
        idx = coc * (BLUR_LEVELS - 1)
        lo = idx.floor().clamp(0, BLUR_LEVELS - 1)
        hi = (lo + 1).clamp(0, BLUR_LEVELS - 1)
        w = (idx - lo)
        gl = torch.gather(stack, 0, lo.long().expand(1, 3, H, W))
        gh = torch.gather(stack, 0, hi.long().expand(1, 3, H, W))
        frame = gl * (1 - w) + gh * w
        if push:
            z = 1.0 + push * u
            g2 = base.clone(); g2[..., 0] /= z; g2[..., 1] /= z
            frame = F.grid_sample(frame, g2, mode="bilinear",
                                  padding_mode="reflection", align_corners=False)
        arr = (frame[0].permute(1, 2, 0).clamp(0, 1) * 255).byte().cpu().numpy()
        proc.stdin.write(arr.tobytes())

    proc.stdin.close()
    if proc.wait() != 0 or not out.exists():
        return {"ok": False, "error": "encoder rejected the frames"}
    return {"ok": True, "model": DEPTH_MODEL, "frames": frames,
            "seconds": round(frames / fps, 3),
            "motion": {"focus_from": f0, "focus_to": f1,
                       "strength": strength, "push": push}}


COMMANDS = {"synth": synth, "align": align, "image": image, "animate": animate}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in COMMANDS:
        print(json.dumps({"ok": False, "error": f"usage: {sys.argv[0]} {'|'.join(COMMANDS)}"}))
        sys.exit(2)
    try:
        result = COMMANDS[cmd](json.load(sys.stdin))
    except Exception as exc:                       # the caller gets a verdict, never a traceback
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result))
