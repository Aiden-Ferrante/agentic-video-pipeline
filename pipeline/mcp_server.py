"""MCP seam for the video pipeline — reads and shape-edits of the timeline.

This server fixes the *boundary* between an agent and the pipeline: an agent
reads state and operates on the timeline through OTIO files, never by
automating Kdenlive's GUI. Kdenlive is the human's window onto the same document.

What is real (2026-09-04, sixteen tools):

  * Reads: list_videos, get_shotlist, get_timeline, pipeline_status,
    check_edit_drift, get_style, script_diff.
  * Writes, all implemented: open_in_editor (launches Kdenlive), promote_timeline,
    retime, reorder, apply_polish, compile_script, adopt_script. Each one edits
    the OTIO or the authored shot list; none renders.
  * Still a stub: derive_short raises NotImplementedError naming the decision it
    waits on (_todo below). A stub that lies about working is worse than no stub
    — and so is a docstring that says the writes are stubs when eight of them
    are not, which this header did until the fleet's agent survey read it.

Nothing here builds. Execution stays in `doit`, so there is exactly one way to
produce an asset and the agent cannot become a second, divergent build path.
This server is registered by ../../.mcp.json (project scope) beside the fleet MCP.
"""
import json
import subprocess
from pathlib import Path

from mcp.server import MCPServer

from . import assemble, beats, config, library, timeline

# mcp 2.0 renamed FastMCP -> MCPServer and moved it to mcp.server.
mcp = MCPServer("video-pipeline",
                instructions="Read and shape the timeline for a video. Building "
                             "assets is the `doit` CLI's job, never this server's.")

KDENLIVE = ["flatpak", "run", "--user", "org.kde.kdenlive"]


def _todo(what: str, decision: str):
    raise NotImplementedError(
        f"{what} is scaffolding only. Blocked on: {decision}. "
        "See ../MAP.md; implement deliberately, not by accident."
    )


# ---------------------------------------------------------------- reads (real)

@mcp.tool()
def list_videos() -> list[dict]:
    """Every video in the pipeline, with what has been produced so far."""
    out = []
    for slug in config.slugs():
        doc = beats.load(slug)
        ids = [b["id"] for b in doc.get("beats", [])]
        out.append({
            "slug": slug,
            "title": doc.get("title", slug),
            "fps": doc.get("fps", 30),
            "beats": len(ids),
            "clips_rendered": sum(1 for b in ids if config.asset_path(slug, b).exists()),
            "has_timeline": timeline.otio_path(slug).exists(),
            "has_master": assemble.master_path(slug).exists(),
            "promoted": timeline.edit_path(slug).exists(),
        })
    return out


@mcp.tool()
def get_shotlist(slug: str) -> dict:
    """The authored shot list — what the pipeline was told to generate."""
    return beats.load(slug)


@mcp.tool()
def get_timeline(slug: str) -> dict:
    """Summarise the OTIO timeline: clips in order, with beat ids and timings.

    Reads the promoted edit copy when one exists, otherwise the generated one,
    so an agent always sees the timeline that is actually authoritative.
    """
    import opentimelineio as otio

    edit = timeline.edit_path(slug)
    path = timeline.authoritative_path(slug)
    if not path.exists():
        return {"slug": slug, "exists": False,
                "hint": "run `doit timeline:%s` to generate one" % slug}

    tl = otio.adapters.read_from_file(str(path))
    tracks = {}
    for track in tl.tracks:
        entries = []
        for clip in track:
            if not isinstance(clip, otio.schema.Clip):
                continue
            meta = clip.metadata.get("pipeline", {})
            entries.append({
                "name": clip.name,
                "beat": meta.get("beat"),
                "vo_text": meta.get("vo_text", ""),
                "seconds": round(clip.duration().to_seconds(), 3),
                "media": Path(clip.media_reference.target_url).name,
            })
        tracks[track.name or str(track.kind)] = entries

    # `clips` stays the picture. A caller asking "what is in this video" means
    # the cut, not the cut plus its narration counted a second time. Resolved
    # through the stem, not a literal track name, so a legacy "video" document
    # and a stems "visuals" one both answer.
    picture = _video_track(tl)
    picture_key = (picture.name or str(picture.kind)) if picture is not None else None
    return {"slug": slug, "exists": True, "source": path.name,
            "promoted": path == edit, "fps": tl.duration().rate,
            "seconds": round(tl.duration().to_seconds(), 3),
            "clips": tracks.get(picture_key, []), "tracks": tracks}


@mcp.tool()
def pipeline_status(slug: str = "") -> dict:
    """Per-task freshness from doit — which beats are stale and need rebuilding.

    Reports only. Use the `doit` CLI to actually rebuild; this server never
    builds, so there is one build path rather than two that can disagree.
    """
    r = subprocess.run([str(config.REPO / ".venv" / "bin" / "doit"), "list", "--all", "--status"],
                       cwd=config.REPO, capture_output=True, text=True, timeout=120)
    tasks = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and ":" in parts[1]:
            name = parts[1].split()[0]
            if not slug or f":{slug}" in name:
                tasks[name] = {"U": "fresh", "R": "stale"}.get(parts[0], parts[0])
    return {"tasks": tasks, "stale": [k for k, v in tasks.items() if v == "stale"]}


@mcp.tool()
def open_in_editor(slug: str) -> str:
    """Launch Kdenlive for this video so a human can polish the timeline.

    Kdenlive cannot take a .otio on the command line — verified on 26.04.3, it
    imports the file as a bin clip instead of opening it as a timeline. Import
    it from inside: Project -> Import/Export -> OpenTimelineIO Project.
    """
    path = timeline.otio_path(slug)
    if not path.exists():
        return f"No timeline for '{slug}' yet — run `doit timeline:{slug}` first."
    subprocess.Popen(KDENLIVE, cwd=str(path.parent),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return (f"Kdenlive launching. Import the timeline by hand:\n"
            f"  Project -> Import/Export -> OpenTimelineIO Project\n"
            f"  {path}")


# --------------------------------------------------------------- writes (stubs)

def _require_promoted(slug: str):
    """Mutations land on the hand-owned copy, never on the generated one.

    Editing the generated timeline would be pointless — the next `doit
    timeline:<slug>` overwrites it — and pretending otherwise is how an agent's
    work silently disappears.
    """
    path = timeline.edit_path(slug)
    if not path.exists():
        raise ValueError(
            f"'{slug}' has no promoted timeline. Call promote_timeline('{slug}') "
            f"first — the generated timeline is rebuilt from the shot list and "
            f"any edit to it would be overwritten.")
    return path


def _load(path):
    import opentimelineio as otio
    return otio.adapters.read_from_file(str(path))


def _save(tl, path) -> None:
    import opentimelineio as otio
    otio.adapters.write_to_file(tl, str(path))


def _stem_track(tl, stem):
    """The track carrying a stem, however old the document. Read-side only."""
    from . import stems
    for track in tl.tracks:
        if stems.stem_of(track) == stem:
            return track
    return None


def _video_track(tl):
    """The picture track: the visuals stem, falling back to kind for documents
    from before tracks carried names at all."""
    import opentimelineio as otio
    found = _stem_track(tl, "visuals")
    if found is not None:
        return found
    return next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)


def _beats_tracks(tl):
    """Every track whose layout is one-item-per-beat — the tracks a per-beat
    mutation touches. Span tracks (music) are deliberately not in this list:
    a bed covers the programme, and resizing one beat must not move it."""
    return [t for t in tl.tracks
            if ((t.metadata.get("pipeline") or {}).get("layout")) == "beats"]


def _item_beat(item):
    """The beat an item belongs to — Clips AND Gaps carry it in metadata from
    the stems timeline on; clip names are the fallback for hand-added clips."""
    from . import stems
    meta = getattr(item, "metadata", None) or {}
    bid = (meta.get("pipeline") or {}).get("beat")
    if bid:
        return bid
    name = getattr(item, "name", "") or ""
    return stems.split_clip_name(name)[0] if name else None


def _require_stem_form(tl, path):
    """Mutations need the stems-form document (layout metadata on tracks).

    A promoted edit from before Phase 3 has no layout stamps and its Gaps carry
    no beat ids, so beat-matching would silently skip them — the same defect
    positional matching existed to avoid. Zero such documents exist on disk;
    one restored from a backup gets a refusal, not a guess.
    """
    if not _beats_tracks(tl):
        raise ValueError(
            f"{path.name} predates stem tracks — delete it and re-promote "
            f"(promote_timeline) before mutating.")


@mcp.tool()
def promote_timeline(slug: str) -> str:
    """Copy the generated timeline to <slug>.edit.otio, which the pipeline will
    never overwrite. The one-way door from 'regenerable' to 'hand-owned'.

    Resolved 2026-08-19 — promotion does NOT snapshot the media. Copying every
    clip would double the disk for one video and still not help: the edit copy
    would then reference frozen assets, so fixing a typo in a Manim scene would
    stop reaching the edit entirely, which is worse than the problem. Instead
    the content hash of each referenced clip is recorded in the edit copy, so a
    later re-render is DETECTED (see check_edit_drift) rather than silently
    swapped underneath the edit.
    """
    # Check the destination first: "you already have a hand-owned edit" is the
    # more important thing to hear, and it is true whether or not the generated
    # timeline happens to exist right now.
    dest = timeline.edit_path(slug)
    if dest.exists():
        return (f"'{slug}' is already promoted at {dest.name}. Delete it by hand if you "
                f"really mean to discard the hand-owned edit and start again.")
    src = timeline.otio_path(slug)
    if not src.exists():
        return f"No generated timeline for '{slug}' — run `doit timeline:{slug}` first."

    import opentimelineio as otio
    tl = _load(src)
    stamped = 0
    for track in tl.tracks:
        for clip in track:
            if not isinstance(clip, otio.schema.Clip):
                continue
            media = assemble._media(clip)
            if media.exists():
                clip.metadata.setdefault("pipeline", {})["promoted_hash"] = \
                    library.content_hash(media)
                stamped += 1
    _save(tl, dest)
    return (f"Promoted '{slug}' -> {dest.name} ({stamped} clips hash-stamped). "
            f"This file is now authoritative for timing and order, and the pipeline "
            f"will not overwrite it. `doit master:{slug}` renders from it.")


@mcp.tool()
def check_edit_drift(slug: str) -> dict:
    """Which clips have been re-rendered since the timeline was promoted.

    The failure this catches: you promote an edit, then change a Manim scene.
    The clip on disk is new, the edit copy still points at that path, and the
    master silently mixes a hand-tuned edit with media the edit never saw.
    """
    path = timeline.edit_path(slug)
    if not path.exists():
        return {"slug": slug, "promoted": False, "drifted": []}

    import opentimelineio as otio
    drifted, checked = [], 0
    # `for track in _load(path).tracks` looks equivalent and is not: the
    # Timeline is a temporary, its Stack does not keep it alive, and iterating
    # the freed Stack yields NOTHING rather than raising. Always bind the
    # timeline to a name first.
    tl = _load(path)
    for track in tl.tracks:
        for clip in track:
            if not isinstance(clip, otio.schema.Clip):
                continue
            was = clip.metadata.get("pipeline", {}).get("promoted_hash")
            media = assemble._media(clip)
            if not was or not media.exists():
                continue
            checked += 1
            if library.content_hash(media) != was:
                drifted.append(clip.name)
    return {"slug": slug, "promoted": True, "checked": checked, "drifted": drifted}


@mcp.tool()
def retime(slug: str, beat: str, seconds: float) -> str:
    """Change a clip's duration on the promoted timeline.

    Resolved 2026-08-19 — the narration wins by default: `fit` already makes
    every clip exactly as long as its beat takes to say, so the timeline needs
    no opinion in the normal case. This tool is the deliberate override, and it
    can only SHORTEN. Asking for more than the media holds would mean holding a
    frame the edit never saw; that is a re-render, so it belongs in the shot
    list or the script, not here.
    """
    import opentimelineio as otio

    path = _require_promoted(slug)
    tl = _load(path)
    _require_stem_form(tl, path)
    clip = next((c for c in _video_track(tl) if c.name == beat), None)
    if clip is None:
        return f"No clip named '{beat}' on {path.name}."
    if seconds <= 0:
        return "A clip must be longer than zero seconds."

    available = clip.media_reference.available_range.duration.to_seconds()
    if seconds > available + 1e-6:
        return (f"'{beat}' only holds {available:.2f}s of media; {seconds:.2f}s would "
                f"need a re-render. Change the narration or the scene, then rebuild.")

    # Retime the picture AND whatever sits under it, together. Moving one alone
    # desyncs the tracks: the render is cut to the shorter of the two, so
    # shortening a clip on its own silently truncates what belongs to it.
    #
    # MATCHED BY BEAT, on beats-layout tracks only. Positional lockstep was the
    # old answer (Gaps carried no beat id, so metadata matching skipped them and
    # the axioms promotion test measured a 2.5s self-inflicted drift) — it died
    # the moment a span track could sit in the stack, because position stops
    # meaning "same beat" the first time one track is not one-item-per-beat.
    # The stems timeline stamps EVERY item, Gaps included, so the match is now
    # by identity, and span tracks (a music bed covers the programme) are
    # untouched by per-beat surgery.
    was, touched, clipped = None, [], None
    for track in _beats_tracks(tl):
        item = next((it for it in track if _item_beat(it) == beat), None)
        if item is None:
            continue
        fps = item.source_range.duration.rate
        span = item.source_range.duration.to_seconds()
        if track.kind == otio.schema.TrackKind.Video:
            was = span
        elif isinstance(item, otio.schema.Clip) and seconds < span - 1e-6:
            # Only a real narration clip can be cut mid-word. Shortening a Gap
            # removes silence, which is exactly what should happen.
            clipped = span
        item.source_range = otio.opentime.TimeRange(
            start_time=item.source_range.start_time,
            duration=otio.opentime.RationalTime(round(seconds * fps), fps))
        touched.append(track.name or str(track.kind))

    _save(tl, path)
    note = ""
    if clipped:
        note = (f" WARNING: this cuts {clipped - seconds:.2f}s off the narration, which "
                f"is against the VO-wins contract — the line will be clipped mid-word.")
    return (f"'{beat}' {was:.2f}s -> {seconds:.2f}s on {path.name}, tracks {touched}."
            f"{note} Run `doit master:{slug}` to render it.")


@mcp.tool()
def reorder(slug: str, beat_order: list[str]) -> str:
    """Reorder clips on the promoted timeline.

    Resolved 2026-08-19 — order does NOT propagate back into the shot list. The
    split of authority is that shotlist.json owns CONTENT (which beats exist,
    what they say) and the promoted timeline owns TIME (durations, trims,
    order). Writing back would make the editor a second author of the shot
    list, which is the v1 failure this design exists to avoid. If a reordering
    is permanent, move the beats in shotlist.json and re-promote.
    """
    path = _require_promoted(slug)
    tl = _load(path)
    _require_stem_form(tl, path)

    # Items, not Clips: a beat with no narration is a Gap, and a reorder that
    # left the Gaps behind would desync every beat after the first move — the
    # exact defect the old Clips-only loop had on estimate-mode slugs, where
    # the whole narration track is Gaps and was silently skipped.
    for track in _beats_tracks(tl):
        by_beat = {}
        for item in track:
            bid = _item_beat(item)
            if bid is not None:
                by_beat[bid] = item
        missing = [b for b in beat_order if b not in by_beat]
        if missing:
            return f"{path.name} has no items for {missing} on track '{track.name}'."
        if len(beat_order) != len(by_beat):
            return (f"Give every beat: track '{track.name}' has {len(by_beat)} items, "
                    f"you listed {len(beat_order)}.")
        ordered = [by_beat[b] for b in beat_order]
        del track[:]
        track.extend(ordered)

    _save(tl, path)
    return (f"Reordered {path.name} to {beat_order}. The shot list is unchanged — "
            f"if this is permanent, move the beats there and re-promote. "
            f"Run `doit master:{slug}` to render it.")


@mcp.tool()
def derive_short(slug: str, beats_range: list[str]) -> str:
    """Cut a contiguous beat range out of the timeline as a Short.

    Resolved 2026-08-19 — re-render, never crop. A 16:9 Manim scene has no safe
    9:16 crop: the composition is authored to the frame, so cropping cuts the
    subject. Manim renders at any resolution from the same source, so a Short
    is the same beats rendered at 1080x1920, not the master squeezed.

    Still unbuilt: it needs the imagery node first, because a Short whose beats
    include a non-animation visual has nothing to re-render from.
    """
    _todo("derive_short",
          "the imagery node, so a beat range can be re-rendered at 9:16 "
          "regardless of which generator produced each beat")


# Polish that must survive is polish the RENDERER understands. Kdenlive's own
# effects do not survive OTIO export, so anything expressed only in the editor
# is lost on the next master — which is why these are timeline operations
# rather than a passthrough to the NLE.
POLISH_KINDS = ("resync",)


@mcp.tool()
def apply_polish(slug: str, kind: str) -> str:
    """Apply a named polish pass to the promoted timeline.

    Resolved 2026-08-19 — polish lives in the timeline and the render step, not
    in Kdenlive effects, because those do not survive the round trip.

    Kinds:
      resync  Re-fit every clip to the CURRENT narration length, keeping any
              order and trim edits. This is what you run after re-recording a
              line: the edit you built against scratch VO stays, and only the
              timings move to the real take.
    """
    if kind not in POLISH_KINDS:
        return f"Unknown polish {kind!r}. Available: {', '.join(POLISH_KINDS)}."

    import opentimelineio as otio
    from . import fit

    path = _require_promoted(slug)
    tl = _load(path)
    _require_stem_form(tl, path)
    changed = []
    # Items on beats-layout tracks, Gaps included — a Gap IS the beat's
    # narration when there is none, and a resync that skipped it would leave
    # silence the wrong length while moving the picture, which is the desync
    # retime was once bitten by. Span tracks are untouched: assemble trims the
    # bed to the picture's total, so per-beat resizes never overhang it.
    for track in _beats_tracks(tl):
        for item in track:
            bid = _item_beat(item)
            # Through fit, not straight to the alignment: the slug's timing mode
            # decides whether a beat's length comes from a take or from the
            # script, and a resync that ignored that would fight the next build.
            want = fit.target_seconds(slug, bid) if bid else 0.0
            if want <= 0:
                continue
            fps = item.source_range.duration.rate
            was = item.source_range.duration.to_seconds()
            if isinstance(item, otio.schema.Clip):
                # Never claim more media than exists; the fit node is what makes
                # the picture long enough, and it may not have re-run yet.
                available = item.media_reference.available_range.duration.to_seconds()
                target = min(want, available)
            else:
                target = want
            if abs(target - was) < 0.04:
                continue
            item.source_range = otio.opentime.TimeRange(
                start_time=item.source_range.start_time,
                duration=otio.opentime.RationalTime(round(target * fps), fps))
            if track.kind == otio.schema.TrackKind.Video:
                changed.append(f"{bid} {was:.2f}->{target:.2f}s")

    _save(tl, path)
    if not changed:
        return f"'{slug}' is already in sync with its narration; nothing to change."
    return (f"Resynced {path.name}: {', '.join(changed)}. Run `doit fit:{slug} "
            f"master:{slug}` — fit re-renders the picture to match.")


# -------------------------------------------------------------- the style

@mcp.tool()
def get_style(slug: str) -> dict:
    """Which style a video wears, where the binding came from (shot list or
    registry), and the effective blocks the pipeline reads after the style's
    defaults are merged under the shot list's own. `styles` lists every style
    on disk."""
    from . import styles
    doc = beats.effective(slug)
    return {"slug": slug, "style": styles.identity(slug), "styles": styles.available(),
            "effective": {k: doc.get(k) for k in ("timing", "grade", "letterbox",
                                                   "music", "chapter_tag")}}


# --------------------------------------------------------- the screenplay


@mcp.tool()
def film_at(slug: str, timecode: str) -> dict:
    """Where a moment of the FINISHED, scored film lives — for turning a viewing note ("at 4:12 the hit is too
    loud") into an edit. `timecode` is "4:12", "1:02:03" or seconds. Returns the act, the beat, the offset inside
    the beat, the word being said and the words around it, the beat's sound cues (`sfx` in the shot list), and
    whether the moment sits in a dip between two acts. Read-only; needs a built film (`doit film:<slug>`)."""
    from . import finish
    if not finish.enabled(slug):
        return {"error": f"{slug} has no finish stage (its style or shot list does not enable `finish`)"}
    if not finish.acts_json(slug).exists():
        return {"error": f"{slug}: no acts.json yet — build it with `doit film:{slug}`"}
    return finish.locate(slug, timecode)


def _cue_problems(slug: str) -> list[str]:
    """Sound-effect cues whose word the narration no longer says (finishing videos only; [] otherwise)."""
    from . import finish
    try:
        return finish.cue_problems(slug) if finish.enabled(slug) else []
    except (OSError, ValueError, KeyError):
        return []


@mcp.tool()
def script_diff(slug: str, script: str = "") -> dict:
    """What compiling the vault screenplay would change in the shot list —
    beats added, narration changed, orphans, reorder — without writing.

    `script` overrides the shot list's own `script` field (vault-relative or
    absolute path). Raises if the screenplay has unmarked blocks: run
    adopt_script first, because ids are authored, never derived.
    """
    from . import screenplay
    out = screenplay.diff(slug, script or None)
    out["cue_problems"] = _cue_problems(slug)
    return out


@mcp.tool()
def compile_script(slug: str, script: str = "") -> dict:
    """Merge the vault screenplay into shotlist.json.

    The script owns narration, order and each beat's `brief`; it never touches
    an existing beat's visual recipe. New blocks become `brief` beats that
    render as placeholder cards, so the animatic builds. Silent beats the script
    does not name keep their place; narrated ones it dropped are reported as
    orphans and kept. Run `doit` afterwards — this writes the authored file and
    builds nothing.
    """
    from . import screenplay
    out = screenplay.compile(slug, script or None, write=True)
    out["cue_problems"] = _cue_problems(slug)   # a reworded line can orphan a sound cue: say so now, not at render
    return out


@mcp.tool()
def adopt_script(slug: str, script: str = "", write: bool = True) -> dict:
    """Write `[id]` markers into a screenplay that has none, matching each
    NARRATOR block to an existing beat by its text. Unmatched narrated blocks
    get fresh ids; silent VIDEO-only blocks are reported, not guessed.
    `write=False` previews the matching."""
    from . import screenplay
    return screenplay.adopt(slug, script or None, write=write)


if __name__ == "__main__":
    mcp.run()
