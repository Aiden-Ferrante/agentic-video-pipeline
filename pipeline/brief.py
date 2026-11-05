"""Brief node: a beat whose picture is not yet made becomes a typeset card.

`visual.kind: "brief"` is what the script compiler emits for a new beat: the
words exist, the picture is a sentence of direction. This node turns that
sentence into a clip so the animatic BUILDS — fit cuts it to the narration,
the timeline places it, the master renders — and a person can watch the whole
module with holes that say what belongs in them, rather than not watch it.

It is deliberately plain: the house ground, the beat id, the section, and the
brief set in the thumbnail's type. Anything more would tempt someone to ship
it. The card is rasterised with the same rsvg-convert the thumbnail uses and
looped to length by the same ffmpeg the fit node uses, so this node adds no
dependency.
"""
import json
import subprocess
from xml.sax.saxutils import escape

from . import beats, config, library

INK = "#f5efe6"
ACCENT = "#c8613a"
MUTED = "#8a8078"
NOMINAL_SECONDS = 5.0
MARGIN = 160
SIZE = 44
LINE = 1.35
ADVANCE = 0.52          # DejaVu Sans regular, mean advance per em


def png_path(slug: str, beat_id: str):
    return config.video_dir(slug) / "assets" / f"{beat_id}.brief.png"


def _wrap(text: str, width_px: int, size: int) -> list[str]:
    limit = max(8, int(width_px / (size * ADVANCE)))
    out, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if cur and len(cand) > limit:
            out.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


def svg(frag: dict) -> str:
    w, h = frag.get("resolution", [1920, 1080])
    bg = "#" + str(frag.get("letterbox", "0x0d1117")).replace("0x", "")
    text = frag.get("brief") or "(no direction written yet)"
    lines = _wrap(text, w - 2 * MARGIN, SIZE)[:9]
    top = h / 2 - (len(lines) - 1) * SIZE * LINE / 2
    rows = "".join(
        f'<text x="{MARGIN}" y="{top + i * SIZE * LINE:.0f}" font-family="DejaVu Sans" '
        f'font-size="{SIZE}" fill="{INK}">{escape(l)}</text>' for i, l in enumerate(lines))
    section = escape(frag.get("section", ""))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <rect width="{w}" height="{h}" fill="{bg}"/>
  <text x="{MARGIN}" y="{MARGIN}" font-family="DejaVu Sans" font-size="28" font-weight="bold"
        letter-spacing="6" fill="{ACCENT}">BRIEF</text>
  <text x="{w - MARGIN}" y="{MARGIN}" font-family="DejaVu Sans" font-size="28"
        fill="{MUTED}" text-anchor="end">{escape(frag["id"])}</text>
  {rows}
  <text x="{MARGIN}" y="{h - MARGIN}" font-family="DejaVu Sans" font-size="26" fill="{MUTED}">{section}</text>
  <rect x="{MARGIN}" y="{h - MARGIN + 24}" width="120" height="6" fill="{ACCENT}"/>
</svg>"""


def render(slug: str, beat_id: str) -> bool:
    frag = json.loads(beats.fragment_path(slug, beat_id, "brief").read_text())
    dest = config.asset_path(slug, beat_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    png = png_path(slug, beat_id)
    svg_file = png.with_suffix(".svg")
    svg_file.write_text(svg(frag))

    rasteriser = config.MANIM_ENV / "bin" / "rsvg-convert"
    if not rasteriser.exists():
        print(f"[{slug}/{beat_id}] no rsvg-convert at {rasteriser}")
        return False
    w, h = frag.get("resolution", [1920, 1080])
    r = subprocess.run([str(rasteriser), "-w", str(w), "-h", str(h), "-o", str(png),
                        str(svg_file)], capture_output=True, text=True)
    if r.returncode != 0 or not png.exists():
        print(f"[{slug}/{beat_id}] rasterise FAILED\n{r.stderr[-400:]}")
        return False
    svg_file.unlink()

    seconds = float(frag.get("seconds") or NOMINAL_SECONDS)
    fps = frag.get("fps", 30)
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-loop", "1", "-framerate", str(fps),
         "-i", str(png), "-t", f"{seconds:.3f}", "-an",
         "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-pix_fmt", "yuv420p",
         str(dest)], capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"[{slug}/{beat_id}] card encode FAILED\n{r.stderr[-400:]}")
        return False

    library.write(
        dest, library="animation",
        rights_block=library.rights("CC0-1.0", title=f"brief {beat_id}",
                                    attribution_required=False),
        provenance_block=library.provenance(
            "brief-card", source_type="algorithm", stem="visuals",
            inputs={"brief": frag.get("brief", ""), "section": frag.get("section", "")}),
        selected=True, duration_seconds=seconds,
    )
    print(f"[{slug}/{beat_id}] brief card {seconds:.1f}s -> {dest.relative_to(config.REPO)}")
    return True
