"""Runner for the image-to-video bridge. Mirrors voice.bridge(), separate env.

Kept as its own module rather than a second function in voice.py so that the
two heavy environments cannot be confused at a call site. They have different
lifetimes and different owners: VOICE_ROOT belongs to the Reachy robot and is
shared, VISUAL_ROOT exists only for this pipeline.
"""
import json
import subprocess

from . import config

# i2v is minutes per clip, not seconds, and a long beat is generated as several
# chained chunks — so the ceiling has to be generous or the pipeline kills work
# that was going to succeed. Two hours covers the longest beat with headroom.
TIMEOUT = 7200


def bridge(command: str, request: dict, timeout: int = TIMEOUT) -> dict:
    """Run one command in the visual env. Never imports torch in this process."""
    from . import bridge as bridge_mod
    if not config.visual_ready():
        return {"ok": False, "error": f"visual env missing at {config.VISUAL_ROOT} "
                                      f"(expected {config.visual_python()})"}
    return bridge_mod.call(config.visual_python(), config.VISUAL_BRIDGE,
                           command, request, timeout)
