"""Fixtures that build a throwaway video folder and point the pipeline at it.

Every test gets its own VIDEOS root. `pipeline.config` resolves paths from
module-level constants, so the fixture monkeypatches those rather than the
environment — an env var would only be read at import time and would leak
between tests.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config  # noqa: E402


def shotlist(*beats, fps=30, resolution=(1920, 1080), title="Fixture"):
    return {
        "slug": "fx", "title": title, "fps": fps,
        "resolution": list(resolution), "beats": list(beats),
    }


def animation_beat(bid, scene_file="a.py", scene="A", vo="narration here"):
    return {"id": bid, "vo_text": vo,
            "visual": {"kind": "animation", "scene_file": scene_file,
                       "scene": scene, "quality": "l"}}


def imagery_beat(bid, prompt="a portrait", vo="narration here"):
    return {"id": bid, "vo_text": vo,
            "visual": {"kind": "imagery", "prompt": prompt}}


def memoryline_beat(bid, shot="shots/x.py:X", vo="narration here"):
    return {"id": bid, "vo_text": vo, "visual": {"kind": "memoryline", "shot": shot}}


def brief_beat(bid, brief="a gate crushes a formula", vo="narration here"):
    return {"id": bid, "vo_text": vo, "brief": brief, "visual": {"kind": "brief"}}


@pytest.fixture
def videos(tmp_path, monkeypatch):
    """An isolated VIDEOS root. Returns a helper that authors a shotlist."""
    root = tmp_path / "videos"
    root.mkdir()
    monkeypatch.setattr(config, "VIDEOS", root)

    def author(doc, slug="fx"):
        d = root / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "shotlist.json").write_text(json.dumps(doc, indent=2) + "\n")
        return d

    author.root = root
    return author


@pytest.fixture
def manim_root(tmp_path, monkeypatch):
    """A fake Manim repo: scenes/ plus a shared style module they import.

    Mirrors the real layout that broke once — all 55 scenes do
    `from scm_style import ...`, so the import closure is what makes editing
    the shared visual language invalidate anything at all.
    """
    root = tmp_path / "manim"
    scenes = root / "scenes"
    scenes.mkdir(parents=True)
    (root / "scm_style.py").write_text("PALETTE = 'dark'\n")
    (root / "helpers.py").write_text("from scm_style import PALETTE\n")
    (scenes / "a.py").write_text("from scm_style import PALETTE\nclass A: pass\n")
    (scenes / "b.py").write_text("from helpers import *\nclass B: pass\n")
    (scenes / "lonely.py").write_text("class L: pass\n")
    monkeypatch.setattr(config, "MANIM_ROOT", root)
    return root
