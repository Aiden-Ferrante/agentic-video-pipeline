"""Alignment node: voice/<beat>.wav + its text become voice/<beat>.align.json.

Forced alignment, not transcription. We already know what was said — the shot
list says so — and an ASR pass would be free to disagree with it, which turns a
timing problem into a text-reconciliation problem. WhisperX would also be the
wrong tool on this machine regardless: CTranslate2 has no CUDA-enabled aarch64
wheel and silently runs on CPU.

What this buys, downstream:

  * the beat's true spoken length, which is what the timeline is cut to
  * word timings for captions
  * bookmark timestamps, so an animation can be told to hit a specific word
  * a confidence score, which is the only thing that catches a take drifting
    from the script — bad alignments still return plausible monotonic numbers
"""
import json

from . import beats, config, voice

# Below this mean score, the audio and the text have probably diverged. On a
# clean synthetic read of the right words this sits around 0.9; deliberately
# aligning unrelated text to the same audio scores about 0.22.
CONFIDENCE_FLOOR = 0.45


def align_path(slug: str, beat_id: str):
    return voice.voice_dir(slug) / f"{beat_id}.align.json"


def build(slug: str, beat_id: str) -> bool:
    frag = json.loads(beats.fragment_path(slug, beat_id, "vo").read_text())
    wav = voice.wav_path(slug, beat_id)
    if not wav.exists():
        print(f"[{slug}/{beat_id}] no voice track to align")
        return False

    result = voice.bridge("align", {"wav": str(wav), "text": frag.get("vo_text", "")})
    if not result.get("ok"):
        print(f"[{slug}/{beat_id}] align FAILED: {result.get('error')}")
        return False

    words = result["words"]
    mean = sum(w["score"] for w in words) / len(words) if words else 0.0
    result["mean_score"] = round(mean, 4)
    result["beat"] = beat_id
    # Trailing room is dead air; the timeline cuts to speech, not to file length.
    result["speech_seconds"] = round(result["speech_end"] - result["speech_start"], 3)

    if mean < CONFIDENCE_FLOOR:
        print(f"  [warn] {slug}/{beat_id} aligned at {mean:.2f} — the recording and "
              f"the shot list have probably drifted apart")

    from .pace import raw_align_path
    out = raw_align_path(slug, beat_id)     # the voice as read; pace writes align_path from it
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"[{slug}/{beat_id}] aligned {len(words)} words, "
          f"{result['speech_seconds']}s speech, score {mean:.2f}")
    return True


def load(slug: str, beat_id: str) -> dict | None:
    path = align_path(slug, beat_id)
    return json.loads(path.read_text()) if path.exists() else None


# Cutting hard against the first and last word sounds clipped even when it is
# technically clean, so a beat carries a little air at each end.
LEAD, TAIL = 0.12, 0.25


def speech_window(slug: str, beat_id: str) -> tuple[float, float] | None:
    """(offset_into_the_wav, duration) — the part of the take worth keeping.

    Recorded and synthesised audio both arrive with dead air around the words.
    Trimming from the ALIGNMENT rather than from a silence detector is why this
    pipeline needs no auto-editor: we know where the speech is because we know
    what was said.
    """
    doc = load(slug, beat_id)
    if not doc or not doc.get("words"):
        return None
    start = max(0.0, doc["speech_start"] - LEAD)
    # `hold`: pace's silence after the last word (a question waits), real samples in the paced wav.
    end = min(doc["seconds"], doc["speech_end"] + TAIL + doc.get("hold", 0.0))
    return round(start, 3), round(end - start, 3)


def spoken_seconds(slug: str, beat_id: str) -> float:
    """How long this beat takes to say. The timeline's contract.

    Returns 0.0 when there is no alignment, which callers read as "fall back to
    the animation's own length".
    """
    window = speech_window(slug, beat_id)
    return window[1] if window else 0.0
