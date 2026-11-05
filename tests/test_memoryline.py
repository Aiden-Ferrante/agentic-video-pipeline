"""The memoryline node's two timing helpers: what a shot is told about its own narration."""
import json

from conftest import memoryline_beat, shotlist

from pipeline import align, memoryline


def _align(videos_root, words, start, end, seconds):
    path = align.align_path("fx", "b01")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"words": words, "speech_start": start, "speech_end": end, "seconds": seconds}))


def test_words_are_given_in_clip_time_from_the_kept_speech_window(videos):
    """The clip starts where the speech window starts (speech_start − LEAD), so a word said at
    1.0 s into the wav lands at 1.0 − (0.5 − LEAD) in the clip — the moment the viewer hears it."""
    root = videos(shotlist(memoryline_beat("b01")))
    _align(root, [{"word": "STEP", "start": 1.0, "end": 1.3}], start=0.5, end=1.3, seconds=2.0)
    got = memoryline.words_for("fx", "b01")
    assert got == [{"word": "STEP", "start": round(1.0 - (0.5 - align.LEAD), 3), "end": round(1.3 - (0.5 - align.LEAD), 3)}]


def test_a_beat_with_no_alignment_gets_no_words_and_its_authored_seconds(videos):
    videos(shotlist(memoryline_beat("b01", vo="")))
    assert memoryline.words_for("fx", "b01") == []
    assert memoryline.seconds_for("fx", "b01", {"seconds": 4.5}) == 4.5


def test_a_beat_depends_on_the_kit_files_it_reaches_not_the_whole_kit(tmp_path, monkeypatch):
    from pipeline import config, memoryline
    kit = tmp_path / "kit"
    for d in ("shots", "typeset", "fusion"):
        (kit / d).mkdir(parents=True)
    (kit / "common.py").write_text("X = 1\n")
    (kit / "beat.py").write_text("import common\n")
    (kit / "layered.py").write_text("import common\n")
    (kit / "fusion" / "beat.py").write_text("import fusion_core\n")      # same name as the root beat.py
    (kit / "fusion" / "fusion_core.py").write_text("")
    (kit / "shots" / "a.py").write_text("import common\nimport a_layout\n")
    (kit / "shots" / "a_layout.py").write_text("")
    (kit / "shots" / "b.py").write_text("import common\n")
    (kit / "typeset" / "a.py").write_text("import json\nL = 'shots/a_layout.py'\n")   # loads a module by path
    monkeypatch.setattr(config, "MEMORYLINE_KIT", kit)
    rel = lambda v: sorted(str(p.relative_to(kit)) for p in memoryline.kit_sources_for(v))
    assert rel({"shot": "shots/b.py:B"}) == ["beat.py", "common.py", "shots/b.py"]
    assert rel({"shot": "layered", "params": {"drawn": "shots/a.py:A", "type": "typeset/a.py:A"}}) == \
        ["common.py", "layered.py", "shots/a.py", "shots/a_layout.py", "typeset/a.py"]
    assert "fusion/fusion_core.py" in rel({"shot": "fusion:Triptych"})


def test_a_continuation_beat_runs_on_the_chain_clock_and_sees_the_earlier_words(monkeypatch):
    """`clock_after` (2026-10-09): the beat renders a later window of one timeline instead of restarting its shot —
    its clock starts at the on-screen length of the beats it carries on from, and every word sits on that clock."""
    from pipeline import fit
    said = {"b01": [{"word": "SEARCH", "start": 1.0, "end": 1.4}], "b02": [{"word": "BEAT", "start": 0.5, "end": 0.8}]}
    monkeypatch.setattr(memoryline, "words_for", lambda slug, bid: said[bid])
    monkeypatch.setattr(fit, "target_seconds", lambda slug, bid: {"b01": 6.0}[bid])
    clock, words = memoryline.chain("fx", "b02", {"params": {"clock_after": ["b01"]}})
    assert clock == 6.0
    assert words == [{"word": "SEARCH", "start": 1.0, "end": 1.4}, {"word": "BEAT", "start": 6.5, "end": 6.8}]
    assert memoryline.chain("fx", "b01", {"params": {}}) == (0.0, said["b01"])     # an ordinary beat is unchanged
    assert [p.name for p in memoryline.chain_deps("fx", {"params": {"clock_after": ["b01"]}})] == ["b01.align.json"]
