"""Captions node: alignments plus the timeline become subtitles.

Do not transcribe. We know what was said — the shot list says so — and we know
when, from the forced alignment. Every open-source caption tool is a Whisper
wrapper bolted to an ASS templater; we would be adopting the half we do not
need and fighting the half we do.

Written by hand rather than through pysubs2, for the same reason `map/` has no
framework: emitting ASS is a hundred lines of string formatting, and the thin
venv is worth more than the hundred lines. It also buys exact typographic
control, which matters here — a caption that lands on top of an equation is
worse than no caption.

Two outputs, deliberately:

    out/<slug>.ass    styled, for burning in
    out/<slug>.srt    plain, uploaded as a soft track so YouTube can index it
                      and viewers can turn it off

Burning in is the caller's choice, not this node's:

    ffmpeg -i master.mp4 -vf ass=out/<slug>.ass -c:a copy captioned.mp4
"""
from . import align, assemble, beats, config

# A caption is read in a glance or not at all. Two lines of this width is the
# ceiling before the eye starts tracking instead of taking it in.
MAX_CHARS = 42
MAX_LINES = 2
MAX_SECONDS = 5.0
# A gap this long inside a sentence is a breath, and a natural place to break.
BREATH = 0.45

FONT = "DejaVu Sans"
SIZE = 54
# With BorderStyle 3 the box is drawn in OutlineColour, NOT BackColour — a
# well-known ASS trap. Both are set to the same translucent near-black so the
# box never fully hides what is underneath it.
# Bottom-centred, well inside the safe area — Manim compositions use the lower
# third, so captions sit under it rather than over it.
MARGIN_V = 70


def ass_path(slug: str):
    return config.video_dir(slug) / "out" / f"{slug}.ass"


def srt_path(slug: str):
    return config.video_dir(slug) / "out" / f"{slug}.srt"


def _cues(slug: str) -> list[dict]:
    """Word timings from every beat, shifted onto the finished timeline."""
    spans = assemble.beat_spans(slug)
    text_by_beat = {b["id"]: beats.narration_text(b)
                    for b in beats.load(slug).get("beats", [])}

    cues = []
    for beat, start, _duration in spans:
        doc = align.load(slug, beat)
        if not doc or not doc.get("words"):
            continue
        # A beat's audio is trimmed to its speech window, so word times measured
        # in the wav must be rebased to where the beat sits in the master.
        window = align.speech_window(slug, beat)
        shift = start - (window[0] if window else 0.0)

        # The alignment normalises words to bare uppercase for the model; the
        # shot list has the real punctuation and casing. Zip them back together
        # so the caption reads like writing, not like a transcript.
        spoken = text_by_beat.get(beat, "").split()
        original = [w for w in spoken if any(c.isalnum() for c in w)]

        for i, word in enumerate(doc["words"]):
            cues.append({
                "beat": beat,
                "text": original[i] if i < len(original) else word["word"].title(),
                "start": round(word["start"] + shift, 3),
                "end": round(word["end"] + shift, 3),
            })
    return cues


def _group(cues: list[dict]) -> list[dict]:
    """Words become readable lines: break on breath, length, or a beat change."""
    lines, current = [], []

    def flush():
        if current:
            lines.append({
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": " ".join(c["text"] for c in current),
            })
            current.clear()

    for cue in cues:
        if current:
            same_beat = cue["beat"] == current[-1]["beat"]
            width = len(" ".join(c["text"] for c in current)) + 1 + len(cue["text"])
            too_long = width > MAX_CHARS * MAX_LINES
            too_slow = cue["end"] - current[0]["start"] > MAX_SECONDS
            breath = cue["start"] - current[-1]["end"] > BREATH
            if not same_beat or too_long or too_slow or breath:
                flush()
        current.append(cue)
    flush()

    # A caption must never outlive its successor's entrance.
    for a, b in zip(lines, lines[1:]):
        a["end"] = min(a["end"], b["start"] - 0.02)
    return [ln for ln in lines if ln["end"] > ln["start"]]


def _wrap(text: str) -> str:
    """Break into at most MAX_LINES, balanced.

    Greedy filling leaves a full first line and an orphan on the second, which
    reads badly at a glance. Splitting at the point that minimises the
    difference between the two lines costs one loop and looks deliberate.
    """
    words = text.split()
    if len(words) < 2 or len(text) <= MAX_CHARS:
        return text
    best, best_cost = None, None
    for i in range(1, len(words)):
        top, bottom = " ".join(words[:i]), " ".join(words[i:])
        if max(len(top), len(bottom)) > MAX_CHARS * 1.35:
            continue
        cost = abs(len(top) - len(bottom))
        if best_cost is None or cost < best_cost:
            best, best_cost = (top, bottom), cost
    return "\\N".join(best) if best else text


def _ass_time(t: float) -> str:
    h, rem = divmod(max(t, 0.0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _srt_time(t: float) -> str:
    h, rem = divmod(max(t, 0.0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{round((s % 1) * 1000):03d}"


def build(slug: str) -> bool:
    doc = beats.load(slug)
    width, height = doc.get("resolution", [1920, 1080])
    lines = _group(_cues(slug))
    if not lines:
        print(f"[{slug}] no alignments to caption")
        return False

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT},{SIZE},&H00FFFFFF,&H8C101010,&H8C101010,0,0,3,6,0,2,80,80,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = "".join(
        f"Dialogue: 0,{_ass_time(l['start'])},{_ass_time(l['end'])},Default,,0,0,0,,{_wrap(l['text'])}\n"
        for l in lines)
    out_ass = ass_path(slug)
    out_ass.parent.mkdir(parents=True, exist_ok=True)
    out_ass.write_text(header + events)

    srt = "".join(
        f"{i}\n{_srt_time(l['start'])} --> {_srt_time(l['end'])}\n"
        f"{_wrap(l['text']).replace(chr(92) + 'N', chr(10))}\n\n"
        for i, l in enumerate(lines, start=1))
    srt_path(slug).write_text(srt)

    print(f"[{slug}] captions: {len(lines)} cues -> {out_ass.name}, {srt_path(slug).name}")
    return True
