"""Emit an OpenTimelineIO timeline from the shot list and rendered clips.

This is the hand-edit escape hatch. When the machine gets a beat wrong and you
want to fix it by eye rather than by re-authoring, open out/<slug>.otio in
Kdenlive 26.04+, which links libopentimelineio 0.18.1 directly — the same
version we write with, so there is no adapter and no schema skew.

IMPORTANT — import is a GUI action, not a command line one. Verified on
26.04.3: passing a .otio as an argument makes Kdenlive create an empty project
and add the file as a *bin clip* (it calls createClipFromFile on it). There is
no CLI flag and no .otio mimetype registration. The real path is:

    Kdenlive -> Project menu -> Import/Export -> OpenTimelineIO Project

AMENDED 2026-08-19 — the round trip is now supported, but only deliberately.
The v1 failure this guarded against was the editor becoming a SILENT second
source of truth. That is avoided by splitting authority rather than by refusing
the trip:

    shotlist.json   authoritative for CONTENT — which beats exist, what they
                    say, which scene renders
    <slug>.edit.otio  authoritative for TIME — durations, trims, order

`promote_timeline` is the one act that creates the edit copy, and the pipeline
never overwrites it. An un-promoted Kdenlive export changes nothing, so an
experiment in the editor cannot quietly become the truth.

Clip length comes from the ALIGNMENT, not from the render: a beat is as long as
it takes to say. The media referenced here is the fitted clip, which is already
exactly that long, so source_range == available_range and no consumer has to
guess what a beat's duration was meant to be.

We do NOT emit MLT XML: OTIO's mlt_xml adapter is write-only, stale since 2021,
and its output is measurably broken (it misspells `decsription`, so the profile
is silently ignored).
"""
import opentimelineio as otio

from . import align, assemble, beats, config, fit, sound, stems, voice


def otio_path(slug: str):
    return config.video_dir(slug) / "out" / f"{slug}.otio"


def edit_path(slug: str):
    """The promoted, hand-owned copy. The pipeline never writes this."""
    return config.video_dir(slug) / "out" / f"{slug}.edit.otio"


def authoritative_path(slug: str):
    """Whichever timeline actually governs: the promoted edit if one exists.

    Every consumer must resolve through this, or the agent and the renderer end
    up reading different documents.
    """
    edit = edit_path(slug)
    return edit if edit.exists() else otio_path(slug)


def _span(seconds: float, fps) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, fps),
        duration=otio.opentime.RationalTime(round(seconds * fps), fps),
    )


def build(slug: str) -> bool:
    doc = beats.load(slug)
    fps = doc.get("fps", 30)

    tl = otio.schema.Timeline(name=doc.get("title", slug))
    tl.global_start_time = otio.opentime.RationalTime(0, fps)
    # One track per stem, named by the stem, carrying machine-readable stem
    # identity — track NAMES are for people, metadata is for code. Layout
    # matters as much as identity: "beats" tracks hold one item per beat with
    # Gaps where the stem is absent (Gaps carry the beat id too, which is what
    # lets retime/reorder move every stem in step without positional lockstep);
    # a "span" track covers programme spans and per-beat operations skip it.
    video = otio.schema.Track(
        name="visuals", kind=otio.schema.TrackKind.Video,
        metadata={"pipeline": {"stem": "visuals", "layout": "beats"}})
    audio = otio.schema.Track(
        name="narration", kind=otio.schema.TrackKind.Audio,
        metadata={"pipeline": {"stem": "narration", "layout": "beats"}})
    tl.tracks.append(video)
    tl.tracks.append(audio)

    voiced = beats.needs_voice(slug)
    placed = 0
    for beat in doc.get("beats", []):
        bid = beat["id"]
        clip_file = fit.fit_path(slug, bid)
        if not clip_file.exists():
            continue
        seconds = assemble.duration(clip_file)
        if seconds <= 0:
            continue
        span = _span(seconds, fps)
        video.append(otio.schema.Clip(
            name=bid,
            media_reference=otio.schema.ExternalReference(
                target_url=clip_file.resolve().as_uri(), available_range=span),
            source_range=span,
            # The narration rides along as metadata so it is visible in the
            # editor without the editor needing to know what a beat is.
            metadata={"pipeline": {
                "beat": bid,
                "vo_text": beats.narration_text(beat),
                # Where this beat's audio starts inside its wav, so the renderer
                # does not have to re-derive it from the alignment.
                "voice_offset": (align.speech_window(slug, bid) or (0.0, 0.0))[0],
            }},
        ))

        # Gated on the slug's timing mode, not merely on the file existing. A
        # slug switched to "estimate" keeps its old wavs on disk, and placing
        # them against pictures cut to the ESTIMATE would drift the two apart
        # beat by beat — a silent timeline is correct, a desynced one is not.
        wav = voice.spoken_path(slug, bid)
        window = align.speech_window(slug, bid) if voiced else None
        if voiced and wav.exists() and window:
            offset, _length = window
            # The clip occupies the PICTURE's span, not the speech window: with
            # a settle, the picture outlasts the words, and a narration clip
            # cut to the words alone would slide every later beat out of sync.
            # conform() padded the wav so the tail is real silence; the clamp
            # is a guard, not an authority.
            wav_total = assemble.duration(wav)
            occupy = min(seconds, max(wav_total - offset, 0.0))
            audio.append(otio.schema.Clip(
                name=stems.clip_name(bid, "narration"),
                media_reference=otio.schema.ExternalReference(
                    target_url=wav.resolve().as_uri(),
                    available_range=_span(wav_total, fps)),
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(round(offset * fps), fps),
                    duration=otio.opentime.RationalTime(round(occupy * fps), fps)),
                metadata={"pipeline": {"beat": bid}},
            ))
        else:
            # A beat with no narration is silence of exactly the picture's
            # length, not a missing clip — and the Gap carries the beat id, so
            # a mutation matching by beat finds it instead of skipping it.
            audio.append(otio.schema.Gap(
                source_range=span, metadata={"pipeline": {"beat": bid}}))
        placed += 1

    if not placed:
        print(f"[{slug}] no clips to place in a timeline")
        return False

    # The music stem: one clip spanning the programme, present only when the
    # authored block asks for a bed that the library can actually supply. The
    # clip references assets/_bed.wav, which task_music produces AFTER this
    # task (its dep on this timeline orders it) — so on a first build the
    # editor shows the clip offline until one `doit` pass completes. That is
    # recorded, not accidental: a bed you cannot see or trim in the editor is
    # not a stem, and drift-checking covers it for free once it is a clip.
    block = sound.spec(slug)
    src = sound.source_for(block["bed"]) if block else None
    if block and src is not None:
        total = video.duration()
        music = otio.schema.Track(
            name="music", kind=otio.schema.TrackKind.Audio,
            metadata={"pipeline": {"stem": "music", "layout": "span"}})
        bed = sound.bed_path(slug)
        music.append(otio.schema.Clip(
            name="music",
            media_reference=otio.schema.ExternalReference(
                target_url=bed.resolve().as_uri(),
                available_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, fps),
                    duration=total)),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, fps), duration=total),
            metadata={"pipeline": {"stem": "music", "role": "bed",
                                   "bed": block["bed"]}},
        ))
        tl.tracks.append(music)

    out = otio_path(slug)
    out.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(tl, str(out))
    total = tl.duration().to_seconds()
    print(f"[{slug}] timeline: {placed} clips, {total:.2f}s -> {out.relative_to(config.REPO)}")
    return True
