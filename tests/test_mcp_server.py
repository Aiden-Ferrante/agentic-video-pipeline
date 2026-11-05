"""The MCP seam only reads other modules; it has no build of its own to fail.
So a rename elsewhere (asset_path moved from animation to config in Stems
Phase 1) breaks it at call time, in the agent's session, with 158 green tests.
Guard the references themselves, and run the one read every session starts with."""
import importlib
import inspect
import re

from conftest import animation_beat, shotlist

from pipeline import mcp_server

REF = re.compile(r"\b(?P<mod>[a-z_]+)\.(?P<attr>[a-z_]+)\(")


def _unwrap(tool):
    return getattr(tool, "fn", getattr(tool, "__wrapped__", tool))


def test_every_module_reference_in_the_server_resolves():
    src = inspect.getsource(mcp_server)
    imported = {name for name in dir(mcp_server)
                if inspect.ismodule(getattr(mcp_server, name))}
    missing = sorted({
        f"{m.group('mod')}.{m.group('attr')}" for m in REF.finditer(src)
        if m.group("mod") in imported
        and not hasattr(getattr(mcp_server, m.group("mod")), m.group("attr"))
    })
    assert missing == [], f"mcp_server calls attributes that no longer exist: {missing}"


def test_list_videos_reads_a_slug_with_nothing_rendered(videos, manim_root):
    videos(shotlist(animation_beat("b01"), animation_beat("b02")))
    rows = _unwrap(mcp_server.list_videos)()
    fx = next(r for r in rows if r["slug"] == "fx")
    assert fx["beats"] == 2
    assert fx["clips_rendered"] == 0
    assert fx["has_master"] is False
