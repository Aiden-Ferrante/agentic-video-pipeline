"""Compose one project's modules into a single editable timeline.

THE PROBLEM THIS SOLVES. A module is a good unit to *work* in — five people's
worth of parallel edits, incremental rebuilds, a beat retouched without
disturbing the other 143. It is a bad unit to *judge* in. Nothing could answer
"does the whole thing flow" because nothing held the whole thing: `videos/` was
a flat list of eight slugs, five of which were secretly one 26-minute film.

WHAT THIS IS, AND WHAT IT REFUSES TO BE. It is a VIEW. It is rebuilt from the
modules every time and read back by nobody. The authority table is unchanged:

    shotlist.json        content — which beats exist, what they say
    <slug>.edit.otio     time — durations, trims, order WITHIN a module
    projects/<id>.json   module identity and order
    out/projects/<id>.otio   NOTHING. Derived, every time.

That last line is the whole discipline. A project timeline that started holding
durations would be a third source of truth for time, and two documents that
disagree about how long a beat is are exactly how v1 died. If you want a cut to
stick, it belongs in the owning module's edit — `dispatch()` says which one.

BEAT IDS COLLIDE ACROSS MODULES. Thirty of the forty-four ids in the Logic
project appear in more than one module; every module has a `b01`. So clips here
are named `<module>/<beat>`, matching the doit task names you already type.
Copying the clips in with their bare ids would silently alias six different
opening shots to one name — the kind of defect that survives every test because
the file still opens and still plays.

MODULE BOUNDARIES ARE MARKERS, NOT NESTED STACKS. Kdenlive imports OTIO through
libopentimelineio directly, and a flat track is the shape it edits natively; a
nested composition is a shape we would be guessing about. Markers put the seams
where you can see them and cost nothing if the editor ignores them.
"""
import copy

import opentimelineio as otio

from . import config, projects, timeline

# Marker colours cycle so adjacent modules are distinguishable at a glance;
# OTIO defines the vocabulary, Kdenlive maps them onto its own guide colours.
_COLOURS = [otio.schema.MarkerColor.CYAN, otio.schema.MarkerColor.MAGENTA,
            otio.schema.MarkerColor.YELLOW, otio.schema.MarkerColor.GREEN]


def out_dir():
    return config.REPO / "out" / "projects"


def otio_path(project_id: str):
    return out_dir() / f"{project_id}.otio"


def find(project_id: str) -> dict | None:
    for p in projects.load():
        if p["id"] == project_id:
            return p
    return None


def module_sources(project: dict) -> list[tuple[dict, object]]:
    """[(module, path)] for every module that actually has a timeline to
    contribute, resolved through `timeline.authoritative_path` so a PROMOTED
    edit wins over the built one — the same rule every other consumer follows.

    A module with no timeline is dropped from the composition but named in the
    build log. Silently omitting it would make "not built yet" indistinguishable
    from "not in the project", which is the confusion the project layer exists
    to remove.
    """
    out = []
    for m in project["modules"]:
        if m.get("missing"):
            continue
        p = timeline.authoritative_path(m["slug"])
        if p.exists():
            out.append((m, p))
    return out


def tracks_by_stem(tl):
    """A module timeline's tracks, keyed by stem.

    Identity over position: `stem_of` reads the stamped metadata, then legacy
    track names — and for documents older than names, the first Video track is
    the visuals and the first Audio the narration, because those were the only
    two stems that existed when position was all there was.

    TRAP, documented in MAP.md and re-hit here: iterating the `.tracks` of a
    timeline that is only a temporary yields NOTHING, silently — the Stack does
    not keep its parent alive. Every caller must bind the timeline to a name
    first, which is why this takes the timeline rather than a path.
    """
    from . import stems as stems_mod
    out = {}
    for track in tl.tracks:
        stem = stems_mod.stem_of(track)
        if stem is None:
            if track.kind == otio.schema.TrackKind.Video:
                stem = "visuals"
            elif track.kind == otio.schema.TrackKind.Audio:
                stem = "narration"
        if stem and stem not in out:
            out[stem] = track
    return out


def build(project_id: str) -> bool:
    project = find(project_id)
    if project is None:
        print(f"[{project_id}] no such project")
        return False

    sources = module_sources(project)
    if not sources:
        print(f"[{project_id}] no module has a timeline yet")
        return False

    from . import stems as stems_mod

    fps = None
    out_tl = otio.schema.Timeline(name=project["title"])
    video = otio.schema.Track(
        name="visuals", kind=otio.schema.TrackKind.Video,
        metadata={"pipeline": {"stem": "visuals", "layout": "beats"}})
    out_tl.tracks.append(video)
    # One output track per audio stem, in STEMS order, created up front: a
    # track that appeared only when some module first contributed to it would
    # sit at a position that depends on module order, and the whole point of
    # stem identity is that nothing depends on position any more.
    audio_stems = {}
    for stem_name in stems_mod.STEMS:
        if stem_name in ("visuals", "captions"):
            continue
        t = otio.schema.Track(
            name=stem_name, kind=otio.schema.TrackKind.Audio,
            metadata={"pipeline": {"stem": stem_name,
                                   "layout": stems_mod.TRACK_LAYOUT[stem_name]}})
        audio_stems[stem_name] = t
        out_tl.tracks.append(t)
    audio = audio_stems["narration"]

    placed, skipped, desynced = 0, [], []
    for i, (mod, path) in enumerate(sources):
        slug = mod["slug"]
        # Bound to a name deliberately — see _tracks.
        src = otio.adapters.read_from_file(str(path))
        by_stem = tracks_by_stem(src)
        src_video = by_stem.get("visuals")
        if src_video is None or not len(src_video):
            skipped.append(slug)
            continue

        start = video.duration()
        if fps is None:
            fps = start.rate
            out_tl.global_start_time = otio.opentime.RationalTime(0, fps)

        for item in src_video:
            clip = copy.deepcopy(item)
            if isinstance(clip, otio.schema.Clip):
                # Qualify the name. `beat` is preserved untouched underneath so
                # dispatch() can route an edit home without parsing the label.
                bare = clip.metadata.get("pipeline", {}).get("beat", clip.name)
                clip.name = f"{slug}/{bare}"
                clip.metadata.setdefault("pipeline", {}).update(
                    {"project": project_id, "module": slug, "beat": bare})
            video.append(clip)
            placed += 1

        # The audio track is appended in lockstep. A module whose voice track is
        # missing or short contributes silence of exactly its picture's length —
        # anything else slides every later module out of sync, and the render is
        # cut to the SHORTER of the two tracks, so the drift would silently eat
        # the end of the film rather than announce itself.
        # Every audio stem is appended in lockstep with the picture. A module
        # with nothing on a stem contributes silence of exactly its picture's
        # length — anything else slides every later module out of sync, and the
        # render is cut to the SHORTER of picture and audio, so the drift would
        # silently eat the end of the film rather than announce itself.
        pic = video.duration() - start
        for stem_name, out_track in audio_stems.items():
            src_track = by_stem.get(stem_name)
            if src_track is not None and len(src_track):
                for item in src_track:
                    a = copy.deepcopy(item)
                    if isinstance(a, otio.schema.Clip):
                        bare = a.metadata.get("pipeline", {}).get("beat")
                        # A span clip (the music bed) has no beat; it is the
                        # module's, and is named for the module alone.
                        a.name = (f"{slug}/{bare}.{stem_name}" if bare
                                  else f"{slug}/{stem_name}")
                        meta = {"project": project_id, "module": slug}
                        if bare:
                            meta["beat"] = bare
                        a.metadata.setdefault("pipeline", {}).update(meta)
                    out_track.append(a)
                drift = pic - (out_track.duration() - start)
                if drift.value > 0:
                    out_track.append(otio.schema.Gap(
                        source_range=otio.opentime.TimeRange(
                            start_time=otio.opentime.RationalTime(0, drift.rate),
                            duration=drift)))
                elif drift.value < 0:
                    # The module arrived already desynced. Padding alone cannot
                    # fix this direction, and letting it through slides every
                    # LATER module out of place — a module-local defect would
                    # become a whole-film one. Absorb it into a trailing gap if
                    # there is one to shrink, and say so either way: a
                    # composition that quietly corrects its inputs hides the
                    # bug that produced them.
                    over = -drift
                    tail = out_track[-1] if len(out_track) else None
                    if (isinstance(tail, otio.schema.Gap)
                            and tail.source_range.duration >= over):
                        tail.source_range = otio.opentime.TimeRange(
                            start_time=tail.source_range.start_time,
                            duration=tail.source_range.duration - over)
                        desynced.append(f"{slug} (+{over.to_seconds():.2f}s "
                                        f"{stem_name}, absorbed)")
                    else:
                        desynced.append(f"{slug} (+{over.to_seconds():.2f}s "
                                        f"{stem_name}, NOT fixable here)")
            else:
                out_track.append(otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, pic.rate),
                        duration=pic)))

        label = " ".join(x for x in (mod.get("section"), mod.get("title") or slug) if x)
        video.markers.append(otio.schema.Marker(
            name=label,
            marked_range=otio.opentime.TimeRange(
                start_time=start, duration=pic),
            color=_COLOURS[i % len(_COLOURS)],
            metadata={"pipeline": {"module": slug, "project": project_id}}))

    if not placed:
        print(f"[{project_id}] nothing to place")
        return False

    out = otio_path(project_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(out_tl, str(out))

    total = out_tl.duration().to_seconds()
    mins, secs = divmod(total, 60)
    print(f"[{project_id}] program: {len(video.markers)} modules, {placed} clips, "
          f"{int(mins)}m{secs:04.1f}s -> {out.relative_to(config.REPO)}")
    if skipped:
        print(f"[{project_id}] no picture, left out: {', '.join(skipped)}")
    if desynced:
        print(f"[{project_id}] AUDIO LONGER THAN PICTURE in: {', '.join(desynced)}"
              f" — fix the module's own edit; this is a defect there, not here.")
    # Every stem against the picture — not tracks[0] vs tracks[1], which
    # stopped meaning anything the moment the stack stopped being a pair.
    for stem_name, out_track in audio_stems.items():
        stem_drift = video.duration() - out_track.duration()
        if abs(stem_drift.value) > 0:
            print(f"[{project_id}] WARNING: {stem_name} differs from the "
                  f"picture by {stem_drift.to_seconds():+.3f}s")
    return True


def dispatch(clip_name: str) -> tuple[str, str] | None:
    """`'axioms/b03'` -> `('axioms', 'b03')`. Which module owns a clip.

    The single answer to "I moved something on the project timeline, where does
    that edit actually live". It lives in the module — always. This function is
    the seam that keeps that true rather than hoping callers remember.
    """
    from . import stems as stems_mod
    slug, sep, beat = clip_name.partition("/")
    if not sep:
        return None
    # Strip any stem suffix — `.narration`, legacy `.vo` — but leave a name
    # that merely contains a dot alone; split_clip_name knows the difference.
    return slug, stems_mod.split_clip_name(beat)[0]
