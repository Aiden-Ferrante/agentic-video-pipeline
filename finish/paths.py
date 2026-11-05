"""Where the finish stage reads and writes — one place, so no script hard-codes a folder.

The finish stage turns the pipeline's fit clips and voice into the film the human watches: acts with captions,
a generated score, word-cued sound effects, the voice over the score, and the acts joined with dips to black.
Everything it writes is derived and lives under videos/<slug>/out/finish/ (ignored, like every out/).
Run a script with FINISH_SLUG=<slug> to point it at another video; four-slots is the default.
"""
import os, pathlib

VP = pathlib.Path(__file__).resolve().parents[1]
SLUG = os.environ.get("FINISH_SLUG", "four-slots")
WORK = VP / "videos" / SLUG / "out" / "finish"
ACTS_JSON = WORK / "acts.json"          # acts by chapter, per-beat start/length inside each act (acts_plan.py)
ACTS = WORK / "acts"                    # <act>.mp4: scored, voiced act — the unit the film joins
NARRATED = WORK / "narrated"            # <act>.mp4: picture + captions, the input sfx_mix scores
PREV = WORK / "prev"                    # <act>.scored.mp4: score + sfx before voice, so act_voice can re-run
SCORE = WORK / "score"                  # <act>.flac: the act's generated score
FILM = WORK / f"{SLUG}-full.mp4"

KIT = VP / "libraries" / "sound" / "kit"                       # the SFX kit (sfx_lib.py), shared by every video
SONNISS = VP / "libraries" / "sound" / "sonniss-gdc2026"       # the kit's source recordings
BASE_TAKE = VP / "libraries" / "sound" / "analog-awe-s12.flac" # the human's pick (2026-09-22): Act I's theme

for _d in (ACTS, NARRATED, PREV, SCORE):
    _d.mkdir(parents=True, exist_ok=True)
