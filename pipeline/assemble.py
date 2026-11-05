"""Assembly node: the authoritative timeline becomes one master.

REWRITTEN 2026-08-19. This used to walk the shot list and concatenate
assets/*.mp4 directly, which meant the OTIO timeline was a dead-end output: an
agent retime or a Kdenlive polish could not reach the master, and the file you
watched was never the file you edited. Now the timeline IS the source, and
`timeline.authoritative_path()` decides which one — the promoted edit copy when
there is one, the generated timeline otherwise.

Still deliberately dumb. It reads clips, trims, and muxes; it makes no
decisions. If this ever needs to be clever, the timeline is lying — v1 inverted
that and made the editor the smartest component, so every change had to be made
through it.

Video is stream-copied whenever a clip is used whole, because the fit node
already produced media at exactly the right length in the project's format.
Audio is the one thing that must be re-encoded: loudness is a property of the
finished narration, so it is measured once, here, over the whole track.
"""
import subprocess

from . import config, voice


def master_path(slug: str):
    return config.video_dir(slug) / "out" / "master.mp4"


def duration(path) -> float:
    r = subprocess.run(
        [config.ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _clips(track):
    import opentimelineio as otio
    for item in track:
        if isinstance(item, otio.schema.Clip):
            yield item


def read_timeline(slug: str):
    """(timeline, path, promoted) for whichever document actually governs."""
    import opentimelineio as otio
    from . import timeline as tl_mod

    path = tl_mod.authoritative_path(slug)
    if not path.exists():
        return None, path, False
    return (otio.adapters.read_from_file(str(path)), path,
            path == tl_mod.edit_path(slug))


def total_seconds(slug: str) -> float:
    """Length of the finished programme, per the authoritative timeline."""
    tl, _, _ = read_timeline(slug)
    return round(tl.duration().to_seconds(), 3) if tl is not None else 0.0


def beat_spans(slug: str) -> list[tuple[str, float, float]]:
    """(beat, start_on_the_timeline, duration) for the picture, in cut order.

    Anything that needs to talk about a moment in the FINISHED video — captions,
    chapters, a Short's in and out points — has to ask the timeline, because a
    reorder or a trim moves every beat after it.
    """
    import opentimelineio as otio
    tl, _, _ = read_timeline(slug)
    if tl is None:
        return []
    out = []
    for track in tl.tracks:
        if track.kind != otio.schema.TrackKind.Video:
            continue
        for clip in _clips(track):
            beat = clip.metadata.get("pipeline", {}).get("beat") or clip.name
            out.append((beat,
                        round(track.range_of_child(clip).start_time.to_seconds(), 3),
                        round(clip.source_range.duration.to_seconds(), 3)))
    return out


def _media(clip):
    from pathlib import Path
    from urllib.parse import unquote, urlparse
    url = clip.media_reference.target_url
    return Path(unquote(urlparse(url).path))


def _segment(clip, dest) -> bool:
    """One timeline clip as a standalone file, honouring its source_range.

    A human trimming a clip in Kdenlive shows up here as a source_range shorter
    than the media, and that is the whole point of rendering from the timeline.
    """
    src = _media(clip)
    if not src.exists():
        print(f"  missing media for {clip.name}: {src}")
        return False

    start = clip.source_range.start_time.to_seconds()
    length = clip.source_range.duration.to_seconds()
    whole = abs(start) < 1e-6 and abs(length - duration(src)) < 0.04

    args = [config.ffmpeg(), "-y", "-v", "error"]
    if whole:
        args += ["-i", str(src), "-c", "copy"]
    else:
        args += ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}",
                 *config.video_encoder(19),
                 "-pix_fmt", "yuv420p", "-an"]
    r = subprocess.run(args + [str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  segment {clip.name} FAILED\n{r.stderr[-400:]}")
        return False
    return True


def _collect_audio(tl):
    """Every placeable audio clip on the timeline, grouped by mix role.

    Role comes from the stem (stems.ROLES): narration is speech, music is a
    bed. A track with no stem identity is treated as speech — the only audio
    the pipeline wrote before stems existed was narration, so that is what an
    unlabelled track is.
    """
    import opentimelineio as otio

    from . import stems

    grouped = {"speech": [], "bed": []}
    for track in tl.tracks:
        if track.kind != otio.schema.TrackKind.Audio:
            continue
        role = stems.ROLES.get(stems.stem_of(track), "speech")
        for clip in _clips(track):
            src = _media(clip)
            if not src.exists():
                continue
            grouped[role].append((
                src,
                track.range_of_child(clip).start_time.to_seconds(),
                clip.source_range.start_time.to_seconds(),
                clip.source_range.duration.to_seconds(),
            ))
    return grouped


# The audio stock — one treatment over every programme, the mix-side twin of
# grade.py's film stock. Chosen by A/B on real narration (2026-08-26):
#   room tone   pink noise at an amplitude you feel rather than hear. TTS
#               emits DIGITAL silence, and the settle pauses made that the
#               loudest "this is AI" tell in the film; a noise floor turns a
#               void into a room.
#   saturation  a light harmonic exciter on the speech sum — the richness of
#               having passed through real equipment.
# Constants, not authored: like the grain field, this is a property of the
# house sound. The day one work needs different air, it becomes an authored
# block (Backlog).
ROOM_TONE_AMPLITUDE = 0.0006
EXCITER = "aexciter=amount=1.5:drive=6:blend=0.4:freq=5500"


def _mix_graph(n_speech: int, n_bed: int, placements, total: float):
    """The ffmpeg filtergraph for a stem mix. Pure, so the tests can read it.

    placements is the flat input list (speech first, then beds), each
    (start, at, length) in seconds. The shape of the graph is the design:

      speech  summed at unity — narration clips never fight each other
      beds    summed, then ducked under the speech sum via sidechaincompress,
              which is what lets a bed sit under words without a hand-ridden
              fader: it drops while a word is said and returns in the gaps
      both    padded and trimmed to the picture's total, so audio can never
              outlive the cut

    No speech: the beds play unducked (a sidechain needs a key). No beds:
    today's narration-only path, byte-for-byte.
    """
    filters, labels = [], []
    for i, (start, at, length) in enumerate(placements):
        filters.append(
            f"[{i}:a]atrim=start={start:.3f}:duration={length:.3f},"
            f"asetpts=PTS-STARTPTS,"
            f"adelay={int(at * 1000)}:all=1[a{i}]")
        labels.append(f"[a{i}]")

    speech, beds = labels[:n_speech], labels[n_speech:]
    # The room tone spans the whole programme and sits under EVERYTHING —
    # including the pauses, which is the point.
    filters.append(f"anoisesrc=color=pink:amplitude={ROOM_TONE_AMPLITUDE}:"
                   f"duration={total:.3f}[room]")
    tail = f"apad,atrim=duration={total:.3f}[out]"

    if speech and beds:
        filters.append("".join(speech) +
                       f"amix=inputs={len(speech)}:normalize=0:"
                       f"dropout_transition=0," + EXCITER + "[vox]")
        filters.append("".join(beds) +
                       f"amix=inputs={len(beds)}:normalize=0:"
                       f"dropout_transition=0[bedmix]")
        # Ratio 6 from a -9 dB rest puts the bed roughly 18 dB under the voice
        # while a line is running — the documentary convention — and lets it
        # come back up through the settle pauses. Slow attack keeps consonants
        # from pumping it; slow release stops it breathing between words.
        filters.append("[vox]asplit=2[voxout][trigger];"
                       "[bedmix][trigger]sidechaincompress="
                       "threshold=0.035:ratio=6:attack=15:release=450:makeup=1[under];"
                       "[voxout][under][room]amix=inputs=3:normalize=0:"
                       "dropout_transition=0," + tail)
    elif speech:
        filters.append("".join(speech) +
                       f"amix=inputs={len(speech)}:normalize=0:"
                       f"dropout_transition=0," + EXCITER + "[vox];"
                       "[vox][room]amix=inputs=2:normalize=0:"
                       "dropout_transition=0," + tail)
    else:
        filters.append("".join(beds) +
                       f"amix=inputs={len(beds)}:normalize=0:"
                       f"dropout_transition=0[bedmix];"
                       "[bedmix][room]amix=inputs=2:normalize=0:"
                       "dropout_transition=0," + tail)
    return ";".join(filters)


def _mix_audio(slug, tl, dest):
    """The programme's audio: every stem placed by role, in one pass.

    Replaced the two-pass voice-then-duck design (2026-08-26, stems Phase 4):
    the bed used to enter off-timeline by path, which meant the editor never
    saw it and drift-checking could not cover it. Now everything audible is a
    clip on the timeline, and this renders whatever the timeline says.

    Returns None when the timeline is legitimately silent — distinct from a
    mix that broke, which is False. Loudness is still set exactly once, here,
    over the finished mix (bed included): normalising the voice and then
    adding music would put the result over target.
    """
    grouped = _collect_audio(tl)
    speech, beds = grouped["speech"], grouped["bed"]
    if not speech and not beds:
        return None

    ordered = speech + beds
    inputs = []
    for src, _at, _start, _length in ordered:
        inputs += ["-i", str(src)]
    graph = _mix_graph(len(speech), len(beds),
                       [(start, at, length) for _src, at, start, length in ordered],
                       tl.duration().to_seconds())

    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", *inputs,
         "-filter_complex", graph, "-map", "[out]",
         "-ar", str(voice.RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  audio mix FAILED\n{r.stderr[-600:]}")
        return False

    # ffmpeg cannot read and write the same file, so normalisation lands on a
    # scratch path and is moved into place once.
    work = dest.with_name("mix.wav")
    ok = voice.normalize_track(dest, work)
    if ok:
        work.replace(dest)
    work.unlink(missing_ok=True)
    return ok


def build(slug: str) -> bool:
    tl, path, promoted = read_timeline(slug)
    if tl is None:
        print(f"[{slug}] no timeline at {path.name} — run `doit timeline:{slug}`")
        return False

    import opentimelineio as otio

    from . import stems
    # The picture is the visuals STEM, not "the first video track" — the
    # moment a second video track can exist (overlays, captions burned as a
    # track) first-by-kind stops meaning anything. Kind is the legacy fallback.
    video = next((t for t in tl.tracks if stems.stem_of(t) == "visuals"), None)
    if video is None:
        video = next((t for t in tl.tracks
                      if t.kind == otio.schema.TrackKind.Video), None)
    clips = list(_clips(video)) if video is not None else []
    if not clips:
        print(f"[{slug}] timeline has no picture")
        return False

    out = master_path(slug)
    work = out.parent / "segments"
    work.mkdir(parents=True, exist_ok=True)
    # Segment names encode timeline position, so a reorder leaves the previous
    # names orphaned. Nothing reads them — concat.txt lists only what we just
    # wrote — but they accumulate on every edit, so clear the scratch dir.
    for stale in work.glob("*.mp4"):
        stale.unlink()

    segments = []
    for n, clip in enumerate(clips):
        seg = work / f"{n:03d}_{clip.name}.mp4"
        if not _segment(clip, seg):
            return False
        segments.append(seg)

    listing = out.parent / "concat.txt"
    listing.write_text("".join(f"file '{s.resolve()}'\n" for s in segments))
    silent = out.parent / "picture.mp4"
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", "-an", str(silent)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[{slug}] picture concat FAILED\n{r.stderr[-600:]}")
        return False

    # One grade over the assembled picture, when the shot list authors one. This
    # is not assemble getting clever: the spec is authored, and all this does is
    # run it. It happens here, after the concat, because grain has to be
    # continuous across cuts — see grade.py.
    from . import beats, grade
    graded_spec = grade.spec(slug)
    if graded_spec:
        graded = out.parent / "picture.graded.mp4"
        if not grade.apply(silent, graded, graded_spec, beats.load(slug).get("fps", 30)):
            # An ungraded master that reports success is worse than no master:
            # the whole point of the pass is that you cannot see it is missing
            # by glancing at one frame.
            graded.unlink(missing_ok=True)
            print(f"[{slug}] master FAILED: the shot list authors a grade but it "
                  f"could not be applied")
            return False
        graded.replace(silent)
        applied = [k for k, v in graded_spec.items()
                   if isinstance(v, (int, float)) and v > 0 and k != "halation_radius"]
        print(f"[{slug}] graded: {', '.join(sorted(applied))}")

    # The chapter tag, when the shot list asks for one. Authored, like the
    # grade — assemble runs the spec, it does not decide to have one. It goes
    # AFTER the grade so the tag is not grained, vignetted and halated along
    # with the picture: it is an overlay on the finished image, not part of it.
    tag_spec = (beats.effective(slug).get("chapter_tag") or {})
    if tag_spec.get("enabled"):
        from . import chapter_tag
        states = chapter_tag.render(slug)
        if not states:
            print(f"[{slug}] master FAILED: chapter_tag is enabled but no "
                  f"chapter spans could be resolved")
            return False
        inputs, graph = chapter_tag.overlay_args(states)
        tagged = out.parent / "picture.tagged.mp4"
        r = subprocess.run(
            [config.ffmpeg(), "-y", "-v", "error", "-i", str(silent), *inputs,
             "-filter_complex", graph, "-map", "[tagged]", "-shortest",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", str(tagged)],
            capture_output=True, text=True)
        if r.returncode != 0 or not tagged.exists():
            tagged.unlink(missing_ok=True)
            print(f"[{slug}] master FAILED: chapter tag overlay\n{r.stderr[-600:]}")
            return False
        tagged.replace(silent)
        print(f"[{slug}] chapter tag: {len(states)} spans, "
              f"{len({str(s['png']) for s in states})} states")

    narration = out.parent / "voice.wav"
    mixed = _mix_audio(slug, tl, narration)
    if mixed is False:
        # The timeline HAS narration and we failed to build it. Shipping a
        # silent master here would report success for a broken video.
        print(f"[{slug}] master FAILED: the timeline has narration but the "
              f"voice track could not be built")
        return False

    if mixed:
        r = subprocess.run(
            [config.ffmpeg(), "-y", "-v", "error", "-i", str(silent), "-i", str(narration),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[{slug}] mux FAILED\n{r.stderr[-600:]}")
            return False
    else:
        silent.replace(out)

    # The finished video declares what it is made of. `compositeSynthetic` is
    # the IPTC term for exactly this, and it is what a platform's provenance
    # check reads — the pieces each carry their own origin, the master carries
    # the fact that it is an assembly of them.
    from . import library
    library.write(
        out, library="animation",
        rights_block=library.rights(
            "CC0-1.0", title=tl.name or slug, attribution_required=False,
            modification_note="assembled from the pipeline timeline"),
        provenance_block=library.provenance(
            "video-pipeline/assemble", source_type="composite",
            inputs={"timeline": path.name,
                    "promoted": promoted,
                    "clips": {c.name: library.content_hash(_media(c))
                              for c in clips if _media(c).exists()}}),
        selected=True,
        # Credits are recomputed here rather than stored, but recording whether
        # anything is owed makes an unattributed publish visible in the sidecar.
        attribution_owed=bool(library.credits(config.video_dir(slug))),
    )

    source = "promoted edit" if promoted else "generated timeline"
    audio = "with narration" if mixed else "SILENT (no narration on the timeline)"
    print(f"[{slug}] master: {len(clips)} clips, {duration(out):.2f}s, {audio}, "
          f"from the {source} -> {out.relative_to(config.REPO)}")
    return True
