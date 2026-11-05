"""Pace node: silence put INTO a beat's narration, never re-voiced.

    voice/<beat>.wav  +  voice/<beat>.raw-align.json   ->   voice/<beat>.paced.wav  +  voice/<beat>.align.json

The synthesised read keeps its own speed; what changes is the room between its words (2026-10-08, the human:
"add pauses … many different pauses … I don't want to regenerate anything voice wise"). Every gap after a word
is TOPPED UP to a target chosen by what ends there — a question waits longer than a full stop, a full stop longer
than a dash — and never shortened, so a pause the voice already took is kept as it is. The silence goes in at
the middle of the existing gap (which is already silence), and every later word's timing moves with it, so the
paced alignment is exact without re-running the aligner.

Downstream reads the PACED pair under the old names' roles: `align.align_path` is the paced alignment (fit,
memoryline, captions, sfx cues all follow it) and `voice.spoken_path` is the audio laid in the film.

The rules are the style's `timing.pauses` over DEFAULTS; a beat can add its own with `pause` in the shot list —
[{"word", "nth", "seconds"}] (the silence after that word, nth as in `sfx`) or `hold` (extra seconds after the
beat's last word). Both are resolved into beats/_pacing.json, the one file this node depends on for rules.
"""
import array
import json
import re
import wave

from . import beats, config, voice

# Seconds of silence wanted AFTER a word, by what ends there. Commas are left to the voice.
# Sized on M1 (2026-10-08): 172 → ~159 wpm across the film, +~170 s, the human asked for ~150–160.
DEFAULTS = {
    "ellipsis": 1.2,      # "…" — a thought trailing off
    "question": 1.2,      # the viewer gets a moment to try the question
    "exclaim": 0.9,
    "full_stop": 0.85,
    "punch": 1.1,         # the stop after a short sentence ("Try it.", "That's it.")
    "punch_words": 4,     # how short "short" is
    "math": 1.2,          # the stop after a sentence that carried an equation
    "colon": 0.7,         # setup → reveal
    "semicolon": 0.6,
    "dash": 0.55,
    "comma": 0.3,         # a breath, only where the voice ran through it
    "hold": {"question": 0.8, "punch": 0.6, "math": 0.6, "default": 0.2},   # after the beat's last word
}
MATH = {"equals", "squared", "derivative", "minus", "plus", "times", "sum", "partial", "gradient", "slope",
        "over", "hat", "sigma", "theta", "divided", "cubed", "root", "log", "exponent"}
DASH = {"—", "–", "-", "--"}
BOOKMARK = re.compile(r"<\s*bookmark[^>]*>")       # the aligner strips these too (bridges/voice_render.py)


def raw_align_path(slug: str, beat_id: str):
    return voice.voice_dir(slug) / f"{beat_id}.raw-align.json"


def pacing_path(slug: str):
    return beats.tools_path(slug).with_name("_pacing.json")


def rules(slug: str) -> dict:
    over = (beats.effective(slug).get("timing") or {}).get("pauses") or {}
    out = {**DEFAULTS, **{k: v for k, v in over.items() if k != "hold" and not k.startswith("_")}}
    out["hold"] = {**DEFAULTS["hold"], **(over.get("hold") or {})}
    return out


def write_fragment(slug: str) -> bool:
    per = {}
    for b in beats.load(slug)["beats"]:
        if b.get("pause") or b.get("hold"):
            per[b["id"]] = {"pause": b.get("pause") or [], "hold": float(b.get("hold") or 0.0)}
    p = pacing_path(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"rules": rules(slug), "beats": per}, indent=2, sort_keys=True) + "\n")
    return True


def _norm(raw: str) -> str:
    return re.sub(r"[^A-Z']", "", raw.upper())


def tokens(text: str) -> list[dict]:
    """The spoken words in aligner order, each with the punctuation that follows it. A token the aligner drops
    (a bare dash, a number) hands its punctuation to the word before it — "rules — and" pauses after "rules"."""
    out = []
    for raw in BOOKMARK.sub(" ", text).split():
        w = _norm(raw)
        if w:
            out.append({"word": w, "trail": re.sub(r"^.*[A-Za-z0-9']", "", raw),
                        "digit": bool(re.search(r"\d", raw)), "raw": raw})
        elif out:
            out[-1]["trail"] += " " + raw
            out[-1]["digit"] = out[-1]["digit"] or bool(re.search(r"\d", raw))
    return out


def _kind(tok: dict, sentence: list[dict], r: dict) -> str | None:
    t = tok["trail"]
    if "…" in t or "..." in t:
        return "ellipsis"
    if "?" in t:
        return "question"
    if "!" in t:
        return "exclaim"
    if re.search(r"\.(\W|$)", t):
        if len(sentence) <= r["punch_words"]:
            return "punch"
        if any(x["digit"] or "=" in x["raw"] or x["word"].lower() in MATH for x in sentence):
            return "math"
        return "full_stop"
    if ":" in t:
        return "colon"
    if ";" in t:
        return "semicolon"
    if any(d in t.split() or t.strip().startswith(d) for d in DASH):
        return "dash"
    if "," in t:
        return "comma"
    return None


def plan(text: str, words: list[dict], r: dict, extra: dict | None = None) -> tuple[list[float], float, list]:
    """Seconds to insert after each aligned word, the hold after the last, and why (for the log)."""
    toks = tokens(text)
    if len(toks) != len(words):                       # the aligner and this reading disagree: pace nothing
        return [0.0] * len(words), 0.0, [f"token mismatch {len(toks)} vs {len(words)} — unpaced"]
    add, why, sentence, last_kind = [0.0] * len(words), [], [], None
    for i, tok in enumerate(toks):
        sentence.append(tok)
        kind = _kind(tok, sentence, r)
        if kind in ("ellipsis", "question", "exclaim", "punch", "math", "full_stop"):
            sentence_done = True
        else:
            sentence_done = False
        last_kind = kind
        if kind and i + 1 < len(words):
            gap = words[i + 1]["start"] - words[i]["end"]
            add[i] = max(0.0, float(r[kind]) - gap)
            if add[i] > 0:
                why.append(f"{tok['raw']}→{kind}+{add[i]:.2f}")
        if sentence_done:
            sentence = []
    for p in (extra or {}).get("pause") or []:
        hits = [i for i, t in enumerate(toks) if t["word"] == _norm(p["word"])]
        n = p.get("nth", 0)
        if hits and -len(hits) <= n < len(hits) and hits[n] + 1 < len(words):
            i = hits[n]
            gap = words[i + 1]["start"] - words[i]["end"]
            add[i] = max(add[i], float(p["seconds"]) - gap)
    hold = float(r["hold"].get(last_kind or "", r["hold"]["default"]) or 0.0) + float((extra or {}).get("hold") or 0.0)
    return add, hold, why


# Where a pause is spliced in. The aligner's word ends run early and the voice runs sentences close, so the
# gap's midpoint can sit in a word's tail or a breath (2026-10-09, the human: "the end of a sentence was cut off
# a bit too early"). Cut at the quietest 10 ms in the gap (reaching a little past its edges), with a short fade.
SEARCH = (0.03, 0.01)     # s before the word's aligned end, s after the next word's aligned start
FADE = 0.02


def _quietest(samples, channels: int, rate: int, a: float, z: float) -> int:
    """The frame index of the quietest 10 ms window between a and z (midpoint if the audio is not 16-bit)."""
    mid = int(round((a + z) / 2 * rate))
    if samples is None:
        return mid
    lo, hi = max(0, int((a - SEARCH[0]) * rate)), min(len(samples) // channels, int((z + SEARCH[1]) * rate))
    half, step = int(0.005 * rate), max(1, int(0.002 * rate))
    best, at = None, mid
    for c in range(lo + half, max(lo + half + 1, hi - half), step):
        e = sum(v * v for v in samples[(c - half) * channels:(c + half) * channels:channels])
        if best is None or e < best or (e == best and abs(c - mid) < abs(at - mid)):
            best, at = e, c
    return at


def _fade(chunk: bytes, width: int, rate: int, out: bool) -> bytes:
    """A FADE-long linear ramp at the end (out) or start (in) of a 16-bit chunk; anything else passes through."""
    n = min(len(chunk) // width, int(FADE * rate))
    if n < 2 or width % 2:
        return chunk
    ch = width // 2
    a = array.array("h", chunk)
    k0 = len(a) // ch - n if out else 0
    for k in range(n):
        g = (n - 1 - k) / (n - 1) if out else k / (n - 1)
        for c in range(ch):
            a[(k0 + k) * ch + c] = int(a[(k0 + k) * ch + c] * g)
    return a.tobytes()


def build(slug: str, beat_id: str) -> bool:
    raw = json.loads(raw_align_path(slug, beat_id).read_text())
    text = json.loads(beats.fragment_path(slug, beat_id, "vo").read_text()).get("vo_text", "")
    pc = json.loads(pacing_path(slug).read_text())
    words = raw.get("words") or []
    add, hold, why = plan(text, words, pc["rules"], pc["beats"].get(beat_id))

    src = voice.wav_path(slug, beat_id)
    with wave.open(str(src), "rb") as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    rate, width = params.framerate, params.sampwidth * params.nchannels
    samples = array.array("h", frames) if params.sampwidth == 2 else None
    pieces, cursor, shift, out_words = [], 0, 0.0, []
    for i, wd in enumerate(words):
        out_words.append({**wd, "start": round(wd["start"] + shift, 3), "end": round(wd["end"] + shift, 3)})
        if add[i] > 0:
            at = _quietest(samples, params.nchannels, rate, wd["end"], words[i + 1]["start"]) * width
            pieces += [_fade(frames[cursor:at], width, rate, out=True), b"\0" * (int(round(add[i] * rate)) * width)]
            cursor, shift = at, shift + add[i]
    pieces.append(frames[cursor:])
    pieces[2::2] = [_fade(p, width, rate, out=False) for p in pieces[2::2]]   # each piece after a splice fades in
    # The hold is real silence after the take's own tail, so speech_window can reach it.
    pieces.append(b"\0" * (int(round(hold * rate)) * width))
    dest = voice.spoken_path(slug, beat_id)
    with wave.open(str(dest), "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(pieces))

    doc = {**raw, "words": out_words, "paced": round(shift, 3), "hold": round(hold, 3),
           "seconds": round(raw["seconds"] + shift + hold, 3),
           "speech_end": round(raw["speech_end"] + shift, 3)}
    doc["speech_seconds"] = round(doc["speech_end"] - doc["speech_start"], 3)
    from .align import align_path
    align_path(slug, beat_id).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(f"[{slug}/{beat_id}] paced +{shift:.2f}s inside, hold {hold:.2f}s" + (f"  ({'; '.join(why[:4])}…)" if why else ""))
    return True
