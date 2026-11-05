"""Shorts node: a contiguous run of beats becomes a vertical video.

Re-render, never crop. A Manim scene is composed to its frame — the equation is
centred, the graph fills the width — so a 9:16 crop of a 16:9 render cuts the
subject in half. Manim renders at any resolution from the same source, so the
right move is to ask for 1080x1920 and let the scene lay itself out again.
Stills are re-letterboxed for the same reason.

Nothing here searches for a good moment. Every open-source Shorts tool exists
to infer structure from an unstructured podcast, and this pipeline already has
it: the shot list says where each idea starts and stops. Selecting a Short is a
query, so it is authored, not guessed:

    "shorts": [{"name": "bayes-hook", "beats": ["b01", "b02"]}]

Captions are burned in rather than shipped as a sidecar file, because vertical
video is watched muted.

Caveat worth knowing: a scene authored for 16:9 will re-lay-out at 9:16, not
just re-frame. Wide content gets small. Beats that matter as Shorts are worth
a scene written for the aspect.
"""
import json
import subprocess

from . import align, assemble, beats, captions, config, library, voice

WIDTH, HEIGHT = 1080, 1920
MAX_SECONDS = 60.0


def out_dir(slug: str):
    return config.video_dir(slug) / "out" / "shorts"


def short_path(slug: str, name: str):
    return out_dir(slug) / f"{name}.mp4"


def specs(slug: str) -> list[dict]:
    return beats.load(slug).get("shorts") or []


def _clip_for(slug: str, beat: dict, fps: int, work) -> tuple[bool, object]:
    """Render one beat at 9:16, fitted to its narration."""
    bid = beat["id"]
    dest = work / f"{bid}.mp4"
    want = align.spoken_seconds(slug, bid)
    # Read through the accessor so a stems-form beat renders identically —
    # this switch and CLIP_KINDS must never fork again.
    visual = beats.visual_spec(beat)
    kind = visual.get("kind", "")

    if kind == "animation":
        from . import animation
        mr = animation._manim_render()
        ok, produced, _dur, log = mr.render_scene(
            visual["scene_file"], visual["scene"],
            width=WIDTH, height=HEIGHT, fps=fps,
            quality=visual.get("quality", "h"))
        if not ok:
            print(f"  {bid}: vertical render FAILED\n{log}")
            return False, None
        source, filters = produced, []
    elif kind == "memoryline":
        # Rendered 16:9 at its narrated length; the reframe below letterboxes it.
        source, filters = config.asset_path(slug, bid), []
        if not source.exists():
            print(f"  {bid}: no memoryline clip to reframe")
            return False, None
    elif kind == "imagery":
        from . import imagery
        source = imagery.still_path(slug, bid)
        if not source.exists():
            print(f"  {bid}: no still to reframe")
            return False, None
        filters = ["loop=loop=-1:size=1:start=0"]
    else:
        print(f"  {bid}: nothing to render for kind {kind!r}")
        return False, None

    filters += [f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease",
                f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=0x0d1117",
                "setsar=1", "format=yuv420p"]
    if want > 0:
        filters.append(f"tpad=stop_mode=clone:stop_duration={want:.3f}")

    args = [config.ffmpeg(), "-y", "-v", "error", "-i", str(source),
            "-vf", ",".join(filters)]
    if want > 0:
        args += ["-t", f"{want:.3f}"]
    args += ["-r", str(fps), "-an", "-c:v", "h264_nvenc", "-preset", "p5",
             "-cq", "20", str(dest)]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"  {bid}: reframe FAILED\n{r.stderr[-400:]}")
        return False, None
    return True, dest


def _narration(slug: str, ids: list[str], fps: int, work, total: float):
    """The same voice tracks, laid end to end for just these beats."""
    inputs, filters, labels, at = [], [], [], 0.0
    for bid in ids:
        wav = voice.spoken_path(slug, bid)
        window = align.speech_window(slug, bid)
        if not wav.exists() or not window:
            at += align.spoken_seconds(slug, bid)
            continue
        offset, length = window
        i = len(labels)
        inputs += ["-i", str(wav)]
        filters.append(f"[{i}:a]atrim=start={offset:.3f}:duration={length:.3f},"
                       f"asetpts=PTS-STARTPTS,adelay={int(at * 1000)}:all=1[a{i}]")
        labels.append(f"[a{i}]")
        at += length
    if not labels:
        return None

    dest = work / "voice.wav"
    graph = (";".join(filters) + ";" + "".join(labels) +
             f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,"
             f"apad,atrim=duration={total:.3f}[out]")
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", *inputs, "-filter_complex", graph,
         "-map", "[out]", "-ar", str(voice.RATE), "-ac", "1",
         "-c:a", "pcm_s16le", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  narration FAILED\n{r.stderr[-400:]}")
        return None
    normalised = work / "voice.norm.wav"
    return normalised if voice.normalize_track(dest, normalised) else None


def _subtitles(slug: str, ids: list[str], work):
    """Captions rebased to the Short's own clock, sized for a phone."""
    lines, at = [], 0.0
    for bid in ids:
        doc = align.load(slug, bid)
        window = align.speech_window(slug, bid)
        length = align.spoken_seconds(slug, bid)
        if doc and doc.get("words") and window:
            shift = at - window[0]
            spoken = next((beats.narration_text(b) for b in beats.load(slug)["beats"]
                           if b["id"] == bid), "").split()
            original = [w for w in spoken if any(c.isalnum() for c in w)]
            for i, word in enumerate(doc["words"]):
                lines.append({"beat": bid,
                              "text": original[i] if i < len(original) else word["word"].title(),
                              "start": word["start"] + shift, "end": word["end"] + shift})
        at += length

    grouped = captions._group(lines)
    if not grouped:
        return None
    # Bigger type, higher off the bottom: a phone's UI chrome sits over the
    # lower ~15% and the video is usually watched one-handed.
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{captions.FONT},64,&H00FFFFFF,&H8C101010,&H8C101010,1,0,3,8,0,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = "".join(
        f"Dialogue: 0,{captions._ass_time(l['start'])},{captions._ass_time(l['end'])},"
        f"Default,,0,0,0,,{captions._wrap(l['text'])}\n" for l in grouped)
    dest = work / "captions.ass"
    dest.write_text(header + events)
    return dest


def build(slug: str, name: str) -> bool:
    spec = next((s for s in specs(slug) if s["name"] == name), None)
    if spec is None:
        print(f"[{slug}] no short named {name!r} in the shot list")
        return False

    doc = beats.load(slug)
    fps = doc.get("fps", 30)
    by_id = {b["id"]: b for b in doc.get("beats", [])}
    ids = [b for b in spec["beats"] if b in by_id]
    if not ids:
        print(f"[{slug}/{name}] none of {spec['beats']} are beats in this video")
        return False

    total = sum(align.spoken_seconds(slug, b) for b in ids)
    if total > MAX_SECONDS:
        print(f"[{slug}/{name}] {total:.1f}s is over the {MAX_SECONDS:.0f}s Shorts "
              f"limit — drop a beat rather than speeding it up")
        return False

    dest = short_path(slug, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = out_dir(slug) / f"_{name}"
    work.mkdir(parents=True, exist_ok=True)
    for stale in work.glob("*"):
        stale.unlink()

    pieces = []
    for bid in ids:
        ok, clip = _clip_for(slug, by_id[bid], fps, work)
        if not ok:
            return False
        pieces.append(clip)

    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in pieces))
    picture = work / "picture.mp4"
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(picture)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[{slug}/{name}] concat FAILED\n{r.stderr[-400:]}")
        return False

    subs = _subtitles(slug, ids, work)
    narration = _narration(slug, ids, fps, work, assemble.duration(picture))

    args = [config.ffmpeg(), "-y", "-v", "error", "-i", str(picture)]
    if narration:
        args += ["-i", str(narration)]
    if subs:
        # Burned in, not a sidecar: vertical video is watched muted.
        args += ["-vf", f"ass={subs}", "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "20"]
    else:
        args += ["-c:v", "copy"]
    if narration:
        args += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    r = subprocess.run(args + [str(dest)], capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"[{slug}/{name}] finish FAILED\n{r.stderr[-500:]}")
        return False

    library.write(
        dest, library="animation",
        rights_block=library.rights("CC0-1.0", title=name, attribution_required=False),
        provenance_block=library.provenance(
            "video-pipeline/shorts", source_type="composite",
            inputs={"beats": ids, "resolution": f"{WIDTH}x{HEIGHT}"}),
        selected=True,
    )
    print(f"[{slug}/{name}] short: {len(ids)} beats, {assemble.duration(dest):.2f}s, "
          f"{WIDTH}x{HEIGHT} -> {dest.relative_to(config.REPO)}")
    return True
