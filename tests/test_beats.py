"""The invalidation contract, made executable.

Every claim here was previously verified by hand and written down in MAP.md as
prose. Prose cannot regress-detect, and this logic has already shipped one
silent-staleness bug — the kind where doit reports everything fresh and serves
stale clips, so the failure is invisible until you watch the master.
"""
import json

from conftest import animation_beat, imagery_beat, shotlist
from pipeline import beats


def mtimes(d):
    return {p.name: p.stat().st_mtime_ns for p in sorted(d.glob("*.json"))}


def test_split_emits_one_fragment_per_beat_per_projection(videos):
    d = videos(shotlist(animation_beat("b01"), animation_beat("b02")))
    beats.split("fx")

    names = {p.name for p in (d / "beats").glob("*.json")}
    # Two PROGRAMME-level fragments sit beside the per-beat ones. `_grade.json`
    # joined `_order.json` when the grade moved into the shot list: it is
    # per-programme by design (one film stock over the whole thing, applied to
    # the concatenated picture so the grain field does not reset at every cut),
    # so it cannot be a per-beat projection. It is emitted unconditionally —
    # a slug with no grade writes a null fragment rather than no fragment, so
    # that REMOVING a grade invalidates the master the same as changing one.
    # `_music.json` joined for the same reason as `_grade.json`: the authored
    # bed/gain/fade is slug-level, task_sound file_deps it, and it is emitted
    # unconditionally so adding a bed changes bytes rather than the dep SET.
    expected = {"_order.json", "_grade.json", "_music.json"} | {
        f"{bid}.{node}.json" for bid in ("b01", "b02") for node in beats.PROJECTIONS
    }
    assert names == expected
    assert json.loads((d / "beats" / "_order.json").read_text()) == ["b01", "b02"]


def test_untouched_fragments_keep_their_mtime(videos):
    """write-if-changed is the whole mechanism. If an unchanged fragment gets a
    new mtime, doit rebuilds every downstream asset on every run."""
    d = videos(shotlist(animation_beat("b01"), animation_beat("b02")))
    beats.split("fx")
    before = mtimes(d / "beats")

    beats.split("fx")
    assert mtimes(d / "beats") == before


def test_rewording_narration_does_not_touch_the_animation_projection(videos):
    """The per-NODE split exists for exactly this. A whole-beat fragment would
    re-render an animation that never reads vo_text."""
    d = videos(shotlist(animation_beat("b01", vo="first wording")))
    beats.split("fx")
    before = mtimes(d / "beats")

    videos(shotlist(animation_beat("b01", vo="completely different wording")))
    beats.split("fx")
    after = mtimes(d / "beats")

    assert after["b01.vo.json"] != before["b01.vo.json"]
    assert after["b01.animation.json"] == before["b01.animation.json"]


def test_editing_one_beat_leaves_the_others_alone(videos):
    d = videos(shotlist(animation_beat("b01"), animation_beat("b02")))
    beats.split("fx")
    before = mtimes(d / "beats")

    videos(shotlist(animation_beat("b01"), animation_beat("b02", scene="Changed")))
    beats.split("fx")
    after = mtimes(d / "beats")

    assert after["b02.animation.json"] != before["b02.animation.json"]
    assert after["b01.animation.json"] == before["b01.animation.json"]


def test_project_fps_lives_inside_the_animation_projection(videos):
    """Changing project fps must invalidate every clip, and it only can if the
    value is inside the bytes doit hashes."""
    videos(shotlist(animation_beat("b01"), fps=30))
    beats.split("fx")
    d = videos.root / "fx" / "beats"
    before = mtimes(d)

    videos(shotlist(animation_beat("b01"), fps=60))
    beats.split("fx")
    assert mtimes(d)["b01.animation.json"] != before["b01.animation.json"]


def test_deleting_a_beat_prunes_its_fragments(videos):
    d = videos(shotlist(animation_beat("b01"), animation_beat("b02")))
    beats.split("fx")
    assert (d / "beats" / "b02.animation.json").exists()

    videos(shotlist(animation_beat("b01")))
    beats.split("fx")

    assert not (d / "beats" / "b02.animation.json").exists()
    assert not (d / "beats" / "b02.vo.json").exists()
    assert json.loads((d / "beats" / "_order.json").read_text()) == ["b01"]


def test_clip_ids_skips_beats_no_generator_produces(videos):
    """The dodo.py:66/80 bug. beat_ids() includes every beat; downstream
    file_dep must use clip_ids() or doit demands a target nothing creates.

    `none` has no generator — exactly the state that used to break the build.
    Imagery does have one now, so it counts as a clip.
    """
    videos(shotlist(
        animation_beat("b01"),
        {"id": "b02", "vo_text": "held frame", "visual": {"kind": "none"}},
        imagery_beat("b03"),
    ))
    assert beats.beat_ids("fx") == ["b01", "b02", "b03"]
    assert beats.clip_ids("fx") == ["b01", "b03"]


def test_of_kind_partitions_beats_by_generator(videos):
    videos(shotlist(animation_beat("b01"), imagery_beat("b02"), animation_beat("b03")))
    assert [b["id"] for b in beats.of_kind("fx", "animation")] == ["b01", "b03"]
    assert [b["id"] for b in beats.of_kind("fx", "imagery")] == ["b02"]
