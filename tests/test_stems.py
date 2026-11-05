"""The stem vocabulary and the sugar/stems equivalence contract.

The load-bearing claim: a beat authored with the flat sugar (`vo_text`,
`visual`) and the same beat authored with a `stems` map produce BYTE-IDENTICAL
projection fragments. That equality is what makes a future shotlist migration
free — fragment bytes are the invalidation contract, so identical bytes means
zero re-renders.
"""
import json

from pipeline import beats, stems


def _sugar_beat():
    return {"id": "b01", "vo_text": "A line of narration.",
            "visual": {"kind": "animation", "scene_file": "a.py", "scene": "A"}}


def _stems_beat():
    return {"id": "b01",
            "stems": {"narration": {"text": "A line of narration."},
                      "visuals": {"kind": "animation", "scene_file": "a.py",
                                  "scene": "A"}}}


DOC = {"fps": 30, "resolution": [1920, 1080],
       "timing": {"mode": "estimate", "pace": 1.0}}


class TestSugarStemsEquivalence:
    def test_every_projection_is_byte_identical_across_the_two_forms(self):
        for node, project in beats.PROJECTIONS.items():
            a = json.dumps(project(_sugar_beat(), DOC), sort_keys=True)
            b = json.dumps(project(_stems_beat(), DOC), sort_keys=True)
            assert a == b, f"projection {node!r} diverges between forms"

    def test_kind_agrees_across_the_two_forms(self):
        assert beats.kind(_sugar_beat()) == beats.kind(_stems_beat()) == "animation"


class TestAccessors:
    def test_stems_form_wins_over_sugar_when_both_present(self):
        beat = {**_sugar_beat(), "stems": {"narration": {"text": "the stems text"}}}
        assert beats.narration_text(beat) == "the stems text"

    def test_missing_everything_reads_as_empty(self):
        assert beats.narration_text({"id": "b01"}) == ""
        assert beats.visual_spec({"id": "b01"}) == {}

    def test_an_explicitly_empty_stems_block_is_respected_not_fallen_through(self):
        # {"narration": {}} says "this beat HAS a narration entry and it is
        # empty" — falling back to sugar here would make absence inexpressible.
        beat = {"id": "b01", "vo_text": "sugar", "stems": {"narration": {}}}
        assert beats.narration_text(beat) == ""


class TestReservedBeatStems:
    def test_per_beat_music_warns_and_is_ignored(self, capsys, videos):
        doc = {"slug": "fx", "fps": 30, "resolution": [1920, 1080],
               "beats": [{"id": "b01", "vo_text": "x",
                          "visual": {"kind": "animation", "scene_file": "a.py",
                                     "scene": "A"},
                          "stems": {"music": {"bed": "strings"}}}]}
        videos(doc)
        beats.split("fx")
        out = capsys.readouterr().out
        assert "reserved" in out and "music" in out


class TestVocabularyTables:
    def test_every_stem_has_a_layout_or_is_captions(self):
        for stem in stems.STEMS:
            assert stem == "captions" or stem in stems.TRACK_LAYOUT

    def test_clip_name_round_trips_including_legacy(self):
        assert stems.split_clip_name(stems.clip_name("b07", "narration")) == \
            ("b07", "narration")
        assert stems.split_clip_name("b07.vo") == ("b07", "narration")
        assert stems.split_clip_name("b07") == ("b07", None)
        # an unrecognised suffix is part of the name, not a stem
        assert stems.split_clip_name("b07.weird") == ("b07.weird", None)

    def test_stem_of_reads_metadata_then_legacy_names(self):
        class T:
            def __init__(self, name, meta=None):
                self.name, self.metadata = name, meta or {}
        assert stems.stem_of(T("video")) == "visuals"
        assert stems.stem_of(T("voice")) == "narration"
        assert stems.stem_of(T("anything",
                              {"pipeline": {"stem": "music"}})) == "music"
        assert stems.stem_of(T("unrelated")) is None


class TestSettle:
    """The picture holds after the line lands; silence never gets the bonus."""

    def test_estimate_mode_adds_settle_to_narrated_beats(self, videos):
        import json
        from pipeline import beats, fit
        doc = {"slug": "fx", "fps": 30, "resolution": [1920, 1080],
               "timing": {"mode": "estimate", "pace": 1.0, "settle": 0.75},
               "beats": [{"id": "b01", "vo_text": "A line of narration here.",
                          "visual": {"kind": "animation", "scene_file": "a.py",
                                     "scene": "A"}}]}
        videos(doc)
        beats.split("fx")
        from pipeline import reading
        base = reading.estimate("A line of narration here.", 1.0)
        assert fit.target_seconds("fx", "b01") == base + 0.75

    def test_a_beat_can_hold_longer_than_the_rest(self, videos):
        """visual.settle stacks on the slug-wide settle, for that beat only."""
        from pipeline import beats, fit, memoryline, reading
        line = "A line of narration here."
        vis = lambda **k: {"kind": "memoryline", "shot": "shots/x.py:X", **k}
        doc = {"slug": "fx", "fps": 30, "resolution": [1920, 1080],
               "timing": {"mode": "estimate", "pace": 1.0, "settle": 0.75},
               "beats": [{"id": "b01", "vo_text": line, "visual": vis(settle=2.5)},
                         {"id": "b02", "vo_text": line, "visual": vis()}]}
        videos(doc)
        beats.split("fx")
        base = reading.estimate(line, 1.0)
        assert fit.target_seconds("fx", "b01") == base + 0.75 + 2.5
        assert fit.target_seconds("fx", "b02") == base + 0.75
        # the render is sized by the same rule, so fit never has to hold a frame
        assert memoryline.seconds_for("fx", "b01", doc["beats"][0]["visual"]) == base + 0.75 + 2.5

    def test_a_silent_beat_keeps_its_rendered_length(self, videos, monkeypatch):
        from pipeline import assemble, beats, fit
        doc = {"slug": "fx", "fps": 30, "resolution": [1920, 1080],
               "timing": {"mode": "estimate", "pace": 1.0, "settle": 0.75},
               "beats": [{"id": "t00", "vo_text": "",
                          "visual": {"kind": "animation", "scene_file": "a.py",
                                     "scene": "A"}}]}
        videos(doc)
        beats.split("fx")
        monkeypatch.setattr(assemble, "duration", lambda p: 8.5)
        assert fit.target_seconds("fx", "t00") == 8.5
