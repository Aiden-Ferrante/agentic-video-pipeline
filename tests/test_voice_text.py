"""Text handling in the voice bridge.

The bridge's module level is stdlib-only on purpose, so the text logic is
testable from the thin pipeline venv without torch. Only synth() and align()
reach for the heavy imports, and neither is called here.
"""
import importlib.util
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent / "bridges" / "voice_render.py"
_spec = importlib.util.spec_from_file_location("voice_render", BRIDGE)
vr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vr)


class TestNormalize:
    def test_uppercases_and_drops_punctuation(self):
        assert vr.normalize("Where does Bayes' formula come from?") == [
            "WHERE", "DOES", "BAYES'", "FORMULA", "COME", "FROM"]

    def test_apostrophes_survive_because_the_model_spells_them(self):
        assert vr.normalize("don't") == ["DON'T"]

    def test_a_word_that_normalises_to_nothing_is_dropped_entirely(self):
        """Keeping an empty token would shift every later word's alignment."""
        assert vr.normalize("two 2 words") == ["TWO", "WORDS"]

    def test_hyphenation_joins_rather_than_splits(self):
        assert vr.normalize("well-known") == ["WELLKNOWN"]


class TestBookmarks:
    def test_a_mark_records_the_word_index_it_precedes(self):
        text, marks = vr.strip_bookmarks("one two <bookmark mark='hit'/> three four")
        assert text == "one two three four"
        assert marks == {"hit": 2}

    def test_marks_are_removed_from_the_spoken_text(self):
        text, _ = vr.strip_bookmarks("say <bookmark mark='a'/>this")
        assert "bookmark" not in text and text == "say this"

    def test_several_marks_each_keep_their_own_index(self):
        _, marks = vr.strip_bookmarks(
            "a <bookmark mark='x'/> b c <bookmark mark='y'/> d")
        assert marks == {"x": 1, "y": 3}

    def test_a_mark_at_the_very_end_indexes_past_the_last_word(self):
        text, marks = vr.strip_bookmarks("all done <bookmark mark='end'/>")
        assert text == "all done"
        assert marks == {"end": 2}

    def test_double_quotes_and_loose_spacing_are_accepted(self):
        _, marks = vr.strip_bookmarks('a < bookmark   mark = "y" > b')
        assert marks == {"y": 1}

    def test_text_without_marks_is_returned_unchanged(self):
        text, marks = vr.strip_bookmarks("nothing special here")
        assert text == "nothing special here" and marks == {}
