"""Animation node: a beat with visual.kind == "animation" becomes one clip.

Reads only its own beat fragment. Emits assets/<beat_id>.mp4. Never looks at
another generator's output — that constraint is what lets animation, imagery
and sound run concurrently.

The render itself is delegated to manim_render.py in the Manim repo, which is
stdlib-only and shells out to the isolated micromamba env. We do not fork it.
"""
import ast
import json
import shutil
import sys
from pathlib import Path

from . import config, library


def _manim_render():
    """Import the carried-over bridge lazily, so this module stays importable
    on a box where the Manim env was never built."""
    root = str(config.MANIM_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import manim_render
    return manim_render


def scene_source(beat: dict) -> Path | None:
    """The .py the clip is rendered from, or None if the beat never named one.

    Returns rather than raises: this runs during task ENUMERATION, so a single
    beat missing `scene_file` would otherwise stop doit from listing any task
    at all. An authoring typo must fail its own beat at render time, where the
    error names the beat, and leave the rest of the build reachable.
    """
    from . import beats
    name = beats.visual_spec(beat).get("scene_file")
    return config.MANIM_ROOT / "scenes" / name if name else None


def scene_sources(beat: dict) -> list[Path]:
    """The scene file plus every local module it transitively imports.

    Depending only on the scene file was a correctness bug: all 55 scenes do
    `from scm_style import ...`, so editing the shared visual language — the
    colours, fonts and SCMScene base class — changed every render while
    invalidating nothing. doit would report everything fresh and quietly serve
    stale clips.

    Only modules resolving to a file under MANIM_ROOT are followed; manim
    itself and the stdlib are pinned by the environment, not by us.
    """
    root = config.MANIM_ROOT
    seen: set[Path] = set()
    src = scene_source(beat)
    stack = [src] if src else []
    while stack:
        path = stack.pop()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                for cand in (root / f"{top}.py", root / "scenes" / f"{top}.py"):
                    if cand.exists():
                        stack.append(cand)
    return sorted(seen)


def render(slug: str, beat_id: str) -> bool:
    from . import beats
    frag = json.loads(beats.fragment_path(slug, beat_id, "animation").read_text())
    fps = frag["fps"]
    width, height = frag["resolution"]
    vis = frag["visual"]

    dest = config.asset_path(slug, beat_id)
    if beats.locked(vis, dest):
        print(f"[{slug}/{beat_id}] LOCKED — kept the existing clip, nothing rendered")
        return True

    mr = _manim_render()
    ok, produced, dur, log = mr.render_scene(
        vis["scene_file"], vis["scene"],
        width=width, height=height, fps=fps,
        quality=vis.get("quality", "h"),
    )
    if not ok:
        print(f"[{slug}/{beat_id}] render FAILED\n{log}")
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced, dest)

    # Provenance is captured here or not at all — reconstructing it later is
    # the documented failure mode in every one of the five media.
    library.write(
        dest,
        library="animation",
        rights_block=library.rights(
            "CC0-1.0", title=vis["scene"], creator="", attribution_required=False),
        provenance_block=library.provenance(
            "manim", tool_version=mr.manim_version(), source_type="algorithm",
            stem="visuals",
            inputs={"scene_file": vis["scene_file"], "scene": vis["scene"],
                    "sources": [str(p.relative_to(config.MANIM_ROOT))
                                for p in scene_sources({"visual": vis})]}),
        selected=True,
        duration_seconds=dur,
    )
    print(f"[{slug}/{beat_id}] {dur}s -> {dest.relative_to(config.REPO)}")
    return True
