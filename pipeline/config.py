"""Where things live. The only module allowed to know about absolute paths."""
import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VIDEOS = REPO / "videos"

# The animation node lives in its own repo with its own isolated micromamba env
# (pango/cairo/ffmpeg). We never import Manim — manim_render.py is stdlib-only
# and shells out. Override with MANIM_ROOT if the sibling layout changes.
MANIM_ROOT = Path(os.environ.get("MANIM_ROOT", REPO.parent / "manim"))
MANIM_ENV = MANIM_ROOT / ".micromamba" / "envs" / "manim"
# The memory-line kit (ML series only) lives in the repo-level skill, beside the
# video-pipeline and manim siblings. It runs in the Manim env (pycairo + numpy).
MEMORYLINE_KIT = Path(os.environ.get("MEMORYLINE_KIT", REPO.parent / ".claude" / "skills" / "memory-line" / "kit"))


def memoryline_workers() -> int:
    """Parallel render workers per memory-line beat (each renders a chunk of frames)."""
    return int(os.environ.get("MEMORYLINE_WORKERS", "8"))

# Speech (TTS + forced alignment) has the same shape as animation: a heavy
# aarch64+CUDA env we shell into rather than import. This one was built for the
# Reachy robot and already carries torch, torchaudio, chatterbox and kokoro —
# so the pipeline's own .venv stays at three packages.
# NOT a sibling module — an EXTERNAL repo, and the distinction is load-bearing.
# `REPO.parent / …` was right while every repo sat in ~/Desktop/code; after the
# 2026-08-26 merge REPO.parent is content-operation, which contains manim (a
# module, so MANIM_ROOT above still resolves) but NOT this. Anchored the way
# FLEET_UI_CSS is: an absolute default for something outside the repo.
# ~/models/voice is a plain name for that env (2026-10-08: a symlink to reachy-chatterbox, so the reorg can move or
# rebuild the env without this file changing again).
VOICE_ROOT = Path(os.environ.get(
    "VOICE_ROOT", Path.home() / "models" / "voice"))
# The score generator's env (Stable Audio 3.0, its own torch). The finish stage shells into its python.
STABLE_AUDIO_ROOT = Path(os.environ.get(
    "STABLE_AUDIO_ROOT", Path.home() / "models" / "stable-audio"))
VOICE_BRIDGE = REPO / "bridges" / "voice_render.py"


def voice_python() -> Path:
    return VOICE_ROOT / "bin" / "python"


def voice_ready() -> bool:
    return voice_python().exists() and VOICE_BRIDGE.exists()


# Image-to-video. A THIRD heavy env, deliberately not the voice one: VOICE_ROOT
# is the Reachy robot's environment, and a modern diffusers in there would mean
# a video experiment could break Benjamin's speech. Separate env, separate
# blast radius.
# The screenplays are authored in the vault and compiled into shotlist.json
# by pipeline/screenplay.py. Read-only from here except for `adopt`, which
# writes id markers into a script once.
VAULT_ROOT = Path(os.environ.get("VAULT_ROOT", Path.home() / "Desktop" / "Obsidian"))

VISUAL_ROOT = Path(os.environ.get(
    "VISUAL_ROOT", Path.home() / "Desktop" / "code" / "visual-gen"))
VISUAL_BRIDGE = VISUAL_ROOT / "i2v_render.py"


def visual_python() -> Path:
    return VISUAL_ROOT / ".venv" / "bin" / "python"


def visual_ready() -> bool:
    return visual_python().exists() and VISUAL_BRIDGE.exists()


def ffmpeg() -> str:
    """Prefer a system ffmpeg; fall back to the Manim env's, which is
    NVENC-capable (h264/hevc/av1) and always present once setup_manim.sh ran."""
    return shutil.which("ffmpeg") or str(MANIM_ENV / "bin" / "ffmpeg")


def ffprobe() -> str:
    return shutil.which("ffprobe") or str(MANIM_ENV / "bin" / "ffprobe")


def video_dir(slug: str) -> Path:
    return VIDEOS / slug


def asset_path(slug: str, beat_id: str) -> Path:
    """A beat's clip. Every visual generator writes here under the same name,
    so downstream nodes can depend on a beat's clip without knowing which
    generator produced it."""
    return video_dir(slug) / "assets" / f"{beat_id}.mp4"


def slugs() -> list[str]:
    """Every video folder holding an authored shotlist.json."""
    if not VIDEOS.exists():
        return []
    return sorted(p.parent.name for p in VIDEOS.glob("*/shotlist.json"))


def video_encoder(cq: int = 19) -> list[str]:
    """ffmpeg video-codec args. NVENC by default (present on the GB10); set
    VIDEO_ENCODER=libx264 to fall back to the CPU when the GPU is contended —
    NVENC needs a CUDA context, and cuCtxCreate fails with OUT_OF_MEMORY when
    other processes hold the unified memory. `cq` maps to x264's crf."""
    enc = os.environ.get("VIDEO_ENCODER", "h264_nvenc")
    if enc == "libx264":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", str(cq)]
    preset = "p6" if cq <= 16 else "p5"
    return ["-c:v", "h264_nvenc", "-preset", preset, "-cq", str(cq)]
