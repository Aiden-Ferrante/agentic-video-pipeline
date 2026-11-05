"""Captions, thumbnail layout, chapters and the sound rights gate.

All pure logic — no rendering, no network — so these run in the thin venv.
"""
import pytest
from conftest import animation_beat, shotlist

from pipeline import captions, publish, sound, thumbnail


class TestCaptionGrouping:
    def word(self, text, start, end, beat="b01"):
        return {"beat": beat, "text": text, "start": start, "end": end}

    def test_words_join_into_one_readable_line(self):
        cues = [self.word("one", 0.0, 0.3), self.word("two", 0.32, 0.6),
                self.word("three", 0.62, 0.9)]
        lines = captions._group(cues)
        assert len(lines) == 1
        assert lines[0]["text"] == "one two three"

    def test_a_breath_starts_a_new_line(self):
        cues = [self.word("before", 0.0, 0.3),
                self.word("after", 0.3 + captions.BREATH + 0.1, 1.2)]
        assert len(captions._group(cues)) == 2

    def test_a_beat_change_starts_a_new_line(self):
        """Two beats must never share a caption; the cut would land mid-line."""
        cues = [self.word("first", 0.0, 0.3, beat="b01"),
                self.word("second", 0.31, 0.6, beat="b02")]
        assert len(captions._group(cues)) == 2

    def test_a_line_never_outlives_the_next_one(self):
        cues = [self.word("a", 0.0, 5.0), self.word("b", 1.0, 2.0, beat="b02")]
        lines = captions._group(cues)
        assert lines[0]["end"] <= lines[1]["start"]

    def test_lines_that_would_be_inverted_are_dropped(self):
        """Clamping can push an end before its own start; such a cue must go
        rather than be emitted with a negative duration."""
        cues = [self.word("a", 0.0, 5.0), self.word("b", 0.0, 2.0, beat="b02")]
        assert all(l["end"] > l["start"] for l in captions._group(cues))


class TestCaptionWrap:
    def test_short_text_stays_on_one_line(self):
        assert "\\N" not in captions._wrap("short enough")

    def test_long_text_is_balanced_not_greedy(self):
        wrapped = captions._wrap("A proof and a program turn out to be the same object")
        top, bottom = wrapped.split("\\N")
        # Greedy filling would leave a full top line and an orphan below.
        assert abs(len(top) - len(bottom)) < 12

    def test_wrapping_never_loses_or_reorders_words(self):
        text = "the quick brown fox jumps over the lazy dog and keeps on running"
        assert captions._wrap(text).replace("\\N", " ") == text


class TestThumbnailLayout:
    def test_a_title_is_set_as_large_as_fits(self):
        lines, size = thumbnail.layout("Zeno")
        assert lines == ["Zeno"] and size == thumbnail.SIZE_LADDER[0]

    def test_no_line_can_overflow_the_frame(self):
        """The bug this replaces: 22 characters is one short line by count and
        1200 pixels wide at 92pt, so the title ran off the canvas."""
        available = thumbnail.WIDTH - 2 * thumbnail.MARGIN
        for title in ("Where Bayes comes from",
                      "A proof and a program are the same object",
                      "Supercalifragilisticexpialidocious"):
            lines, size = thumbnail.layout(title)
            for line in lines:
                assert len(line) * size * thumbnail.ADVANCE <= available, (title, line)

    def test_a_long_title_never_exceeds_the_line_budget(self):
        lines, _ = thumbnail.layout(" ".join(["word"] * 12))
        assert len(lines) <= thumbnail.MAX_LINES


class TestChapters:
    def test_no_chapter_fields_means_no_chapters(self, videos):
        videos(shotlist(animation_beat("b01")))
        assert publish.chapters("fx") == []

    def test_a_stamp_is_mm_ss_until_an_hour(self):
        assert publish._stamp(0) == "00:00"
        assert publish._stamp(75) == "01:15"
        assert publish._stamp(3675) == "1:01:15"


class TestSoundRightsGate:
    def test_a_bed_with_no_sidecar_is_refused(self, videos, tmp_path, monkeypatch):
        """The whole discipline of the sound node. A bed whose licence was never
        written down is the expensive kind of unknown."""
        lib = tmp_path / "sound"
        lib.mkdir()
        (lib / "unlicensed.wav").write_bytes(b"RIFF")
        monkeypatch.setattr(sound, "library_dir", lambda: lib)

        doc = shotlist(animation_beat("b01"))
        doc["sound"] = {"bed": "unlicensed"}
        videos(doc)
        assert sound.build("fx") is False

    def test_no_bed_requested_is_not_an_error_state(self, videos):
        videos(shotlist(animation_beat("b01")))
        assert sound.spec("fx") is None
