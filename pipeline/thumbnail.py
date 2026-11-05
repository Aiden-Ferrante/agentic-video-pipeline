"""Thumbnail node: an SVG template becomes out/thumbnail.png.

SVG, not a diffusion model. A thumbnail is typography over an image, and
typography is exactly what generators are worst at — a model asked for a title
card returns confident gibberish letterforms. Text is set here; diffusion is
allowed only for a BACKPLATE, behind the words.

No template engine. Jinja2 would be one more dependency in a venv that has
three, to do a string substitution — and the escaping still has to be written
by hand, because a title containing an ampersand breaks the SVG either way.

Rasterised with rsvg-convert out of the Manim env, which is already a hard
dependency of the pipeline and already on this machine.

    "thumbnail": {"title": "Where Bayes comes from", "kicker": "PROBABILITY",
                  "backplate": "b03"}
"""
import subprocess
from xml.sax.saxutils import escape

from . import beats, config, imagery, library

WIDTH, HEIGHT = 1280, 720
BG = "#0d1117"
INK = "#f5efe6"
ACCENT = "#c8613a"

# YouTube renders a thumbnail as small as ~210px wide in a sidebar, so the
# title has to survive a 6x reduction. Few words, set as large as will fit.
MARGIN = 84
MAX_LINES = 3
SIZE_LADDER = (108, 96, 86, 76, 68, 60, 54)
# Mean advance per character for DejaVu Sans Bold, as a fraction of the font
# size. Estimated rather than measured: SVG has no text metrics without a
# layout engine, and the alternative is shelling out to rasterise trial
# layouts. The ladder below leaves enough slack to absorb the error.
ADVANCE = 0.66


def thumbnail_path(slug: str):
    return config.video_dir(slug) / "out" / "thumbnail.png"


def svg_path(slug: str):
    return config.video_dir(slug) / "out" / "thumbnail.svg"


def spec(slug: str) -> dict:
    doc = beats.load(slug)
    block = dict(doc.get("thumbnail") or {})
    block.setdefault("title", doc.get("title", slug))
    return block


def _wrap_to(words: list[str], limit: int) -> list[str]:
    out, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            out.append(current)
            current = word
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def layout(title: str) -> tuple[list[str], int]:
    """(lines, font_size) — the largest setting of this title that fits.

    Breaking on a fixed character count and then setting the result at a fixed
    size is what overflowed the frame: 22 characters is one short line by count
    and 1200 pixels wide at 92pt. The width available is what decides both.
    """
    words = title.split()
    available = WIDTH - 2 * MARGIN
    for size in SIZE_LADDER:
        per_line = max(int(available / (size * ADVANCE)), 1)
        lines = _wrap_to(words, per_line)
        if len(lines) <= MAX_LINES and all(len(l) <= per_line for l in lines):
            return lines, size

    # Nothing on the ladder fits. Usually one unbreakable word — a long
    # compound or a URL — which no wrap can help, so size to that word instead
    # of falling through and letting it run off the canvas.
    longest = max((len(w) for w in words), default=1)
    size = max(int(available / (longest * ADVANCE)), 1)
    lines = _wrap_to(words, max(int(available / (size * ADVANCE)), 1))[:MAX_LINES]
    if size < SIZE_LADDER[-1]:
        print(f"  [warn] {title!r} only fits at {size}px — unreadable at sidebar "
              f"size. A thumbnail title wants three or four short words.")
    return lines, size


def _backplate(slug: str, block: dict) -> str:
    """An <image> element for the backplate, or nothing.

    A frame from the video itself is the honest default — it shows what the
    viewer is actually going to get.
    """
    source = None
    if block.get("backplate"):
        beat = block["backplate"]
        still = imagery.still_path(slug, beat)
        if still.exists():
            source = still
        else:
            frame = config.video_dir(slug) / "out" / "backplate.png"
            clip = config.asset_path(slug, beat)
            if clip.exists():
                from . import assemble
                # Midpoint, not a fixed offset: a second in, a Manim scene is
                # usually still drawing itself, and a half-written title reads
                # as a rendering artefact rather than a backdrop.
                at = max(assemble.duration(clip) / 2, 0.1)
                r = subprocess.run(
                    [config.ffmpeg(), "-y", "-v", "error", "-ss", f"{at:.2f}",
                     "-i", str(clip), "-frames:v", "1", "-update", "1", str(frame)],
                    capture_output=True, text=True)
                source = frame if r.returncode == 0 and frame.exists() else None
    if source is None:
        return ""
    return (f'<image href="{escape(str(source.resolve()))}" x="0" y="0" '
            f'width="{WIDTH}" height="{HEIGHT}" preserveAspectRatio="xMidYMid slice" '
            f'opacity="0.6"/>')


def build(slug: str) -> bool:
    block = spec(slug)
    lines, size = layout(block["title"])
    if not lines:
        print(f"[{slug}] no thumbnail title")
        return False

    top = HEIGHT / 2 - (len(lines) - 1) * size * 0.62 + 12
    kicker = block.get("kicker", "")

    rows = "".join(
        f'<text x="{MARGIN}" y="{top + i * size * 1.24:.0f}" font-family="DejaVu Sans" '
        f'font-size="{size}" font-weight="bold" fill="{INK}">{escape(line)}</text>'
        for i, line in enumerate(lines))
    kicker_el = (
        f'<text x="{MARGIN}" y="150" font-family="DejaVu Sans" font-size="34" '
        f'font-weight="bold" letter-spacing="6" fill="{ACCENT}">{escape(kicker.upper())}</text>'
        if kicker else "")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
  <rect width="{WIDTH}" height="{HEIGHT}" fill="{BG}"/>
  {_backplate(slug, block)}
  <rect width="{WIDTH}" height="{HEIGHT}" fill="{BG}" opacity="0.45"/>
  {kicker_el}
  {rows}
  <rect x="{MARGIN}" y="{HEIGHT - 132}" width="150" height="9" fill="{ACCENT}"/>
</svg>"""

    out_svg = svg_path(slug)
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text(svg)

    rasteriser = config.MANIM_ENV / "bin" / "rsvg-convert"
    if not rasteriser.exists():
        print(f"[{slug}] no rsvg-convert at {rasteriser} — SVG written, not rasterised")
        return False
    dest = thumbnail_path(slug)
    r = subprocess.run(
        [str(rasteriser), "-w", str(WIDTH), "-h", str(HEIGHT),
         "-o", str(dest), str(out_svg)], capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"[{slug}] rasterise FAILED\n{r.stderr[-400:]}")
        return False

    library.write(
        dest, library="imagery",
        rights_block=library.rights("CC0-1.0", title=block["title"],
                                    attribution_required=False),
        provenance_block=library.provenance(
            "rsvg-convert", source_type="algorithm",
            inputs={"title": block["title"], "kicker": kicker,
                    "backplate": block.get("backplate", "")}),
        selected=True,
    )
    print(f"[{slug}] thumbnail: {len(lines)} lines -> {dest.relative_to(config.REPO)}")
    return True
