"""Split the authored shotlist into per-beat, per-node fragments.

This is the most load-bearing step in the pipeline. Two levels of granularity
matter, and missing either one silently destroys incremental re-render:

1. Per BEAT. A monolithic shotlist.json declared as input to every beat means
   editing beat 7 invalidates all 80 — no timestamp or whole-file hash can see
   that only one beat moved.

2. Per NODE. A whole-beat fragment means retouching narration re-renders the
   animation, even though the animation never reads vo_text. Each generator
   gets a projection containing only the fields it actually consumes, so it
   rebuilds only when its own slice changes.

Everything is written write-if-changed: an untouched fragment keeps its mtime,
so doit leaves that beat's downstream assets alone.

Rule: shotlist.json is authored by a human. beats/* is derived and must never
be hand-edited — this step overwrites and prunes it.
"""
import json
from pathlib import Path

from . import config

# ---- stem accessors ---------------------------------------------------------
# A beat MAY carry a `stems` map ({"narration": {"text": ...}, "visuals":
# {...}}); the flat fields (`vo_text`, `visual`) are first-class sugar and stay
# forever. EVERY read of a beat's media goes through these accessors, so the
# two spellings cannot drift — and the projections emit the SAME JSON keys for
# both, so migrating a shotlist from sugar to stems form produces byte-identical
# fragments and invalidates nothing.

# Per-beat music and captions are reserved, not accepted: no per-beat task
# exists for either, and the CLIP_KINDS rule below applies to stems too —
# naming a capability before its task exists IS the bug.
_RESERVED_BEAT_STEMS = ("music", "captions")


def narration_text(beat: dict) -> str:
    """The beat's narration, from `stems.narration.text` or the `vo_text` sugar."""
    stems_map = beat.get("stems") or {}
    block = stems_map.get("narration")
    if block is not None:
        return block.get("text", "")
    return beat.get("vo_text", "")


def visual_spec(beat: dict) -> dict:
    """The beat's visual spec, from `stems.visuals` or the `visual` sugar."""
    stems_map = beat.get("stems") or {}
    block = stems_map.get("visuals")
    if block is not None:
        return block
    return beat.get("visual", {})


def _warn_reserved(beat: dict) -> None:
    stems_map = beat.get("stems") or {}
    for name in _RESERVED_BEAT_STEMS:
        if name in stems_map:
            print(f"[beats] WARNING {beat.get('id', '?')}: per-beat "
                  f"'stems.{name}' is reserved and ignored — no per-beat "
                  f"{name} task exists (music is authored slug-level)")


# One projection per generator node. A node's file_dep is its own projection
# and nothing else. Adding a capability means adding a projection here —
# a new field, never a new pathway.
def _visual(beat, doc):
    return {
        "id": beat["id"],
        "visual": visual_spec(beat),
        # fps and resolution belong in here: changing the project fps must
        # invalidate every clip, and it only can if it is inside the bytes.
        "fps": doc.get("fps", 30),
        "resolution": doc.get("resolution", [1920, 1080]),
        # The bars behind a letterboxed plate. Same rule as fps — it changes
        # pixels, so it lives in the fragment. It defaults to the historical
        # value, and it exists because a film whose archival beats letterbox to
        # one black while its Manim beats render on another has a visible level
        # shift at every cut between them.
        "letterbox": doc.get("letterbox", "0x0d1117"),
    }


def _imagery(beat, doc):
    """Like _visual, plus how long the beat will end up on screen.

    A still that is merely HELD does not care about its own length — the fit
    node stretches it and a frozen still is indistinguishable from a held one.
    A still that MOVES cares enormously: rendered at 5s and held to 9s, the
    camera glides for five seconds and then stops dead for four, which is the
    exact freeze this pipeline works to avoid on animation beats.

    Only populated when the slug is timed from the script, because that is the
    only mode where a beat's length is knowable before anything is rendered.
    In voice mode it stays null and the imagery node falls back to its nominal
    hold, exactly as before.
    """
    from . import reading
    out = _visual(beat, doc)
    block = doc.get("timing") or {}
    # An authored `seconds` wins over the estimate. A silent beat has no
    # narration to be timed from, so without this every wordless shot is
    # NOMINAL_SECONDS and a montage comes out metronomic — sixteen identical
    # five-second holds read as a slideshow, not a sequence.
    authored = (out.get("visual") or {}).get("seconds")
    if authored:
        out["seconds"] = float(authored)
    elif block.get("mode") == "estimate":
        out["seconds"] = reading.estimate(narration_text(beat),
                                          float(block.get("pace", 1.0)))
    else:
        out["seconds"] = None
    return out


def _brief(beat, doc):
    """A brief card is cut to the narration like a still, so it carries the
    same `seconds` as an imagery beat — plus the direction it typesets."""
    out = _imagery(beat, doc)
    out["brief"] = beat.get("brief", "")
    out["section"] = beat.get("section", "")
    return out


PROJECTIONS = {
    "animation": _visual,
    "memoryline": _visual,
    "imagery": _imagery,
    "brief": _brief,
    # The key stays `vo_text` whichever form authored it — fragment bytes are
    # the invalidation contract, and a byte-stable projection is what makes a
    # sugar->stems migration free.
    "vo": lambda beat, doc: {
        "id": beat["id"],
        "vo_text": narration_text(beat),
    },
}

# Every visual.kind that has a generator behind it, and so produces a clip at
# config.asset_path(). A kind that is NOT listed here produces nothing, and its
# beat must never reach a downstream file_dep — doit would demand a target no
# task creates and fail the whole build rather than that one beat.
#
# This tuple must never name a kind before its task exists in dodo.py. Listing
# an aspirational generator here IS the bug it is meant to prevent.
CLIP_KINDS = ("animation", "memoryline", "imagery", "brief")


def tools_path(slug):
    """The resolved provider map, written by task_tools rather than split():
    its input is the owning project's registry file, not the shotlist, and a
    fragment must be rebuilt by the bytes that actually drive it. It lives in
    beats/ so the invalidation story stays in one directory; split() only
    protects it from the prune."""
    return _dir(slug) / "_tools.json"


def music_path(slug):
    """The slug-level music spec, projected out for the same reason as _grade.

    `sound.spec` used to read shotlist.json live — so `task_sound` had no dep
    on the authored gain/fade, and editing `gain_db` rebuilt nothing. Same
    defect, same fix: project the block into its own fragment so the sound
    task's file_dep sees the bytes that actually drive it.

    Always written, null when there is none — the file's presence must not be
    a signal, or the dependency SET would change when a bed is added and doit
    reads a changed set as staleness everywhere.
    """
    return _dir(slug) / "_music.json"


def load(slug: str) -> dict:
    """The RAW authored shot list. Tools that write the file read this."""
    return json.loads((config.video_dir(slug) / "shotlist.json").read_text())


def effective(slug: str) -> dict:
    """The shot list with its style's bindings merged UNDER it — what the
    pipeline READS. Never save this: the compiler and Gen Lab write the
    authored file, and baking a style default into it would freeze the
    module to the style it happened to wear that day."""
    from . import styles
    return styles.effective(load(slug), styles.bindings(slug))


# How a slug decides how long a beat is. The picture always yields to the
# narration; this only chooses where the narration's length is READ FROM.
#
#   auto      an alignment when one exists, else the read estimate. The
#             historical behaviour, so slugs with no timing block are unchanged.
#   estimate  the script alone. No TTS, no alignment, no voice track — the
#             visuals are cut to how long the words take to read.
#   voice     require a real alignment; never guess from text.
TIMING_MODES = ("auto", "estimate", "voice")


def timing(slug: str) -> dict:
    """The slug's timing authority: {"mode": str, "pace": float}.

    Warns rather than raises on an unknown mode. This is read during task
    ENUMERATION, so raising here would take down the whole build — including
    every unrelated slug — over one typo in one shot list.
    """
    block = effective(slug).get("timing") or {}
    mode = block.get("mode", "auto")
    if mode not in TIMING_MODES:
        print(f"  [warn] {slug}: unknown timing mode {mode!r}; "
              f"falling back to 'auto'. Expected one of {list(TIMING_MODES)}.")
        mode = "auto"
    return {"mode": mode, "pace": float(block.get("pace", 1.0)),
            # Seconds the PICTURE holds after the narration lands. The fix for
            # "the words are fine but it feels too fast": ideas need a beat of
            # silence to settle before the next one starts. Applied per
            # narrated beat by fit; silent beats keep their rendered length.
            "settle": float(block.get("settle", 0.0))}


def needs_voice(slug: str) -> bool:
    """Whether this slug has a narration track at all.

    In estimate mode there is nothing to synthesise and nothing to align, so
    those tasks must not be ENUMERATED — a task that is merely skipped at run
    time still leaves downstream file_deps demanding targets nobody produces,
    and doit fails the build rather than the beat.
    """
    return timing(slug)["mode"] != "estimate"


def beat_ids(slug: str) -> list[str]:
    """Read straight from the authored file, so task enumeration works before
    the fragments have ever been generated."""
    return [b["id"] for b in load(slug).get("beats", [])]


def narrated_ids(slug: str) -> list[str]:
    """Beats that HAVE narration — the ones narration/align tasks exist for.

    A beat with no narration text is not a failed synthesis, it is an absent
    stem: the timeline places a Gap of the picture's length and fit falls back
    to the render's own duration. Enumerating a narration task for it would
    demand a wav no engine can honestly produce — found the day the first
    voiced slug met the silent TOC cards (2026-08-26).
    """
    return [b["id"] for b in load(slug).get("beats", [])
            if narration_text(b).strip()]


def kind(beat: dict) -> str:
    return visual_spec(beat).get("kind", "")


def locked(visual: dict, asset: Path) -> bool:
    """Whether an authored `lock` should stop this beat from regenerating.

    THE COST MODEL CHANGED. library.py classifies the media by how expensive a
    take is, and puts voice alone in the "rejected variants keep value" class
    because a performance is unrepeatable. Image-to-video moved imagery into
    that same class: a generated shot costs 25-45 minutes and is stochastic, so
    its render is NOT a cheap cache of its prompt the way a Manim render is a
    cheap cache of its scene file. Re-rolling does not get the old take back.

    So a beat can be pinned. While `lock` is set the generator reuses whatever
    is already on disk and does not run — you can retouch narration, retime, or
    rebuild the master all day without spending the GPU or losing a take you
    liked.

    The lock is honoured ONLY when the asset actually exists, so pinning a beat
    that was never rendered still renders it once rather than failing or
    silently producing a film with a hole in it.

    This is the small, reversible version of take management. The full answer is
    a takes directory with a selected flag, mirroring what voice already does.
    """
    return bool(visual.get("lock")) and asset.exists()


def of_kind(slug: str, want: str) -> list[dict]:
    """Beats one generator is responsible for."""
    return [b for b in load(slug).get("beats", []) if kind(b) == want]


def clip_ids(slug: str) -> list[str]:
    """Beats that will actually have a clip on disk, in shotlist order.

    Downstream nodes (master, timeline) must depend on THIS and not on
    beat_ids(): a beat whose visual.kind has no generator is a legitimate
    authoring state, and it must not take the whole build down with it.
    """
    return [b["id"] for b in load(slug).get("beats", []) if kind(b) in CLIP_KINDS]


def _dir(slug: str) -> Path:
    return config.video_dir(slug) / "beats"


def beat_settle(slug: str, beat_id: str) -> float:
    """This beat's own hold after its narration lands (`visual.settle`, seconds), on top of the
    slug-wide `timing.settle`. For the one beat whose last image must hang — a question mark that
    should be looked at, not glimpsed. Read from the visual fragment the clip is rendered from, so
    changing it re-renders the clip and, through the clip, re-fits it."""
    for node in ("memoryline", "animation"):
        p = fragment_path(slug, beat_id, node)
        if p.exists():
            return float((json.loads(p.read_text()).get("visual") or {}).get("settle", 0.0))
    return 0.0


def fragment_path(slug: str, beat_id: str, node: str) -> Path:
    """The slice `node` depends on for `beat_id`."""
    return _dir(slug) / f"{beat_id}.{node}.json"


def order_path(slug: str) -> Path:
    """Just the beat ids, in order. Assembly depends on this and not on the
    shotlist, so rewording narration does not re-mux the master."""
    return _dir(slug) / "_order.json"


def grade_path(slug: str) -> Path:
    """The programme-wide grade, projected out for the same reason as _order.

    The grade is authored in the shot list but consumed by `master`, and master
    depends on the timeline — which a grade change does not touch. Without this
    projection, editing the grade would rebuild nothing and you would sit
    looking at an ungraded master wondering why the setting did nothing.

    Per-slug rather than per-beat: a grade that varied by beat would be the
    precise inconsistency the pass exists to remove.
    """
    return _dir(slug) / "_grade.json"


def _write_if_changed(path: Path, payload) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def split(slug: str) -> None:
    # Projections read the EFFECTIVE document: a style's grade, letterbox and
    # pace land in the fragments, so a style edit invalidates exactly what
    # reads those bytes and nothing else.
    doc = effective(slug)
    out_dir = _dir(slug)
    out_dir.mkdir(parents=True, exist_ok=True)

    keep = {order_path(slug).name, grade_path(slug).name, music_path(slug).name,
            tools_path(slug).name}
    _write_if_changed(order_path(slug), [b["id"] for b in doc.get("beats", [])])
    # Always written, null when there is none, so the file's presence is not
    # itself a signal — master file_deps it unconditionally.
    _write_if_changed(grade_path(slug), doc.get("grade"))
    # `music` is the stem name; `sound` is the historical spelling. Both are
    # accepted at authoring time and normalise to one fragment.
    _write_if_changed(music_path(slug), doc.get("music") or doc.get("sound"))

    for beat in doc.get("beats", []):
        _warn_reserved(beat)
        for node, project in PROJECTIONS.items():
            path = fragment_path(slug, beat["id"], node)
            keep.add(path.name)
            _write_if_changed(path, project(beat, doc))

    # A beat deleted from the shotlist must not leave stale fragments behind,
    # or its assets would linger in the master forever.
    for stale in out_dir.glob("*.json"):
        if stale.name not in keep:
            stale.unlink()
