"""One act's score, from the act's timing file (acts_plan.py): its length, its beats, and the score spec the shot list
gives that act (`finish.acts.<chapter>.score`), with the style's prompt tail already applied.

Two kinds of spec:

  arc     the chosen take re-generated in place where the story turns, then a gain envelope that follows the act.
          Times are beat-relative ({"beat": id, "plus": s}, {"after": id, "plus": s} = the beat's end, {"t": s},
          {"end": s}, {"total": s}). Rebuilt whenever the act's timing changes; deterministic for the same timing.
  mood    one generated cue in the act's mood (+ seed), faded in and out; `open_with_take` continues the chosen
          take's opening instead (the last act returning to the first).
          NEVER REGENERATE WHAT THE HUMAN HEARD (2026-09-24): a score already on disk is kept (same length), cut
          and re-faded (the act shrank), or continued in its own mood from 10 s before its old end (the act grew).

Run with the stable-audio env:  $STABLE_AUDIO_PY finish/score.py <act-slug> [...]   → paths.SCORE/<act>.flac
"""
import json, sys, pathlib, soundfile as sf, torch, numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from paths import BASE_TAKE, SCORE, WORK

SR = 44100
_model = None


def model():
    global _model
    if _model is None:
        from stable_audio_3 import StableAudioModel
        _model = StableAudioModel.from_pretrained("medium")
    return _model


def fade(x):
    t = np.arange(len(x)) / SR; L = len(x) / SR
    return x * (np.clip(t / 3.0, 0, 1) * np.clip((L - t) / 4.0, 0, 1))[:, None]


def size(T):
    return -(-int(T * SR) // 2048) * 2048


def arc(spec, beats, end, take_prompt):
    total = end + 1.5

    def at(p):
        if "beat" in p: return beats[p["beat"]][0] + p["plus"]
        if "after" in p: return beats[p["after"]][0] + beats[p["after"]][1] + p["plus"]
        if "end" in p: return end + p["end"]
        if "total" in p: return total + p["total"]
        return p["t"]

    wav, sr = sf.read(BASE_TAKE, dtype="float32")
    audio = torch.from_numpy(wav.T)
    for step in spec["steps"]:
        prompt = take_prompt if step["prompt"] == "@take" else step["prompt"]
        a, b = at(step["from"]), at(step["to"])
        out = model().generate(prompt=prompt, duration=total, seed=step["seed"], inpaint_audio=(sr, audio),
                               sample_size=size(total), inpaint_mask_start_seconds=a, inpaint_mask_end_seconds=b)
        out = out.squeeze(0) if out.dim() == 3 else out
        audio = out.float().cpu(); sr = SR
        print("  inpainted", round(a, 1), "→", round(b, 1), flush=True)
    x = audio.numpy().T[: int(end * SR)]
    t = np.arange(len(x)) / SR
    kt, kd = zip(*[(at(p), db) for p, db in spec["gain"]])
    return x * (10 ** (np.interp(t, kt, kd) / 20))[:, None]


def mood(spec, end, prev):
    if prev.exists():
        wav, sr = sf.read(prev, dtype="float32"); old = len(wav) / sr
        if abs(old - end) <= 0.05:
            return None, f"kept {old:.1f} s"
        if old > end:
            return fade(wav[: int(end * SR)]), f"cut {old:.1f} → {end:.1f} s"
        keep = max(0.0, old - 10.0); T = end + 1.5
        out = model().generate(prompt=spec["prompt"], duration=T, seed=spec["seed"], sample_size=size(T),
                               inpaint_audio=(sr, torch.from_numpy(wav.T)), inpaint_mask_start_seconds=keep,
                               inpaint_mask_end_seconds=T)
        x = (out.squeeze(0) if out.dim() == 3 else out).float().cpu().numpy().T[: int(end * SR)]
        return fade(x), f"continued {old:.1f} → {end:.1f} s (kept {keep:.1f} s)"
    T = end + 1.5
    kw = dict(prompt=spec["prompt"], duration=T, seed=spec["seed"], sample_size=size(T))
    if spec.get("open_with_take"):
        wav, sr = sf.read(BASE_TAKE, dtype="float32")
        kw.update(inpaint_audio=(sr, torch.from_numpy(wav.T)), inpaint_mask_start_seconds=min(30.0, T / 3),
                  inpaint_mask_end_seconds=T)
    out = model().generate(**kw)
    x = (out.squeeze(0) if out.dim() == 3 else out).float().cpu().numpy().T[: int(end * SR)]
    return fade(x), f"generated {end:.1f} s"


take_prompt = json.loads(BASE_TAKE.with_name(BASE_TAKE.name + ".json").read_text())["provenance"]["prompt"]
for act in sys.argv[1:]:
    tm = json.loads((WORK / "timing" / f"{act}.json").read_text())
    spec, out = tm.get("score") or {}, SCORE / f"{act}.flac"
    if "arc" in spec:
        x, what = arc(spec["arc"], tm["beats"], tm["seconds"], take_prompt), "arc"
    elif "prompt" in spec:
        x, what = mood(spec, tm["seconds"], out)
    else:
        raise SystemExit(f"{act}: no score in the shot list (finish.acts.<chapter>.score)")
    if x is not None:
        sf.write(out, x, SR)
    print(act, what, flush=True)
