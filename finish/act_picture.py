"""An act's reviewable picture: fit clips + narration windows (actcut) with captions burned in.
Out: paths.NARRATED/<act>.mp4 — the picture sfx_mix.py scores. python act_picture.py 1 [2 …]"""
import json, pathlib, subprocess, sys
HERE = pathlib.Path(__file__).resolve().parent; PY = str(HERE.parent / ".venv/bin/python")
from paths import ACTS, ACTS_JSON, NARRATED, WORK
SET = json.loads((WORK / "settings.json").read_text())
STYLE = SET["captions"]                                 # the template's caption style
BURN = SET.get("burn_captions", True)                  # false: the .srt is still written (YouTube's CC), not burned in

acts = {a["n"]: a for a in json.loads(ACTS_JSON.read_text())}
for n in map(int, sys.argv[1:]):
    a = acts[n]; base = ACTS / a["slug"]; out = NARRATED / f"{a['slug']}.mp4"
    subprocess.run([PY, str(HERE / "actcut.py"), f"{base}.picture.mp4", *a["ids"]], check=True, capture_output=True)
    subprocess.run([PY, str(HERE / "captions.py"), f"{base}.srt", *a["ids"]], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", f"{base}.picture.mp4", *(["-vf", f"subtitles={base}.srt:force_style='{STYLE}'"] if BURN else []),
                    "-c:v", "h264_nvenc", "-b:v", "12M", "-c:a", "aac", "-b:a", "192k", str(out)], check=True)
    pathlib.Path(f"{base}.picture.mp4").unlink(); print(n, out.name)
