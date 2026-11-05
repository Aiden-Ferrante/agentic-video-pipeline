"""Styles: bound per project, overridden per shot list, merged UNDER the shot
list's own blocks, and worth nothing on disk — a shot list that already
carries every block yields byte-identical fragments with or without one."""
import json

import pytest
from conftest import animation_beat, shotlist

from pipeline import beats, config, projects, providers, styles

GRADE = {"aspect": 0.0, "grain": 10, "vignette": 0.12}
STYLE = {"id": "s", "version": 1, "title": "S",
         "bindings": {"timing": {"pace": 1.0, "settle": 0.7}, "letterbox": "0x1c1714",
                      "grade": GRADE, "music": {"gain_db": -9, "fade": 3.0},
                      "chapter_tag": {"enabled": True},
                      "narration": {"voice_settings": {"stability": 0.45, "style": 0.25},
                                    "voice_id": "smuggled"}}}


@pytest.fixture
def world(videos, tmp_path, monkeypatch):
    """A style library, a registry claiming `fx` for project `p`, and a shot
    list author. Returns (author_style, author_project)."""
    lib = tmp_path / "styles"
    lib.mkdir()
    monkeypatch.setattr(styles, "STYLES", lib)
    reg = tmp_path / "projects"
    reg.mkdir()
    monkeypatch.setattr(projects, "PROJECTS", reg)
    monkeypatch.setattr(projects, "VIDEOS", videos.root)

    def author_style(doc, sid="s"):
        (lib / f"{sid}.json").write_text(json.dumps(doc))

    def author_project(style=None, tools=None):
        (reg / "p.json").write_text(json.dumps(
            {"id": "p", "title": "P", "modules": [{"slug": "fx"}],
             "style": style, "tools": tools or {}}))
    return author_style, author_project


class TestBinding:
    def test_unbound_is_none_and_harmless(self, videos, world):
        videos(shotlist(animation_beat("b01")))
        assert styles.bound_id("fx") is None
        assert styles.bindings("fx") == {}
        assert styles.style_file("fx") is None
        assert beats.effective("fx") == beats.load("fx")

    def test_registry_binds_and_shot_list_overrides(self, videos, world):
        author_style, author_project = world
        author_style(STYLE)
        author_style({**STYLE, "id": "t"}, "t")
        author_project(style="s")
        videos(shotlist(animation_beat("b01")))
        assert styles.bound_id("fx") == "s"
        assert styles.identity("fx")["source"] == "registry"
        doc = shotlist(animation_beat("b01"))
        doc["style"] = "t"
        videos(doc)
        assert styles.bound_id("fx") == "t"
        assert styles.identity("fx")["source"] == "shot list"

    def test_missing_style_file_degrades_with_a_warning(self, videos, world, capsys):
        _, author_project = world
        author_project(style="ghost")
        videos(shotlist(animation_beat("b01")))
        assert styles.bindings("fx") == {}
        assert styles.style_file("fx") is None
        assert "WARNING" in capsys.readouterr().out
        assert beats.timing("fx")["settle"] == 0.0


class TestMerge:
    def test_style_fills_and_shot_list_wins_field_by_field(self, videos, world):
        author_style, author_project = world
        author_style(STYLE)
        author_project(style="s")
        doc = shotlist(animation_beat("b01"))
        doc["timing"] = {"mode": "estimate"}
        doc["grade"] = {"vignette": 0.32}
        videos(doc)
        eff = beats.effective("fx")
        assert eff["timing"] == {"mode": "estimate", "pace": 1.0, "settle": 0.7}
        assert eff["grade"] == {**GRADE, "vignette": 0.32}
        assert eff["letterbox"] == "0x1c1714"
        assert eff["chapter_tag"] == {"enabled": True}
        assert beats.timing("fx")["settle"] == 0.7

    def test_a_style_never_creates_a_bed(self, videos, world):
        author_style, author_project = world
        author_style(STYLE)
        author_project(style="s")
        videos(shotlist(animation_beat("b01")))
        assert "music" not in beats.effective("fx")
        doc = shotlist(animation_beat("b01"))
        doc["music"] = {"_note": "deliberately no bed"}
        videos(doc)
        assert beats.effective("fx")["music"] == {"_note": "deliberately no bed"}
        doc = shotlist(animation_beat("b01"))
        doc["music"] = {"bed": "gymnopedie"}
        videos(doc)
        assert beats.effective("fx")["music"] == {"bed": "gymnopedie", "gain_db": -9, "fade": 3.0}

    def test_effective_is_pure_and_the_raw_file_is_untouched(self, videos, world):
        author_style, author_project = world
        author_style(STYLE)
        author_project(style="s")
        videos(shotlist(animation_beat("b01")))
        before = beats.load("fx")
        beats.effective("fx")
        assert beats.load("fx") == before and "grade" not in beats.load("fx")


class TestDelivery:
    def test_style_delivery_underlays_and_registry_wins(self, videos, world, capsys):
        author_style, author_project = world
        author_style(STYLE)
        author_project(style="s", tools={"narration": {
            "provider": "elevenlabs", "voice_id": "v9",
            "voice_settings": {"style": 0.1}}})
        videos(shotlist(animation_beat("b01")))
        r = providers.resolve("fx", "narration")
        assert r["provider"] == "elevenlabs"
        assert r["config"]["voice_id"] == "v9"              # identity: registry
        assert r["config"]["voice_settings"] == {"stability": 0.45, "style": 0.1}
        assert "smuggled" not in json.dumps(r)              # a style binds delivery only
        assert "identity lives in the registry" in capsys.readouterr().out

    def test_other_stems_are_untouched(self, videos, world):
        author_style, author_project = world
        author_style(STYLE)
        author_project(style="s")
        videos(shotlist(animation_beat("b01")))
        assert providers.resolve("fx", "music") == {"provider": "library", "config": {}}


class TestZeroInvalidation:
    def test_fragments_are_byte_identical_with_or_without_the_style(self, videos, world):
        """The contract this cycle is held to: a shot list carrying every block
        a style supplies splits to the same bytes whether or not it is bound."""
        author_style, author_project = world
        author_style(STYLE)
        doc = shotlist(animation_beat("b01"))
        doc.update({"timing": {"mode": "estimate", "pace": 1.0, "settle": 0.7},
                    "letterbox": "0x1c1714", "grade": GRADE,
                    "music": {"bed": "x", "gain_db": -9, "fade": 3.0},
                    "chapter_tag": {"enabled": True}})
        videos(doc)
        author_project(style=None)
        beats.split("fx")
        d = config.VIDEOS / "fx" / "beats"
        plain = {p.name: p.read_bytes() for p in d.glob("*.json")}
        author_project(style="s")
        beats.split("fx")
        styled = {p.name: p.read_bytes() for p in d.glob("*.json")}
        assert plain == styled

    def test_and_a_stripped_shot_list_yields_the_same_bytes_under_the_style(self, videos, world):
        author_style, author_project = world
        author_style(STYLE)
        author_project(style="s")
        full = shotlist(animation_beat("b01"))
        full.update({"timing": {"mode": "estimate", "pace": 1.0, "settle": 0.7},
                     "letterbox": "0x1c1714", "grade": GRADE, "chapter_tag": {"enabled": True}})
        videos(full)
        beats.split("fx")
        d = config.VIDEOS / "fx" / "beats"
        before = {p.name: p.read_bytes() for p in d.glob("*.json")}
        stripped = shotlist(animation_beat("b01"))
        stripped["timing"] = {"mode": "estimate"}
        videos(stripped)
        beats.split("fx")
        after = {p.name: p.read_bytes() for p in d.glob("*.json")}
        assert before == after


def test_style_file_is_a_dep_of_beats_and_tools(videos, world, manim_root):
    import dodo
    author_style, author_project = world
    author_style(STYLE)
    author_project(style="s")
    videos(shotlist(animation_beat("b01")))
    sf = styles.style_file("fx")
    assert sf is not None
    for factory in (dodo.task_beats, dodo.task_tools):
        deps = {str(d) for t in factory() for d in t["file_dep"]}
        assert str(sf) in deps


def test_finish_merges_field_by_field_under_the_shot_list():
    from pipeline import styles
    doc = {"finish": {"acts": {"One": {}}, "dip_seconds": 1.0}}
    out = styles.effective(doc, {"finish": {"enabled": True, "dip_seconds": 2.5}})
    assert out["finish"] == {"enabled": True, "dip_seconds": 1.0, "acts": {"One": {}}}
    assert "enabled" not in doc["finish"]                 # the authored document is never modified
