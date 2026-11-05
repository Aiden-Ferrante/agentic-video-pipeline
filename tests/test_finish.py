"""The finish stage (video-pipeline/finish/) must not reach outside the repo for code or inputs."""
import pathlib, re

FINISH = pathlib.Path(__file__).resolve().parents[1] / "finish"


def test_no_script_hard_codes_a_home_folder():
    bad = []
    for p in FINISH.glob("*.py"):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if re.search(r"/home/|Desktop|out/ml|stable-audio-3/outputs", line) and not line.lstrip().startswith("#"):
                bad.append(f"{p.name}:{i}: {line.strip()}")
    assert not bad, "\n".join(bad)


def test_paths_write_under_the_videos_ignored_out(monkeypatch):
    import importlib, sys
    monkeypatch.setenv("FINISH_SLUG", "fixture-slug")
    sys.path.insert(0, str(FINISH))
    try:
        paths = importlib.reload(importlib.import_module("paths"))
        assert paths.WORK.relative_to(paths.VP).parts[:3] == ("videos", "fixture-slug", "out")
        assert paths.FILM.parent == paths.WORK
        assert paths.BASE_TAKE.with_name(paths.BASE_TAKE.name + ".json").exists()   # the score's take carries a sidecar
    finally:
        sys.path.remove(str(FINISH))
        import shutil; shutil.rmtree(paths.VP / "videos" / "fixture-slug", ignore_errors=True)


def test_cards_open_and_tails_close_each_act(monkeypatch):
    """2026-10-09: each act is entered on its title card and left on a held frame, so the dip never fades the voice."""
    from pipeline import beats, finish
    doc = {"beats": [{"id": "a", "chapter": "The Line", "vo_text": "x", "sfx": [{"word": "x", "fx": "hit", "gain": 0}]},
                     {"id": "b", "chapter": "Why Squared", "vo_text": "y"}]}
    monkeypatch.setattr(beats, "load", lambda slug: doc)
    monkeypatch.setattr(finish, "settings", lambda slug: {"dip_seconds": 2.5, "card": {"enabled": True, "hold": 3.0}})
    acts = finish.acts("fx")
    assert [a["ids"] for a in acts] == [["_card01", "a", "_tail01"], ["_card02", "b", "_tail02"]]
    one, two = (finish.card_spec("fx", a) for a in acts)
    assert (one["label"], one["seconds"], one["tail"]) == ("ACT ONE", 4.0, 3.1)        # the film opens without a dip
    assert (two["label"], two["title"], two["seconds"], two["tail"]) == ("ACT TWO", "Why Squared", 5.5, 0.6)
    assert finish.act_cues("fx", acts[0], {})["cues"] == [["a", "x", 0, "hit", 0, 0.0]]
