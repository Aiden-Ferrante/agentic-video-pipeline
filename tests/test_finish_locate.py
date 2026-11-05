"""finish.locate: a film timecode → act, beat, offset — with the dip overlap film.py uses."""
import json

from pipeline import beats, finish


def _film(tmp_path, monkeypatch):
    work = tmp_path / "finish"; work.mkdir()
    acts = [{"slug": "act01-a", "chapter": "A", "seconds": 20.0, "beats": {"b1": [0.0, 12.0], "b2": [12.0, 8.0]}},
            {"slug": "act02-b", "chapter": "B", "seconds": 10.0, "beats": {"b3": [0.0, 10.0]}}]
    (work / "acts.json").write_text(json.dumps(acts))
    (work / "settings.json").write_text(json.dumps({"dip_seconds": 2.0}))
    monkeypatch.setattr(finish, "work", lambda slug: work)
    monkeypatch.setattr(finish, "_act_seconds", lambda slug, a: a["seconds"])
    monkeypatch.setattr(beats, "load", lambda slug: {"beats": [{"id": "b2", "sfx": [{"word": "x"}]}]})


def test_timecodes_parse():
    assert finish.seconds_of("4:12") == 252 and finish.seconds_of("1:02:03") == 3723 and finish.seconds_of(7.5) == 7.5


def test_a_moment_maps_to_its_beat_and_the_dip_names_both_acts(tmp_path, monkeypatch):
    _film(tmp_path, monkeypatch)
    r = finish.locate("fx", "0:13")
    assert (r["act"], r["beat"], r["beat_offset"], r["in_dip"]) == ("act01-a", "b2", 1.0, None)
    assert r["sfx"] == [{"word": "x"}]
    r = finish.locate("fx", 19)                       # act 2 starts at 20 - 2 = 18: inside the dip, act 2 wins
    assert (r["act"], r["beat"], r["beat_offset"]) == ("act02-b", "b3", 1.0)
    assert r["in_dip"] == "dip from act01-a into act02-b"
    assert "outside" in finish.locate("fx", 29)["error"]   # film = 20 + 10 - 2 = 28 s
