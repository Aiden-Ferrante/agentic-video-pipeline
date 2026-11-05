"""Task enumeration over a mixed-kind shot list.

The regression this exists for: `task_master` and `task_timeline` built their
file_dep from every beat, while only animation beats had a producing task. The
first non-animation beat therefore made doit demand a target nothing creates,
and doit fails the whole build rather than that one beat.
"""
import json

import dodo
from conftest import animation_beat, brief_beat, imagery_beat, memoryline_beat, shotlist

from pipeline import beats

# Every task factory that participates in the produced/required graph.
FACTORIES = (dodo.task_beats, dodo.task_animation, dodo.task_memoryline, dodo.task_imagery, dodo.task_brief,
             dodo.task_narration,
             dodo.task_align, dodo.task_fit, dodo.task_timeline, dodo.task_music,
             dodo.task_captions, dodo.task_master)


def deps(task):
    return {p.name if hasattr(p, "name") else str(p) for p in task.get("file_dep", [])}


def by_name(gen):
    return {t["name"]: t for t in gen}


def test_every_clip_depended_on_is_some_task_target(videos, manim_root):
    """The invariant the bug violated: any clip a downstream task depends on
    must be produced by an upstream one. `none` stands in for any kind that is
    authored ahead of its node."""
    videos(shotlist(animation_beat("b01"), imagery_beat("b02"),
                    {"id": "b03", "vo_text": "held", "visual": {"kind": "none"}}))

    produced, required = set(), set()
    for factory in FACTORIES:
        for task in factory():
            produced |= {str(t) for t in task.get("targets", [])}
            required |= {str(d) for d in task.get("file_dep", [])}

    # Authored inputs and Manim sources are never targets; clips always are.
    assert {r for r in required if "/assets/" in r} - produced == set()


def test_clip_kinds_never_promises_a_generator_that_does_not_exist(videos, manim_root):
    """CLIP_KINDS drives every downstream file_dep. If it names a kind with no
    task, the build breaks the moment such a beat is authored — so guard the
    tuple itself rather than waiting to rediscover it."""
    builders = {"animation": animation_beat, "memoryline": memoryline_beat, "imagery": imagery_beat,
                "brief": brief_beat}
    videos(shotlist(*[
        builders[k](f"b{i:02d}") for i, k in enumerate(beats.CLIP_KINDS, start=1)
    ]))
    enumerated = set()
    for factory in FACTORIES:
        for task in factory():
            enumerated |= {str(t) for t in task.get("targets", [])}
    for bid in beats.clip_ids("fx"):
        assert str(dodo.config.asset_path("fx", bid)) in enumerated


def test_fit_ignores_a_beat_with_no_generator(videos, manim_root):
    videos(shotlist(animation_beat("b01"),
                    {"id": "b02", "vo_text": "held", "visual": {"kind": "none"}}))
    assert set(by_name(dodo.task_fit())) == {"fx/b01"}


def test_timeline_ignores_a_beat_with_no_generator(videos, manim_root):
    videos(shotlist(animation_beat("b01"),
                    {"id": "b02", "vo_text": "held", "visual": {"kind": "none"}}))
    assert "b02.fit.mp4" not in deps(by_name(dodo.task_timeline())["fx"])


def test_an_imagery_beat_gets_its_own_task_and_reaches_the_timeline(videos, manim_root):
    videos(shotlist(animation_beat("b01"), imagery_beat("b02")))
    assert set(by_name(dodo.task_imagery())) == {"fx/b02"}
    assert deps(by_name(dodo.task_timeline())["fx"]) >= {"b01.fit.mp4", "b02.fit.mp4"}


def test_master_depends_on_the_timeline_the_grade_and_the_clips(videos, manim_root):
    """Master reads three kinds of input and all three must invalidate it.

    This test used to assert `deps == {"fx.otio"}`, on the reasoning that the
    timeline governs. It governs the EDIT. The master is rendered from the media
    the timeline points at, so a clip whose CONTENT changed while its DURATION
    did not left the .otio byte-identical and the master silently stale —
    swapping thirteen stills for generated motion produced identical durations,
    doit correctly saw no dependency change, and the film on disk stayed an hour
    older than its own footage while reporting success.

    So the original claim is asserted as a floor, not an equality: the timeline
    must still be there (that is what makes a retime reach the render), and the
    clips and the grade must be there too.
    """
    videos(shotlist(animation_beat("b01"), animation_beat("b02")))
    master = by_name(dodo.task_master())["fx"]
    assert deps(master) == {"fx.otio", "_grade.json", "b01.fit.mp4", "b02.fit.mp4"}


def test_master_reads_the_fitted_clips_not_the_raw_renders(videos, manim_root):
    """The clips it depends on are the FITTED ones — the media the timeline
    actually points at. Depending on `assets/<beat>.mp4` instead would track a
    file the master never reads and miss the refit when a duration changes."""
    videos(shotlist(animation_beat("b01")))
    d = deps(by_name(dodo.task_master())["fx"])
    assert "b01.fit.mp4" in d
    assert "b01.mp4" not in d


def test_master_depends_on_the_promoted_edit_once_it_exists(videos, manim_root):
    """A promoted timeline governs, so editing it must rebuild the master."""
    d = videos(shotlist(animation_beat("b01")))
    (d / "out").mkdir(parents=True, exist_ok=True)
    (d / "out" / "fx.edit.otio").write_text("{}")
    # A floor, not an equality: what this test is about is that promoting adds
    # the edit copy. Pinning the whole set here would make every future change
    # to master's inputs fail this test for a reason it does not care about,
    # which is what happened when the grade and the clips were added.
    assert deps(by_name(dodo.task_master())["fx"]) >= {"fx.otio", "fx.edit.otio"}


def test_fit_always_depends_on_the_alignment(videos, manim_root):
    """The narration governs the clip's length, so the alignment must be a
    dependency — and unconditionally, even before it exists."""
    videos(shotlist(animation_beat("b01")))
    assert deps(by_name(dodo.task_fit())["fx/b01"]) == {"b01.mp4", "b01.align.json"}


def test_dependency_sets_do_not_change_once_the_build_has_run(videos, manim_root):
    """doit treats a changed file_dep SET as a change, so a dep added only
    'if it exists' leaves the task stale immediately after a successful build
    and the pipeline never comes to rest. Enumerate identically both times."""
    d = videos(shotlist(animation_beat("b01"), animation_beat("b02")))

    def snapshot():
        return {t["name"]: sorted(str(p) for p in t.get("file_dep", []))
                for f in FACTORIES for t in f()}

    before = snapshot()
    # Simulate a completed build: every declared target now exists.
    for name, targets in ((t["name"], t.get("targets", []))
                          for f in FACTORIES for t in f()):
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")
    assert snapshot() == before


def test_animation_task_depends_on_its_scene_import_closure(videos, manim_root):
    videos(shotlist(animation_beat("b01", scene_file="b.py")))
    task = by_name(dodo.task_animation())["fx/b01"]
    assert deps(task) >= {"b01.animation.json", "b.py", "helpers.py", "scm_style.py"}


def test_animation_tasks_are_only_yielded_for_animation_beats(videos, manim_root):
    videos(shotlist(animation_beat("b01"), imagery_beat("b02")))
    assert set(by_name(dodo.task_animation())) == {"fx/b01"}


def test_master_does_not_depend_on_the_vo_projection(videos, manim_root):
    """Rewording narration must not re-mux the master; that is why assembly
    depends on _order.json rather than on the shot list."""
    videos(shotlist(animation_beat("b01")))
    assert "b01.vo.json" not in deps(by_name(dodo.task_master())["fx"])


class TestMusicFragment:
    """The gain-edit contract: authored music drives the bed through bytes.

    sound.spec used to read shotlist.json live, so task_sound had no dep on
    the authored gain/fade and editing gain_db rebuilt nothing. The _music.json
    projection is the fix; these pin it.
    """

    def test_editing_the_gain_changes_the_music_fragment(self, videos):
        d = videos(shotlist(animation_beat("b01")))
        doc = json.loads((d / "shotlist.json").read_text())
        doc["sound"] = {"bed": "quiet-strings", "gain_db": -22}
        (d / "shotlist.json").write_text(json.dumps(doc))
        beats.split("fx")
        before = (d / "beats" / "_music.json").read_text()

        doc["sound"]["gain_db"] = -16
        (d / "shotlist.json").write_text(json.dumps(doc))
        beats.split("fx")
        after = (d / "beats" / "_music.json").read_text()
        assert before != after

    def test_task_sound_file_deps_the_music_fragment(self, videos, monkeypatch, tmp_path):
        d = videos(shotlist(animation_beat("b01")))
        doc = json.loads((d / "shotlist.json").read_text())
        doc["sound"] = {"bed": "quiet-strings"}
        (d / "shotlist.json").write_text(json.dumps(doc))
        # a bed source with a sidecar, so the task is yielded at all
        lib = tmp_path / "soundlib"
        lib.mkdir()
        (lib / "quiet-strings.wav").write_bytes(b"RIFF")
        (lib / "quiet-strings.wav.json").write_text(json.dumps(
            {"rights": {"spdx_license": "CC0-1.0"}}))
        from pipeline import sound
        monkeypatch.setattr(sound, "library_dir", lambda: lib)
        tasks = by_name(dodo.task_music())
        assert "_music.json" in deps(tasks["fx"])

    def test_the_music_stem_key_is_accepted_alongside_the_legacy_sound_key(self, videos):
        d = videos(shotlist(animation_beat("b01")))
        doc = json.loads((d / "shotlist.json").read_text())
        doc["music"] = {"bed": "quiet-strings"}
        (d / "shotlist.json").write_text(json.dumps(doc))
        beats.split("fx")
        from pipeline import sound
        assert sound.spec("fx")["bed"] == "quiet-strings"

    def test_no_music_block_writes_a_null_fragment_not_no_fragment(self, videos):
        d = videos(shotlist(animation_beat("b01")))
        beats.split("fx")
        assert json.loads((d / "beats" / "_music.json").read_text()) is None


def test_the_canvas_runnable_table_names_real_task_groups():
    """serve.py's RUNNABLE values must be dodo group names — the two lists are
    maintained by hand in different files, and a rename that touches one but
    not the other turns a canvas build button into a silent 404."""
    import re
    from pathlib import Path
    src = (Path(__file__).parent.parent / "map" / "serve.py").read_text()
    block = re.search(r"RUNNABLE = \{(.*?)\}", src, re.S).group(1)
    promised = set(re.findall(r':\s*"([a-z_]+)"', block))
    real = {f.__name__.removeprefix("task_") for f in FACTORIES}
    real |= {"short", "thumbnail", "publish", "program"}  # groups outside FACTORIES
    missing = promised - real
    assert not missing, f"canvas offers builds no task provides: {missing}"


def test_a_memoryline_beat_is_timed_by_its_narration_and_reaches_the_timeline(videos, manim_root):
    """The shot syncs to the words, so the timing authority is a dep — the same one fit uses."""
    videos(shotlist(animation_beat("b01"), memoryline_beat("b02")))
    tasks = by_name(dodo.task_memoryline())
    assert set(tasks) == {"fx/b02"}
    assert {"b02.memoryline.json"} <= deps(tasks["fx/b02"])
    fit_deps = deps(by_name(dodo.task_fit())["fx/b02"])
    timing = fit_deps - {"b02.mp4"}
    assert timing and timing <= deps(tasks["fx/b02"])
    assert deps(by_name(dodo.task_timeline())["fx"]) >= {"b01.fit.mp4", "b02.fit.mp4"}


def _finishing(videos):
    beat = lambda bid, ch: {**animation_beat(bid), "chapter": ch}
    doc = shotlist(beat("b01", "One"), beat("b02", "One"), beat("b03", "Two"))
    doc["finish"] = {"enabled": True}
    videos(doc)


def test_finish_tasks_chain_from_fit_clips_to_the_film(videos, manim_root):
    """Every file a finish task needs is produced by some task — so `doit film:<slug>` builds the lot."""
    _finishing(videos)
    produced, required = set(), set()
    for factory in (*FACTORIES, dodo.task_fit, dodo.task_acts, dodo.task_score, dodo.task_actpic,
                    dodo.task_actmix, dodo.task_film):
        for task in factory():
            produced |= {str(t) for t in task.get("targets", [])}
            required |= {str(d) for d in task.get("file_dep", [])}
    derived = {r for r in required if "/out/finish/" in r or "/assets/" in r}
    assert derived - produced == set()
    film = by_name(dodo.task_film())
    assert set(film) == {"fx"} and {"act01-one.mp4", "act02-two.mp4"} <= deps(film["fx"])


def test_the_score_depends_on_timing_never_on_picture(videos, manim_root):
    _finishing(videos)
    for task in dodo.task_score():
        names = deps(task)
        assert not any(n.endswith(".mp4") for n in names), names
        assert any(n.endswith(".json") and n.startswith("act0") for n in names)


def test_a_video_that_does_not_opt_in_has_no_finish_tasks(videos, manim_root):
    videos(shotlist(animation_beat("b01")))
    assert list(dodo.task_acts()) == [] and list(dodo.task_film()) == []


def test_a_cue_whose_word_is_gone_is_refused(videos, manim_root):
    from pipeline import finish
    beat = {**animation_beat("b01", vo="Maybe none of these. These, then those?"), "chapter": "One",
            "sfx": [{"word": "these", "nth": 1, "fx": "deny", "gain": -17},     # said twice: fine
                    {"word": "those", "nth": 1, "fx": "deny", "gain": -17},     # said once: #1 is not there
                    {"word": "gone", "fx": "tick", "gain": -15},                # never said
                    {"word": None, "fx": "tick", "gain": -15},                  # the beat's start: no word to check
                    {"word": "those", "nth": -1, "fx": "arc", "gain": -16}]}   # the last one: fine
    videos({**shotlist(beat), "finish": {"enabled": True}})
    bad = finish.cue_problems("fx")
    assert len(bad) == 2 and "'those' (#1)" in bad[0] and "'gone'" in bad[1]
    cues = by_name(dodo.task_cues())["fx"]
    assert cues["actions"][0]() is False
    assert by_name(dodo.task_film())["fx"]["task_dep"] == ["cues:fx"]


def test_score_spec_takes_the_template_tail(videos, manim_root):
    from pipeline import finish
    _finishing(videos)
    st = {"score": {"tail": ", E-flat minor"}}
    spec = {"acts": {"One": {"score": {"mood": "pads", "seed": 101}},
                     "Two": {"score": {"arc": {"_note": "x", "steps": [{"prompt": "@take", "seed": 1},
                                                                       {"prompt": "dark", "seed": 2}], "gain": []}}}}}
    one, two = finish.acts("fx")
    assert finish.score_spec("fx", one, st, spec) == {"prompt": "pads, E-flat minor", "seed": 101, "open_with_take": False}
    arc = finish.score_spec("fx", two, st, spec)["arc"]
    assert [s["prompt"] for s in arc["steps"]] == ["@take", "dark, E-flat minor"] and "_note" not in arc
