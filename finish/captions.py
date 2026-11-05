"""Plain SRT for a preview cut from fit clips: the script's words, ~9 per line, timed by the alignment.
python captions.py OUT.srt m01 m02 ..."""
import json, subprocess, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from paths import SLUG
from pipeline import beats, config, fit, memoryline

def dur(p):
    return float(subprocess.run([config.ffprobe(), "-v", "0", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                capture_output=True, text=True).stdout)

def ts(x):
    h, r = divmod(x, 3600); m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"

out, ids = sys.argv[1], sys.argv[2:]
doc = {b["id"]: b for b in beats.load(SLUG)["beats"]}
lines, at, n = [], 0.0, 0
for bid in ids:
    d = dur(fit.fit_path(SLUG, bid))
    if bid not in doc:                      # an act's title card or held tail: no words
        at += d; continue
    text = beats.narration_text(doc[bid]).split()
    words = memoryline.words_for(SLUG, bid)
    for i in range(0, len(text), 9):
        chunk = text[i:i + 9]
        if words:
            a = words[min(len(words) - 1, int(i / len(text) * len(words)))]["start"]
            j = min(len(words) - 1, int(min(len(text), i + 9) / len(text) * len(words)) - 1)
            b = words[j]["end"] + 0.15
        else:
            a, b = i / len(text) * d, min(len(text), i + 9) / len(text) * d
        n += 1
        lines.append(f"{n}\n{ts(at + a)} --> {ts(at + min(b, d))}\n{' '.join(chunk)}\n")
    at += d
pathlib.Path(out).write_text("\n".join(lines))
print(n, "captions,", round(at, 1), "s")
