"""The Gen Lab's two halves: the recipes are data, the pool rewrites a shot list.

The recipe tests are structural, not aesthetic — they cannot tell you an image
looks good. What they CAN catch is the failure this design is most exposed to:
a graph that references a node output nothing produces, which ComfyUI answers
with a 400 that only shows up when you press Generate.
"""
import json

import pytest

from pipeline import config, library, pool, recipes
from tests.conftest import animation_beat, shotlist


# ------------------------------------------------------------------ recipes

@pytest.mark.parametrize("key", sorted(recipes.IMAGE_RECIPES))
def test_image_graphs_are_internally_consistent(key):
    g = recipes.image_graph(key, "a test prompt", 7)
    _assert_wiring(g)
    assert any(n["class_type"].startswith("Save") for n in g.values())


@pytest.mark.parametrize("key", sorted(recipes.MUSIC_RECIPES))
def test_music_graphs_are_internally_consistent(key):
    g = recipes.music_graph(key, "warm pads, no drums", 3, seconds=30)
    _assert_wiring(g)


def _assert_wiring(graph):
    """Every link must name a node that exists in the same graph."""
    for node_id, node in graph.items():
        for field, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                target, slot = value
                assert target in graph, (
                    f"{node_id}.{field} links to missing node {target!r}")
                assert isinstance(slot, int)


def test_music_graphs_force_instrumental():
    """An empty lyrics field makes ACE-Step sing. A bed must never sing."""
    for key in recipes.MUSIC_RECIPES:
        g = recipes.music_graph(key, "tags", 0)
        assert g["pos"]["inputs"]["lyrics"] == recipes.INSTRUMENTAL


def test_each_recipe_keeps_its_own_step_count():
    """Distilled and full tiers differ by an order of magnitude; collapsing
    them onto one default is the silent way to make a model look bad."""
    assert recipes.IMAGE_RECIPES["flux2-klein-4b"]["steps"] == 4
    assert recipes.IMAGE_RECIPES["hidream-o1"]["steps"] == 28
    assert recipes.MUSIC_RECIPES["ace-turbo"]["steps"] == 8
    assert recipes.MUSIC_RECIPES["ace-xl-sft"]["steps"] == 50


def test_missing_reports_absent_weights():
    r = recipes.IMAGE_RECIPES["flux2-klein-4b"]
    assert recipes.missing(r, {"unets": [], "text_encoders": [], "vaes": []})
    have = {"unets": ["flux-2-klein-4b.safetensors"],
            "text_encoders": ["qwen_3_4b.safetensors"],
            "vaes": ["flux2-vae.safetensors"]}
    assert recipes.missing(r, have) == []


def test_unknown_recipe_is_refused():
    with pytest.raises(ValueError):
        recipes.image_graph("no-such-model", "p", 0)
    with pytest.raises(ValueError):
        recipes.music_graph("no-such-model", "p", 0)


# --------------------------------------------------------------------- pool

@pytest.fixture
def batch(videos):
    videos(shotlist(animation_beat("b01"), animation_beat("b02")))
    pool.start_batch("fx", "imagery", "B1",
                     {"recipe": "flux2-klein-4b", "prompt": "a chalkboard",
                      "count": 2, "seed_start": 10, "width": 1024, "height": 1024,
                      "seconds": 5.0})
    for i in range(2):
        pool.add_variant("fx", "imagery", "B1", i, b"\x89PNG-fake",
                         recipe="flux2-klein-4b", prompt="a chalkboard", seed=10 + i)
    return "B1"


def test_variants_start_unselected_with_a_model_sidecar(batch):
    b = pool.batches("fx", "imagery")[0]
    assert [v["seed"] for v in b["variants"]] == [10, 11]
    assert not any(v["selected"] for v in b["variants"])
    side = library.read(pool.batch_dir("fx", "imagery", "B1") / "v00.png")
    assert side["rights"]["spdx_license"] == pool.MODEL_OUTPUT_SPDX
    assert side["provenance"]["digital_source_type"] == "trainedAlgorithmicMedia"


def test_commit_writes_the_recipe_into_the_beat(batch):
    r = pool.commit("fx", "imagery", "B1", "v01.png", beat_id="b02")
    assert r["ok"], r
    doc = json.loads((config.video_dir("fx") / "shotlist.json").read_text())
    beat = [b for b in doc["beats"] if b["id"] == "b02"][0]
    # The shot list must carry enough to REBUILD, not a path to a blob.
    assert beat["visual"] == {"kind": "imagery", "source": "generate",
                              "recipe": "flux2-klein-4b", "prompt": "a chalkboard",
                              "seed": 11, "width": 1024, "height": 1024,
                              "seconds": 5.0}
    # and the untouched beat stays untouched
    other = [b for b in doc["beats"] if b["id"] == "b01"][0]
    assert other["visual"]["kind"] == "animation"


def test_commit_leaves_exactly_one_winner(batch):
    pool.commit("fx", "imagery", "B1", "v00.png", beat_id="b01")
    pool.commit("fx", "imagery", "B1", "v01.png", beat_id="b01")
    flags = [v["selected"] for v in pool.batches("fx", "imagery")[0]["variants"]]
    assert flags == [False, True]


def test_selected_finds_the_pick_by_its_parameters(batch):
    assert pool.selected("fx", "imagery", recipe="flux2-klein-4b",
                         prompt="a chalkboard", seed=10) is None
    pool.commit("fx", "imagery", "B1", "v00.png", beat_id="b01")
    got = pool.selected("fx", "imagery", recipe="flux2-klein-4b",
                        prompt="a chalkboard", seed=10)
    assert got is not None and got.name == "v00.png"
    # a different seed is a different take, and must not resolve
    assert pool.selected("fx", "imagery", recipe="flux2-klein-4b",
                         prompt="a chalkboard", seed=99) is None


def test_commit_preserves_authored_motion(videos):
    doc = shotlist({"id": "b01", "vo_text": "x",
                    "visual": {"kind": "imagery", "source": "commons",
                               "motion": {"kind": "defocus", "strength": 6}}})
    videos(doc)
    pool.start_batch("fx", "imagery", "B1", {"recipe": "flux2-klein-4b",
                                             "prompt": "p", "count": 1,
                                             "seed_start": 0})
    pool.add_variant("fx", "imagery", "B1", 0, b"x", recipe="flux2-klein-4b",
                     prompt="p", seed=0)
    pool.commit("fx", "imagery", "B1", "v00.png", beat_id="b01")
    out = json.loads((config.video_dir("fx") / "shotlist.json").read_text())
    # Motion is a directing choice about the shot, not a property of the take.
    assert out["beats"][0]["visual"]["motion"] == {"kind": "defocus", "strength": 6}


def test_commit_refuses_an_unknown_beat(batch):
    assert not pool.commit("fx", "imagery", "B1", "v00.png", beat_id="nope")["ok"]
    assert not pool.commit("fx", "imagery", "B1", "v00.png")["ok"]


def test_sound_commit_writes_the_programme_block(videos):
    videos(shotlist(animation_beat("b01")))
    pool.start_batch("fx", "sound", "S1", {"recipe": "ace-xl-sft", "tags": "warm pads",
                                           "count": 1, "seed_start": 4,
                                           "seconds": 66.0, "bpm": 84,
                                           "keyscale": "C major"})
    pool.add_variant("fx", "sound", "S1", 0, b"fLaC", recipe="ace-xl-sft",
                     prompt="warm pads", seed=4)
    assert pool.commit("fx", "sound", "S1", "v00.flac")["ok"]
    doc = json.loads((config.video_dir("fx") / "shotlist.json").read_text())
    assert doc["sound"]["source"] == "generate"
    assert doc["sound"]["tags"] == "warm pads"
    assert doc["sound"]["seed"] == 4
    assert doc["sound"]["seconds"] == 66.0
