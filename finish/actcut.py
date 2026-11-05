"""Quick act preview from fit clips + narration windows (no grade/captions). python actcut.py OUT.mp4 m01 m02 ..."""
import subprocess, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import SLUG
from pipeline import align, config, fit, voice
out, ids = sys.argv[1], sys.argv[2:]
d = pathlib.Path(out).parent
vl, al = d / f"{pathlib.Path(out).stem}.v.txt", []   # per act: a shared v.txt let parallel acts swap pictures (2026-10-14)
vl.write_text("".join(f"file '{fit.fit_path(SLUG, b)}'\n" for b in ids))
filt, ins = [], []
for i, b in enumerate(ids):
    w = align.speech_window(SLUG, b); dur = float(subprocess.run([config.ffprobe(), "-v", "0", "-show_entries", "format=duration", "-of", "csv=p=0", str(fit.fit_path(SLUG, b))], capture_output=True, text=True).stdout)
    wav = voice.spoken_path(SLUG, b)
    ins += ["-i", str(wav)] if wav.exists() else ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono"]   # a silent beat
    s0 = w[0] if w else 0.0
    filt.append(f"[{i + 1}:a]atrim=start={s0:.3f},asetpts=PTS-STARTPTS,apad,atrim=0:{dur:.3f}[a{i}]")
filt.append("".join(f"[a{i}]" for i in range(len(ids))) + f"concat=n={len(ids)}:v=0:a=1[a]")
subprocess.run([config.ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(vl), *ins, "-filter_complex", ";".join(filt),
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", out], check=True)
print(out)
