"""Memoryline node: a beat with visual.kind == "memoryline" becomes one clip.

The style lives in the memory-line skill's kit (content-operation/.claude/skills/
memory-line/kit), scoped to the Machine Learning series. A beat names a kit shot
and its params:

    "visual": {"kind": "memoryline", "shot": "shots/four_slots_act1.py:Line",
               "params": {...}}

ONE DELIBERATE DEPARTURE from the animation node's "own fragment only" rule: a
memory-line shot is timed to its narration — the dust settles on the word, the
number appears when it is said — so this node also reads the beat's alignment
(or, timed from the script, its read-time estimate), through the same authority
fit uses. The clip renders PAD seconds longer than that, so fit only ever cuts:
held last frames are fatal on moving line art.
"""
import json
import subprocess
import tempfile
from pathlib import Path

from . import align, config, library

PAD = 1.0


def kit_sources() -> list[Path]:
    """Every kit module a shot can import. Coarse on purpose: the kit is small and a
    change to its shared drawing code (boil, memory, glow) changes every shot."""
    kit = config.MEMORYLINE_KIT
    if not kit.exists():
        return []
    return sorted(p for p in kit.rglob("*.py") if "variants" not in p.parts and "__pycache__" not in p.parts)


def _kit_dirs(kit: Path) -> list[Path]:
    return [kit, *sorted(d for d in kit.iterdir() if d.is_dir() and d.name not in ("variants", "__pycache__", "data", "fonts"))]


def _closure(path: Path, kit: Path, seen: set) -> None:
    """`path` and every kit module it reaches: its imports, and string literals naming a .py file (layered.py and
    the layouts load modules by path). Over-inclusion costs a re-render; under-inclusion a stale clip."""
    import ast
    if path in seen or not path.is_file():
        return
    seen.add(path)
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError, ValueError):
        return
    names, refs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and ".py" in node.value:
            refs.add(node.value.split(":")[0])
    for n in names:
        # Python's own order for these modules (each puts its folder and the kit root on sys.path): own folder,
        # kit root, then any other kit folder only if neither has it. First match wins, as at import time.
        for d in (path.parent, kit, *_kit_dirs(kit)):
            if (d / f"{n}.py").is_file():
                _closure(d / f"{n}.py", kit, seen)
                break
    for r in refs:
        for base in (kit, path.parent):
            _closure((base / r).resolve(), kit, seen)


def kit_sources_for(visual: dict) -> list[Path]:
    """The kit modules ONE beat's render can reach — its entry script, the shot/typeset files its spec names, and
    their import closure. Replaces kit_sources() as the memoryline dep (2026-10-08): editing one video's typeset
    file re-rendered ~40 beats; now it re-renders the beats that use it."""
    kit = config.MEMORYLINE_KIT
    if not kit.exists():
        return []
    shot = str(visual.get("shot", ""))
    entry = kit / ("fusion/beat.py" if shot.startswith("fusion") else "layered.py" if shot.startswith("layered")
                   else "beat.py")
    seen: set = set()
    _closure(entry, kit, seen)
    if shot.startswith("fusion"):
        for f in (kit / "fusion").glob("*.py"):
            _closure(f, kit, seen)

    def strings(v):
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for x in v.values():
                yield from strings(x)
        elif isinstance(v, list):
            for x in v:
                yield from strings(x)
    for v in strings(visual):
        if ".py" in v:
            _closure((kit / v.split(":")[0]).resolve(), kit, seen)
    return sorted(seen)


def seconds_for(slug: str, beat_id: str, visual: dict) -> float:
    """The narrated length, decided exactly as fit.target_seconds decides it — minus
    fit's last fallback (the render's own length), which does not exist yet here."""
    from . import beats, reading
    mode = beats.timing(slug)
    if mode["mode"] != "estimate":
        spoken = align.spoken_seconds(slug, beat_id)
        if spoken > 0:
            return spoken + mode["settle"] + beats.beat_settle(slug, beat_id)
    if mode["mode"] != "voice":
        frag = beats.fragment_path(slug, beat_id, "vo")
        if frag.exists():
            est = reading.estimate(json.loads(frag.read_text()).get("vo_text", ""), mode["pace"])
            if est > 0:
                return est + mode["settle"] + beats.beat_settle(slug, beat_id)
    return float(visual.get("seconds", 6.0))


def words_for(slug: str, beat_id: str) -> list[dict]:
    """Word timings in CLIP time: the clip starts where the kept speech window starts."""
    doc, window = align.load(slug, beat_id), align.speech_window(slug, beat_id)
    if not doc or not window:
        return []
    s0 = window[0]
    return [{"word": w["word"], "start": round(w["start"] - s0, 3), "end": round(w["end"] - s0, 3)}
            for w in doc.get("words", [])]


def chain(slug: str, beat_id: str, visual: dict) -> tuple[float, list[dict]]:
    """A continuation beat (`params.clock_after`: the earlier beats it carries on from, in order) renders a LATER
    WINDOW of one timeline instead of starting its shot over (2026-10-09, the human: visuals that "restart" at a
    cut should look seamless). Its clock starts where those beats' on-screen time ends, and it sees their words too,
    shifted onto the same clock — so it can carry the first beat's params unchanged and add its own cues."""
    from . import fit
    words, clock = [], 0.0
    for prev in (visual.get("params") or {}).get("clock_after") or []:
        words += [{**w, "start": round(w["start"] + clock, 3), "end": round(w["end"] + clock, 3)} for w in words_for(slug, prev)]
        clock += fit.target_seconds(slug, prev)
    words += [{**w, "start": round(w["start"] + clock, 3), "end": round(w["end"] + clock, 3)} for w in words_for(slug, beat_id)]
    return round(clock, 3), words


def chain_deps(slug: str, visual: dict) -> list:
    return [align.align_path(slug, b) for b in (visual.get("params") or {}).get("clock_after") or []]


def render(slug: str, beat_id: str) -> bool:
    from . import assemble, beats
    frag = json.loads(beats.fragment_path(slug, beat_id, "memoryline").read_text())
    vis = frag["visual"]
    dest = config.asset_path(slug, beat_id)
    if beats.locked(vis, dest):
        print(f"[{slug}/{beat_id}] LOCKED — kept the existing clip, nothing rendered")
        return True
    if not vis.get("shot"):
        print(f"[{slug}/{beat_id}] memoryline beat names no `shot`")
        return False
    kit = config.MEMORYLINE_KIT
    seconds = round(seconds_for(slug, beat_id, vis) + PAD, 3)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wj, pj = Path(tmp) / "words.json", Path(tmp) / "params.json"
        clock, words = chain(slug, beat_id, vis)
        params = dict(vis.get("params") or {})
        if clock:
            params["clock"] = clock
            if vis["shot"].startswith("layered"):              # a layered beat's two layers share the clock
                for k in ("drawn_params", "type_params"):
                    params[k] = {**(params.get(k) or {}), "clock": clock}
        wj.write_text(json.dumps(words))
        pj.write_text(json.dumps(params))
        # A Triptych beat (shot "fusion:…") renders three layers on one clock; everything else is one kit shot.
        # A layered beat ("layered") is a drawn layer under a typeset Manim layer on the same word clock.
        entry = kit / ("fusion/beat.py" if vis["shot"].startswith("fusion")
                       else "layered.py" if vis["shot"].startswith("layered") else "beat.py")
        cmd = [str(config.MANIM_ENV / "bin" / "python"), str(entry), "--shot", vis["shot"],
               "--seconds", str(seconds), "--out", str(dest), "--words", str(wj), "--params", str(pj),
               "--workers", str(config.memoryline_workers())]
        r = subprocess.run(cmd, cwd=kit, capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"[{slug}/{beat_id}] memoryline render FAILED\n{(r.stderr or r.stdout)[-1500:]}")
        return False
    dur = assemble.duration(dest)
    library.write(
        dest, library="animation",
        rights_block=library.rights("CC0-1.0", title=vis["shot"], creator="", attribution_required=False),
        provenance_block=library.provenance(
            "memoryline", tool_version="kit", source_type="algorithm", stem="visuals",
            inputs={"shot": vis["shot"], "params": vis.get("params") or {}, "seconds": seconds,
                    "sources": [str(p.relative_to(kit)) for p in kit_sources()]}),
        selected=True, duration_seconds=dur)
    print(f"[{slug}/{beat_id}] {dur}s memoryline -> {dest.relative_to(config.REPO)}")
    return True
