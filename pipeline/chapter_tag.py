"""A persistent corner tag naming the part of the section currently on screen.

The TOC card at the top of a module and the divider cards between its parts tell
the viewer where they are twice, and then never again — for the eight or ten
minutes that actually matter they are on their own. This puts the same
information in the corner permanently: the module, and the module's parts with
the live one lit.

Two rules it follows:

  It asks the TIMELINE for its spans, never the shot list's beat order. A
  reorder or a trim moves every beat after it, and `assemble.beat_spans` is the
  one place that knows that (assemble.py:66).

  It is drawn ONCE PER PART, not once per beat. A tag that re-rendered per beat
  would be 404 identical PNGs and 404 overlay filters; there are three or four
  distinct states in a module, so there are three or four images.

Rendered as transparent PNGs and composited by ffmpeg with a per-state
`enable=between(t,...)`, so it costs one filter per part and never touches a
scene file.
"""
from __future__ import annotations
import json, subprocess
from pathlib import Path

from . import assemble, beats, config

# The tag sits top-RIGHT because `revamp_kit.caption` and the original scene
# files both occupy the lower-left corner.
MARGIN = 46
TITLE_PT = 27
PART_PT = 20
LINE = 30
LIT = "#f0eee6"
DIM = "#b8a99a"
ACCENT = "#cc785c"
DIM_ALPHA = 0.62
FONT = str(Path.home() / ".fonts")


def _font_file() -> str:
    """The channel's serif, so the tag matches the drawings rather than the OS."""
    for pat in ("channel-*.ttf", "channel-*.otf", "CrimsonText*.ttf"):
        hit = sorted(Path(FONT).glob(pat))
        if hit:
            return str(hit[0])
    return ""


def states(slug: str) -> list[dict]:
    """One entry per contiguous run of beats sharing a chapter.

    Returns [{chapter, index, start, end}] over the FINISHED timeline. Beats
    with no chapter (the TOC and divider cards sit outside the parts) inherit
    the run they fall inside, so the tag never blinks off mid-module.
    """
    doc = beats.effective(slug)
    chapter_of = {b["id"]: b.get("chapter") for b in doc["beats"]}
    order = [c for c in (b.get("chapter") for b in doc["beats"]) if c]
    parts: list[str] = []
    for c in order:
        if c not in parts:
            parts.append(c)

    spans = assemble.beat_spans(slug)
    if not spans:
        return []

    out: list[dict] = []
    current = None
    for beat, start, dur in spans:
        ch = chapter_of.get(beat) or current
        if ch is None:
            continue                      # nothing before the first chapter yet
        if out and ch == current:
            out[-1]["end"] = start + dur
        else:
            out.append({"chapter": ch, "index": parts.index(ch),
                        "start": start, "end": start + dur})
        current = ch
    # The first run starts where the first PART starts, not at zero. Before any
    # chapter there is no part, and a tag naming one would be lying — 1.1 opens
    # on a wordless cold open that predates the section entirely. For every
    # other module beat one is already chaptered, so this changes nothing there.
    if out:
        out[-1]["end"] = spans[-1][1] + spans[-1][2]
    return out, parts


def _draw(png: Path, number: str, title: str, parts: list[str], live: int,
          width: int, height: int) -> bool:
    """One transparent PNG: the module line, a rule, and the parts list."""
    font = _font_file()
    fontarg = f":fontfile='{font}'" if font else ""
    x = f"w-tw-{MARGIN}"                      # right-aligned, per line
    filters = [f"color=c=black@0:s={width}x{height}:d=1[bg]"]
    y = MARGIN
    head = f"{number}  ·  {title}"
    chain = (f"drawtext=text='{_esc(head)}':fontsize={TITLE_PT}:fontcolor={LIT}"
             f"{fontarg}:x={x}:y={y}")
    y += TITLE_PT + 22
    for i, p in enumerate(parts):
        colour = ACCENT if i == live else f"{DIM}@{DIM_ALPHA}"
        size = PART_PT + (2 if i == live else 0)
        chain += (f",drawtext=text='{_esc(f'{i + 1}.  {p}')}':fontsize={size}"
                  f":fontcolor={colour}{fontarg}:x={x}:y={y}")
        y += LINE
    filters.append(f"[bg]{chain}[out]")
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c=black@0:s={width}x{height}:d=1,format=rgba",
         "-vf", chain, "-frames:v", "1", str(png)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  tag draw failed: {r.stderr.strip()[-300:]}")
    return png.exists()


def _esc(s: str) -> str:
    """drawtext's own escaping: colons split options, quotes end the literal."""
    return (s.replace("\\", "\\\\").replace(":", "\\:")
             .replace("'", "’").replace("%", "\\%"))


def render(slug: str) -> list[dict]:
    """Draw one PNG per part. Returns the states, each with a `png` path."""
    doc = beats.effective(slug)
    width, height = doc.get("resolution", [1920, 1080])
    st, parts = states(slug)
    if not st:
        return []
    number, title = _label(slug, doc)
    outdir = config.video_dir(slug) / "out" / "tag"
    outdir.mkdir(parents=True, exist_ok=True)
    seen: dict[int, Path] = {}
    for s in st:
        i = s["index"]
        if i not in seen:
            png = outdir / f"part{i + 1}.png"
            if not _draw(png, number, title, parts, i, width, height):
                return []
            seen[i] = png
        s["png"] = seen[i]
    return st


def _label(slug: str, doc: dict) -> tuple[str, str]:
    """'1.3' and 'Classical Logic'.

    The registry owns module numbering, so it is asked first; the shot list's
    thumbnail kicker ('Logic · 1.3') is the fallback, and the slug the last
    resort. Never hard-code the numbering here — a reorder would silently
    mislabel every frame of the video.
    """
    # Authored wins. A module's own title is chosen for a YouTube listing and
    # is not always what should sit in a corner for ten minutes — origins is
    # "How the Universe Learned to Look at Itself" on the thumbnail and "From
    # Experience to Formal System" in the series.
    spec = doc.get("chapter_tag") or {}
    if spec.get("number") or spec.get("title"):
        return (spec.get("number") or slug), (spec.get("title") or doc.get("title") or slug)
    kicker = ((doc.get("thumbnail") or {}).get("kicker") or "").strip()
    number = ""
    if "·" in kicker:
        number = kicker.split("·")[-1].strip()
    title = (doc.get("title") or slug).strip()
    # Only SHORTEN what does not fit. Splitting on the first comma turned
    # "Assumptions, Tautologies and Axioms" into "Assumptions", which names the
    # module worse than the slug does.
    short = title
    if len(short) > 34:
        for cut in (" as the ", " and the ", ": "):
            if cut in short:
                short = short.split(cut)[0]
                break
    return number or slug, short.strip()


def overlay_args(states_: list[dict]) -> tuple[list[str], str]:
    """ffmpeg inputs and a filtergraph that lays each part's tag over its span."""
    inputs: list[str] = []
    graph, last = [], "0:v"
    # `-loop 1` on a still is an INFINITE input. overlay keeps pulling from it,
    # so the encode never ends: the first attempt wrote a 33 GB file over 26
    # hours for a ten-minute video. Bound every looped input with -t, and the
    # muxer with -shortest, so the output can only be as long as the picture.
    total = max(s["end"] for s in states_)
    for n, s in enumerate(states_, start=1):
        inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(s["png"])]
        label = f"v{n}"
        graph.append(f"[{last}][{n}:v]overlay=0:0:"
                     f"enable='between(t,{s['start']:.3f},{s['end']:.3f})'[{label}]")
        last = label
    return inputs, ";".join(graph) + f";[{last}]null[tagged]" if graph else ("", "")
