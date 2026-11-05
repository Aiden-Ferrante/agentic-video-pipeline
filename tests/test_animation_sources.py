"""The AST import closure.

This is the fix for the 2026-08-07 silent-staleness bug: file_dep listed only
the scene .py, but every scene imports the shared visual language, so editing
colours or the base class changed every render while invalidating nothing.
"""
from conftest import animation_beat

from pipeline import animation


def sources(beat, root):
    return sorted(p.relative_to(root).as_posix() for p in animation.scene_sources(beat))


def test_closure_includes_a_directly_imported_shared_module(manim_root):
    assert sources(animation_beat("b01", scene_file="a.py"), manim_root) == [
        "scenes/a.py", "scm_style.py",
    ]


def test_closure_is_transitive(manim_root):
    """b.py imports helpers, which imports scm_style. Editing scm_style must
    invalidate b.py's beat even though b.py never names it."""
    assert sources(animation_beat("b01", scene_file="b.py"), manim_root) == [
        "helpers.py", "scenes/b.py", "scm_style.py",
    ]


def test_a_scene_with_no_local_imports_depends_only_on_itself(manim_root):
    assert sources(animation_beat("b01", scene_file="lonely.py"), manim_root) == [
        "scenes/lonely.py",
    ]


def test_third_party_imports_are_not_followed(manim_root):
    """manim itself is pinned by the environment, not tracked by us — following
    it would drag the whole site-packages tree into every file_dep."""
    (manim_root / "scenes" / "c.py").write_text(
        "import manim\nimport json\nfrom scm_style import PALETTE\n")
    assert sources(animation_beat("b01", scene_file="c.py"), manim_root) == [
        "scenes/c.py", "scm_style.py",
    ]


def test_a_beat_that_names_no_scene_file_does_not_break_enumeration(manim_root):
    """An animation beat missing `scene_file` is an authoring typo. It must
    fail its own render, not raise during task enumeration and hide every
    other task in the build."""
    assert animation.scene_source({"id": "b01", "visual": {"kind": "animation"}}) is None
    assert animation.scene_sources({"id": "b01", "visual": {"kind": "animation"}}) == []


def test_a_missing_scene_file_does_not_raise(manim_root):
    """Task enumeration runs before anything is built, and a typo'd scene_file
    must surface as a render failure, not as a crash that hides every task."""
    assert animation.scene_sources(animation_beat("b01", scene_file="nope.py")) == []


def test_a_syntactically_broken_scene_still_yields_itself(manim_root):
    """A half-written scene must remain a dependency, or fixing the syntax
    error would not trigger a re-render."""
    (manim_root / "scenes" / "broken.py").write_text("class A(:\n")
    assert sources(animation_beat("b01", scene_file="broken.py"), manim_root) == [
        "scenes/broken.py",
    ]
