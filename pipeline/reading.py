"""Reading node: how long a beat takes to SAY, derived from the text alone.

The timing contract has always been "narration wins, picture yields" — but that
required audio to exist before anything could be cut, which put a TTS pass in
front of every visual decision. This module supplies the same number from the
script, so a shot list can be timed with no voice track at all.

WHY SYLLABLES AND NOT WORDS. Calibrated against this project's own 18 beats of
forced-alignment ground truth (Kokoro reads, WhisperX-style alignment):

    syllables + punctuation    mean |error| 0.35s   max 1.09s   R^2 0.963
    best-fit words-per-minute  mean |error| 0.93s   max 2.12s

Words-per-minute is off by nearly a second on a typical beat because it prices
"extraordinary" and "cat" identically, and charges nothing for a full stop.
Since a beat here runs 3-10s, a 0.9s error is a visible mistiming and a 0.35s
one is not.

THE CONSTANTS ARE A PACE, NOT A LAW. They were fitted to a synthetic read at
roughly 123 wpm — a slow, documentary register that happens to suit this
material. A different narrator is a different pace, so `pace` scales the whole
estimate: pace=1.15 is 15% slower (a longer beat), pace=0.9 is brisker. Re-fit
by running the calibration against real takes once they exist; the shape of the
model (syllables + pauses) should hold, only the coefficients move.
"""
import re

# Fitted 2026-08-24 against videos/origins (n=18). See the module docstring.
SECONDS_PER_SYLLABLE = 0.2307      # 4.33 syllables/sec
SENTENCE_PAUSE = 0.486             # a full stop, question or exclamation
CLAUSE_PAUSE = 0.234               # comma, semicolon, colon, dash
OVERHEAD = -0.720                  # fitted intercept; see MIN_SECONDS below

# The fitted intercept is NEGATIVE — it exists to stop the linear syllable term
# from over-charging short beats, and it is only meaningful inside the range the
# fit saw (12-48 syllables). Extrapolated down to a two-word beat it would
# happily return a negative duration, so every estimate is clamped. A shot has
# to be on screen long enough to be seen regardless of how little is said over
# it.
MIN_SECONDS = 1.2

_WORD = re.compile(r"[A-Za-z']+")
_VOWEL_GROUP = re.compile(r"[aeiouy]+")
_SENTENCE_END = re.compile(r"[.!?]")
_CLAUSE_BREAK = re.compile(r"[,;:]|—|--")


def syllables(word: str) -> int:
    """Vowel-group count with the two corrections that actually matter in prose.

    Deliberately a heuristic and not a dictionary: a pronunciation dictionary
    would be a data dependency and a licence question, and it buys very little
    here because the estimate is averaged over a whole beat — individual word
    errors cancel rather than accumulate.
    """
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    n = len(_VOWEL_GROUP.findall(w))
    # Silent terminal 'e' ("time", "structure") — but not where the e carries a
    # syllable itself ("simple", "agree", "eye").
    if n > 1 and w.endswith("e") and not w.endswith(("le", "ee", "ye")):
        n -= 1
    return max(1, n)


def estimate(text: str, pace: float = 1.0) -> float:
    """Seconds to read `text` aloud at narration pace.

    Punctuation is charged as time, not decoration: the pause at a full stop is
    a real half-second, and a beat written as three short sentences genuinely
    runs longer than the same words run together.
    """
    if not text or not text.strip():
        return 0.0
    syl = sum(syllables(w) for w in _WORD.findall(text))
    if not syl:
        return 0.0
    seconds = (syl * SECONDS_PER_SYLLABLE
               + len(_SENTENCE_END.findall(text)) * SENTENCE_PAUSE
               + len(_CLAUSE_BREAK.findall(text)) * CLAUSE_PAUSE
               + OVERHEAD)
    return round(max(MIN_SECONDS, seconds) * max(0.1, pace), 3)
