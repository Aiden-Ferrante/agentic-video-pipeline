"""pace: pauses are topped up by what ends at each word, never shortened, and the alignment shifts exactly."""
from pipeline import pace


def _words(text, gap=0.1):
    out, t = [], 0.0
    for w in pace.tokens(text):
        out.append({"word": w["word"], "start": t, "end": t + 0.3}); t += 0.3 + gap
    return out


R = pace.DEFAULTS


def test_targets_follow_the_punctuation():
    text = "Is it a line? Written out: J equals one over two m, times the sum. Try it."
    add, hold, _ = pace.plan(text, _words(text), R)
    toks = [t["raw"] for t in pace.tokens(text)]
    got = {toks[i]: round(a + 0.1, 2) for i, a in enumerate(add) if a}
    assert got["line?"] == R["question"] and got["out:"] == R["colon"]
    assert got["m,"] == R["comma"] and got["sum."] == R["math"]
    assert hold == R["hold"]["punch"]                           # "Try it." — a short last sentence


def test_a_pause_the_voice_already_took_is_kept():
    text = "One. Two three four five six."
    add, _, _ = pace.plan(text, _words(text, gap=2.0), R)
    assert add == [0.0] * 6


def test_a_dropped_dash_hands_its_pause_to_the_word_before():
    text = "rules — and then"
    assert [t["word"] for t in pace.tokens(text)] == ["RULES", "AND", "THEN"]
    add, _, _ = pace.plan(text, _words(text), R)
    assert round(add[0] + 0.1, 2) == R["dash"]


def test_a_beat_can_ask_for_its_own_pause_and_hold():
    text = "the gradient points uphill and we step down"
    add, hold, _ = pace.plan(text, _words(text), R, {"pause": [{"word": "uphill", "seconds": 1.5}], "hold": 1.0})
    assert round(add[3] + 0.1, 2) == 1.5 and hold == R["hold"]["default"] + 1.0


def test_mismatched_tokens_pace_nothing():
    add, hold, why = pace.plan("one two", [{"word": "ONE", "start": 0, "end": 0.3}], R)
    assert add == [0.0] and hold == 0.0 and why[0].startswith("token mismatch")
