"""Resolve a finishing video into the files every finish step reads — the only script that reads the shot list.

    acts.json            acts by chapter, each beat's [start, length] inside its act (measured from fit clips)
    settings.json        the template: the style's `finish` binding, under the shot list's own (dips, captions,
                         loudness, voice-over-score, score prompt tail)
    timing/<act>.json    what the act's SCORE depends on: its length, its beats, its score spec (tail applied)
    cues/<act>.json      what the act's MIX depends on: the word-cued sound effects of its beats, and its beds

Each file is per act where it can be, so a change reaches only the acts it touches (doit compares content).
Refuses — before anything renders — when a cue's word is not in its beat's narration.
"""
import json, subprocess, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import ACTS_JSON, SLUG, WORK
from pipeline import config, finish, fit

bad = finish.cue_problems(SLUG)
if bad:
    raise SystemExit("cue words missing from the narration — fix the cue or the line:\n  " + "\n  ".join(bad))
acts, st, spec = finish.acts(SLUG), finish.settings(SLUG), finish.per_video(SLUG)
for a in acts:
    t, a["beats"] = 0.0, {}
    for x in a["ids"]:
        d = float(subprocess.run([config.ffprobe(), "-v", "0", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  str(fit.fit_path(SLUG, x))], capture_output=True, text=True).stdout or 0)
        a["beats"][x] = [round(t, 3), round(d, 3)]; t += d
    a["seconds"] = round(t, 3)
ACTS_JSON.write_text(json.dumps(acts, indent=1))
(WORK / "settings.json").write_text(json.dumps(st, indent=1, sort_keys=True))
for sub in ("timing", "cues"):
    (WORK / sub).mkdir(exist_ok=True)
for a in acts:
    (WORK / "timing" / f"{a['slug']}.json").write_text(json.dumps(
        {"seconds": a["seconds"], "beats": a["beats"], "score": finish.score_spec(SLUG, a, st, spec)}, indent=1))
    (WORK / "cues" / f"{a['slug']}.json").write_text(json.dumps(finish.act_cues(SLUG, a, spec), indent=1))
for a in acts: print(a["n"], a["slug"], len(a["ids"]), a["seconds"])
