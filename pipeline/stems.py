"""The stem vocabulary: one canonical name per media class, mapped everywhere.

A STEM is the vertical axis of a production — narration, visuals, music,
captions — separately produced, individually replaceable, synced by beat id,
laid together in the editor. The horizontal axis stays "modules" (1.2, 1.3, …)
and the two words are never interchangeable: five other vocabularies already
name media classes in this repo (beats.PROJECTIONS, beats.CLIP_KINDS, OTIO
track names, library.LIBRARIES, the registry's tools keys), none of them agree,
and captions and music are missing from most. This module is the reconciliation:
the canonical names live here, and every other vocabulary MAPS onto them.

MAPPED, NOT RENAMED, on disk. Fragment node names (`vo`), library names
(`voice`, `sound`), directory names (`videos/*/voice/`) and sidecar `library`
fields keep their historical spellings — renaming them would churn 160+ tracked
sidecars and invalidate build fragments for zero behavioural gain. Only the
user-facing names migrate: OTIO track names, tools keys, doit group names,
canvas nodes. If a table below looks like it is holding two spellings of the
same idea, that is the point.

Stdlib-only, like projects.py, and for the same reason: map/serve.py runs
outside the venv and must be able to import this.
"""

# Canonical order. Track stacks, program composition and displays follow it.
STEMS = ("narration", "visuals", "music", "captions")

# stem -> the beats.PROJECTIONS node(s) that carry its per-beat fragment.
# Music is slug-level (_music.json, the _grade.json pattern) — a per-beat music
# fragment is reserved until a per-beat music task exists (the CLIP_KINDS rule:
# never name a kind before its task does). Captions are a pure derivative of
# alignment + timeline and have no fragment at all.
FRAGMENT_NODES = {
    "narration": ("vo",),
    "visuals": ("animation", "memoryline", "imagery"),
    "music": (),
    "captions": (),
}

# How a stem's OTIO track is laid out.
#   beats  one item per beat, in beat order, Gaps where the stem is absent —
#          every item carries metadata.pipeline.beat, Gaps included.
#   span   items cover programme spans, not beats; per-beat operations
#          (retime, reorder) never touch a span track.
TRACK_LAYOUT = {"narration": "beats", "visuals": "beats", "music": "span"}

# What the stem IS in the mix. assemble routes audio by role: speech is summed
# at unity and is the sidechain key; a bed ducks under it.
ROLES = {"narration": "speech", "music": "bed"}

# Track names written before stems existed. Read-side only: nothing writes
# these names any more, but a promoted edit restored from a backup might still
# carry them, and reading it should not require archaeology.
LEGACY_TRACK_NAMES = {"video": "visuals", "voice": "narration"}

# Registry `tools` keys from before the stem vocabulary. Aliased forever —
# a project file is authored by a person and old spellings must keep meaning
# what they meant. Values are (stem,) or (stem, sub_key).
LEGACY_TOOL_KEYS = {
    "voice": ("narration",),
    "animation": ("visuals", "animation"),
    "imagery": ("visuals", "imagery"),
    "sound": ("music",),
}


def clip_name(beat_id: str, stem: str) -> str:
    """The clip name for a beat's item on a stem track: `b01.narration`."""
    return f"{beat_id}.{stem}"


def split_clip_name(name: str) -> tuple[str, str | None]:
    """(beat_id, stem) from a clip name, accepting every historical suffix.

    `b01.narration` -> ("b01", "narration"); legacy `b01.vo` -> ("b01",
    "narration"); a bare `b01` -> ("b01", None) — the visuals track has never
    suffixed its clips and does not start now, so downstream code keys the
    picture by bare beat id exactly as before.
    """
    base, dot, suffix = name.partition(".")
    if not dot:
        return name, None
    if suffix == "vo":
        return base, "narration"
    if suffix in STEMS:
        return base, suffix
    # An unrecognised suffix is part of the name, not a stem marker.
    return name, None


def stem_of(track) -> str | None:
    """Which stem an OTIO track carries, however old the document is.

    Resolution order: the stem stamped in metadata (written from Phase 3 on),
    then the legacy track name, then None — a kind alone is not enough to name
    a stem once there can be two audio stems on one timeline.
    """
    meta = getattr(track, "metadata", None) or {}
    stamped = (meta.get("pipeline") or {}).get("stem")
    if stamped in STEMS:
        return stamped
    return LEGACY_TRACK_NAMES.get(getattr(track, "name", None))
