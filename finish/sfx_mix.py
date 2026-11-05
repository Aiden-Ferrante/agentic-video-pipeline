"""Sound effects for the act drafts: each act's score plus word-cued effects from the one M1 kit
(paths.KIT, made by sfx_lib.py). Picture = the act's narrated cut (captions
burned in); narration dropped. Cues are (beat, word, nth, effect, gain dB vs the score's RMS, offset s) — a
word of None means the beat's start; they come from cues/<act>.json. Run with the stable-audio venv (numpy + soundfile).
    python sfx_mix.py 1 [2 …]"""
import json, pathlib, re, subprocess, sys
import numpy as np, soundfile as sf
from paths import ACTS as ACTDIR, ACTS_JSON, KIT, NARRATED, SCORE, SLUG, VP, WORK
VOICE = VP / "videos" / SLUG / "voice"; SR = 44100
ACTS = {a["n"]: a for a in json.loads(ACTS_JSON.read_text())}

# Cues and beds are the video's (shot list: a beat's `sfx`, `finish.beds`), resolved per act by acts_plan.py into
# cues/<act>.json; loudness and the film's opening come from the template (settings.json).
SET = json.loads((WORK / "settings.json").read_text())
LOUD = SET.get("loudness") or {"I": -16, "TP": -1.5, "LRA": 11}
norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())


def word_at(beat, word, nth):
    if word is None:
        return 0.0
    al = json.loads((VOICE / f"{beat}.align.json").read_text()); ws = al["words"]; s0 = al.get("speech_start") or 0.0
    hits = [x["start"] for x in ws if norm(x["word"]) == norm(word)]
    if not hits:
        raise SystemExit(f"{beat}: no word {word!r} — words are {' '.join(norm(x['word']) for x in ws)}")
    return hits[nth] - s0


def chip(f, secs=0.07, duty=0.5, bits=4):
    """An 8-bit blip: a square wave at f, quantised to `bits`, fast decay — pure DSP, so it is clean."""
    t = np.arange(int(secs * SR)) / SR; y = np.where((t * f) % 1 < duty, 1.0, -1.0) * np.exp(-t / (secs / 3))
    q = 2 ** (bits - 1); y = np.round(y * q) / q; y[: 40] *= np.linspace(0, 1, 40)
    return np.c_[y, y].astype("float32")


def curve_blips(beat):
    """m04: one blip per point on the capability curve, at the times the typeset layer lands them (the same
    formula, LY.curve_times) — faster and higher as the curve steepens; checkpoints v1…v10 get a two-note blip."""
    import importlib.util
    kit = VP.parent / ".claude/skills/memory-line/kit/shots/four_slots_act1_layout.py"
    sp = importlib.util.spec_from_file_location("LY", kit); LY = importlib.util.module_from_spec(sp); sp.loader.exec_module(LY)
    al = json.loads((VOICE / f"{beat}.align.json").read_text()); s0 = al.get("speech_start") or 0.0
    ws = [x for x in al["words"]]
    def w(tok, nth=0, frac=0.5):
        h = [x["start"] - s0 for x in ws if norm(x["word"]) == norm(tok)]
        return h[nth] if h else frac * T
    fitp = VP / f"videos/{SLUG}/assets/{beat}.fit.mp4"
    T = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(fitp)])) + 1.0
    times = LY.curve_times(w, T, 40); ck = {4, 11, 17, 22, 26, 30, 33, 35, 37, 39}
    out = []
    for k, t in enumerate(times):
        f = 330 * 2 ** (k / 16)                                          # two and a half octaves over the climb
        s = chip(f) if k not in ck else np.concatenate([chip(f, 0.06), chip(f * 1.5, 0.09)])
        out.append((t, s))
    return out


def beat_starts(ids):
    t, out = 0.0, {}
    for i in ids:                                    # same arithmetic actcut uses: clips back to back
        out[i] = t
        p = VP / f"videos/{SLUG}/assets/{i}.fit.mp4"
        p = p if p.exists() else VP / f"videos/{SLUG}/assets/{i}.mp4"
        t += float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]))
    return out


for n in map(int, sys.argv[1:]):
    a = ACTS[n]; slug = a["slug"]
    pic = NARRATED / f"{slug}.mp4"
    music = SCORE / f"{slug}.flac"
    x, sr = sf.read(music, dtype="float32"); assert sr == SR
    d_pic = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(pic)]))
    if abs(len(x) / SR - d_pic) > 0.5:                    # a score shorter than its act desyncs every act after it
        print(f"  [warn] {slug}: score {len(x) / SR:.1f}s vs picture {d_pic:.1f}s — padded/trimmed; re-score this act")
    x = np.pad(x, ((0, max(0, int(d_pic * SR) - len(x))), (0, 0)))[: int(d_pic * SR)].copy()
    rms = float(np.sqrt((x ** 2).mean())); peak = float(np.abs(x).max())
    start = beat_starts(a["ids"])
    CQ = json.loads((WORK / "cues" / f"{slug}.json").read_text())
    if n == 1:                                           # the film's first sound: let the score arrive, not start
        f = int(float((SET.get("score") or {}).get("film_open_seconds", 4.0)) * SR); x[:f] *= np.linspace(0, 1, f)[:, None] ** 2
    for beat, word, nth, fx, g, off in CQ["cues"]:
        if fx.startswith("@"):
            for tt, s in curve_blips(beat):
                s = s * (rms * 10 ** (g / 20) / max(1e-6, float(np.sqrt((s ** 2).mean()))))
                s = s * min(1.0, 0.4 * peak / max(1e-6, float(np.abs(s).max())))
                i = int((start[beat] + tt + off) * SR); j = min(len(x), i + len(s)); x[i:j] += s[: j - i]
            print(f"  {start[beat]:7.2f}s  {beat:5} 40 x 8-bit blips"); continue
        t = start[beat] + word_at(beat, word, nth) + off
        s, _ = sf.read(KIT / f"{fx}.flac", dtype="float32")
        s = s * (rms * 10 ** (g / 20) / max(1e-6, float(np.sqrt((s ** 2).mean()))))
        s = s * min(1.0, 0.4 * peak / max(1e-6, float(np.abs(s).max())))
        i = max(0, int(t * SR)); j = min(len(x), i + len(s))
        if j > i: x[i:j] += s[: j - i]
        print(f"  {t:7.2f}s  {beat:5} {str(word):12} {fx}")
    for b0, b1, fx, g in CQ["beds"]:
        t0 = start[b0]; ids = a["ids"]; t1 = start[ids[ids.index(b1) + 1]] if ids.index(b1) + 1 < len(ids) else len(x) / SR
        s, _ = sf.read(KIT / f"{fx}.flac", dtype="float32"); L = int((t1 - t0) * SR); xf = int(3 * SR)
        bed = s.copy()
        while len(bed) < L:                                   # loop with 3 s crossfades
            r = np.linspace(0, 1, xf)[:, None]; bed = np.concatenate([bed[:-xf], bed[-xf:] * (1 - r) + s[:xf] * r, s[xf:]])
        bed = bed[:L] * (rms * 10 ** (g / 20) / max(1e-6, float(np.sqrt((s ** 2).mean()))))
        f = int(4 * SR); bed[:f] *= np.linspace(0, 1, f)[:, None]; bed[-f:] *= np.linspace(1, 0, f)[:, None]
        i = int(t0 * SR); x[i:i + len(bed)] += bed[: len(x) - i]; print(f"  bed {fx} {t0:.1f}–{t1:.1f}s")
    wav = ACTDIR / f"{slug}.mix.wav"; sf.write(wav, x, SR)
    d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(pic)]))
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(pic), "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-af", f"aresample=48000,loudnorm=I={LOUD['I']}:TP={LOUD['TP']}:LRA={LOUD['LRA']},afade=t=out:st={d - 2:.2f}:d=2", "-t", f"{d:.3f}",
                    "-c:a", "aac", "-b:a", "256k", str(ACTDIR / f"{slug}.mp4")], check=True)
    wav.unlink(); print(n, slug)
