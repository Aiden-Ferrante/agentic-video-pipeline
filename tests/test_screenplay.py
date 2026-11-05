"""The script compiles: both dialects parse to one shape, adoption marks by
text, and a recompile of an unchanged script writes nothing."""
import json

import pytest
from conftest import animation_beat, shotlist

from pipeline import beats, screenplay

SCREENPLAY = """# 1.2 — What a Formal Logic Is
## REVAMP · target ~12 min

---

## ACT I — THE QUESTION

**NARRATOR:**

You have been told that mathematics is written in logic. Fine. Then here is a
fair question: what is a logic actually *made of*?

**VIDEO:** Black. The question alone.

**NARRATOR:**

Not what it's for. Not what it feels like.

**VIDEO:** An indistinct glowing mass, unresolved, wrapping onto a
second line.

**VIDEO:** A picture with no words before it.

---

## ACT II — THE LANGUAGE

**NARRATOR:**

A language. A semantics. A proof system.

**VIDEO:** The three modules take their labels.
"""

BEAT_SHEET = """# Linearity
Script draft 1

---

## Part 0 · Cold open

**c00a** — *animation: `HaveYouWondered`. Black, then a form.*

Have you ever wondered how computer graphics actually work?

**c00b** — *the form turns*

How is it that we can represent a three dimensional object
on a flat screen?

## Part 1

**r01** — *a grid*

Throw away the meaning. Keep the structure.
"""


def test_screenplay_dialect_parses_to_blocks():
    p = screenplay.parse(SCREENPLAY)
    assert p["dialect"] == "screenplay"
    b = p["blocks"]
    assert [x["narration"][:12] for x in b] == ["You have bee", "Not what it'", "", "A language. "]
    assert b[0]["narration"].endswith("*made of*?")           # markup kept, wrap collapsed
    assert b[0]["brief"] == "Black. The question alone."
    assert b[1]["brief"] == "An indistinct glowing mass, unresolved, wrapping onto a second line."
    assert b[2]["brief"] == "A picture with no words before it."   # silent beat
    assert b[0]["section"] == "ACT I — THE QUESTION"
    assert b[3]["section"] == "ACT II — THE LANGUAGE"
    assert all(x["id"] is None for x in b)


def test_beat_sheet_dialect_carries_ids():
    p = screenplay.parse(BEAT_SHEET)
    assert p["dialect"] == "beats"
    ids = [x["id"] for x in p["blocks"]]
    assert ids == ["c00a", "c00b", "r01"]
    assert p["blocks"][1]["narration"] == ("How is it that we can represent a three "
                                           "dimensional object on a flat screen?")
    assert p["blocks"][0]["brief"].startswith("animation: `HaveYouWondered`")


def test_not_a_screenplay_is_refused():
    with pytest.raises(ValueError):
        screenplay.parse("# Notes\n\njust prose\n")


@pytest.fixture
def scripted(videos, tmp_path):
    """A slug whose shot list was hand-built from SCREENPLAY, plus a TOC card
    the script never mentions, seated after the first beat."""
    script = tmp_path / "1.2 — REVAMP.md"
    script.write_text(SCREENPLAY)
    doc = shotlist(
        animation_beat("r01", scene="A", vo="You have been told that mathematics is written "
                                          "in logic. Fine. Then here is a fair question: "
                                          "what is a logic actually *made of*?"),
        {"id": "t00", "vo_text": "", "visual": {"kind": "animation", "scene_file": "a.py",
                                                "scene": "Toc", "quality": "l"}},
        animation_beat("r02", scene="B", vo="Not what it's for. Not what it feels like."),
        animation_beat("r03", scene="C", vo="A language. A semantics. A proof system."),
    )
    doc["script"] = str(script)
    videos(doc)
    return script


def test_adopt_marks_by_text_and_leaves_silent_blocks(scripted):
    rep = screenplay.adopt("fx")
    assert rep["matched"] == ["r01", "r02", "r03"]
    assert rep["fresh"] == []
    assert rep["silent_fresh"] == ["r04"]
    assert rep["written"] is True
    text = scripted.read_text()
    assert "**NARRATOR [r01]:**" in text and "**NARRATOR [r03]:**" in text
    assert "**VIDEO [r04]:** A picture with no words before it." in text
    assert text.count("**NARRATOR:**") == 0
    # Idempotent: a second adopt finds nothing to do.
    assert screenplay.adopt("fx")["written"] is False


def test_compile_refuses_unmarked_blocks(scripted):
    with pytest.raises(ValueError, match="adopt"):
        screenplay.compile("fx")


def test_recompile_of_unchanged_script_writes_nothing(scripted):
    screenplay.adopt("fx")
    # The silent VIDEO block is a new beat on the first compile.
    first = screenplay.compile("fx")
    assert first["added"] == ["r04"] and first["written"] is True
    before = json.dumps(beats.load("fx"), sort_keys=True)
    again = screenplay.compile("fx")
    assert again["written"] is False
    assert again["added"] == [] and again["changed"] == [] and again["reordered"] is False
    assert json.dumps(beats.load("fx"), sort_keys=True) == before


def test_compile_owns_words_and_order_but_not_recipes(scripted):
    screenplay.adopt("fx")
    screenplay.compile("fx")
    doc = beats.load("fx")
    by = {b["id"]: b for b in doc["beats"]}
    # Recipes untouched, brief attached, furniture seated after r01.
    assert by["r01"]["visual"]["scene"] == "A"
    assert by["r01"]["brief"] == "Black. The question alone."
    assert [b["id"] for b in doc["beats"]] == ["r01", "t00", "r02", "r04", "r03"]
    assert by["r04"]["visual"] == {"kind": "brief"} and by["r04"]["vo_text"] == ""

    # Reword one line: exactly that beat changes.
    text = scripted.read_text().replace("Not what it's for.", "Not what it is FOR.")
    scripted.write_text(text)
    rep = screenplay.compile("fx")
    assert rep["changed"] == ["r02"] and rep["added"] == []
    assert beats.load("fx")["beats"][2]["vo_text"].startswith("Not what it is FOR.")


def test_dropped_narrated_beat_is_an_orphan_not_a_deletion(scripted):
    screenplay.adopt("fx")
    screenplay.compile("fx")
    text = scripted.read_text()
    start = text.index("**NARRATOR [r03]:**")
    scripted.write_text(text[:start])
    rep = screenplay.compile("fx")
    assert rep["orphaned"] == ["r03"]
    assert "r03" in [b["id"] for b in beats.load("fx")["beats"]]


def test_brief_beats_project_and_count_as_clips(videos):
    videos(shotlist({"id": "r01", "vo_text": "Twelve syllables of narration here.",
                     "brief": "A gate crushes a formula.", "section": "ACT II",
                     "visual": {"kind": "brief"}}))
    beats.split("fx")
    frag = json.loads(beats.fragment_path("fx", "r01", "brief").read_text())
    assert frag["brief"] == "A gate crushes a formula."
    assert frag["section"] == "ACT II"
    assert beats.clip_ids("fx") == ["r01"]


def test_brief_card_svg_wraps_and_escapes():
    from pipeline import brief
    out = brief.svg({"id": "r09", "brief": "Symbols & a gate " * 20, "section": "ACT <II>",
                     "resolution": [1920, 1080], "letterbox": "0x1c1714"})
    assert out.count("<text") >= 5
    assert "&amp;" in out and "&lt;II&gt;" in out
    assert 'fill="#1c1714"' in out
