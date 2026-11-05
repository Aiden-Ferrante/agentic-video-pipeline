"""voice_cost: only beats whose words or voice changed are spent; takes and current reads cost nothing."""
import json

from pipeline import beats, library, providers, voice, voice_cost

_real_bridge = voice_cost._bridge


def _setup(tmp_path, monkeypatch, provider="elevenlabs"):
    doc = {"beats": [{"id": "a", "vo_text": "One two three."}, {"id": "b", "vo_text": "Four five."},
                     {"id": "c", "vo_text": "Six."}, {"id": "s", "vo_text": ""}]}
    monkeypatch.setattr(beats, "load", lambda slug: doc)
    monkeypatch.setattr(providers, "resolved_map",
                        lambda slug: {"narration": {"provider": provider, "config": {"voice_id": "V1"}}})
    monkeypatch.setattr(voice, "take_path", lambda slug, b: tmp_path / "takes" / f"{b}.wav")
    monkeypatch.setattr(voice, "wav_path", lambda slug, b: tmp_path / f"{b}.wav")
    monkeypatch.setattr(voice_cost, "_bridge", lambda: type("B", (), {"_cap": staticmethod(lambda: 3),
                                                                       "api_key": staticmethod(lambda: ""), "API": ""}))
    (tmp_path / "takes").mkdir()
    return doc


def _sidecar(tmp_path, bid, tool, text, voice_id="V1"):
    (tmp_path / f"{bid}.wav.json").write_text(json.dumps(
        {"provenance": {"tool": tool, "inputs": {"vo_text": text, "voice_id": voice_id}}}))


def test_only_changed_words_are_spent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "takes" / "a.wav").write_bytes(b"")            # a recorded take wins
    _sidecar(tmp_path, "b", "elevenlabs", "Four five.")           # already the clone's read of these words
    _sidecar(tmp_path, "c", "elevenlabs", "Six!")                 # words changed since
    p = voice_cost.plan("fx")
    status = {r["beat"]: r["status"] for r in p["beats"]}
    assert status == {"a": "take", "b": "current", "c": "synth"}  # silent beat s is not listed
    assert (p["synth_beats"], p["synth_chars"], p["cap_beats_per_run"]) == (1, len("Six."), 3)


def test_a_new_voice_respends_and_scratch_is_spent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _sidecar(tmp_path, "a", "kokoro", "One two three.")
    _sidecar(tmp_path, "b", "elevenlabs", "Four five.", voice_id="OLD")
    p = voice_cost.plan("fx")
    assert [r["beat"] for r in p["beats"] if r["status"] == "synth"] == ["a", "b", "c"]


def test_local_provider_costs_nothing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, provider="local")
    assert voice_cost.plan("fx")["synth_chars"] == 0


def test_a_professional_clone_is_ready_only_after_fine_tuning():
    voice_cost._bridge = _real_bridge
    assert not voice_cost.voice_ready({"category": "professional", "fine_tuning": {"state": {}}})
    assert voice_cost.voice_ready({"category": "professional", "fine_tuning": {"state": {"eleven_multilingual_v2": "fine_tuned"}}})
    assert voice_cost.voice_ready({"category": "cloned"})


def test_use_takes_false_and_new_settings_respend(tmp_path, monkeypatch):
    """2026-10-08: the clone reads every line (use_takes: false) and a delivery change (stability) re-voices."""
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(voice, "narration_config", lambda slug: {"use_takes": False})
    monkeypatch.setattr(providers, "resolved_map", lambda slug: {"narration": {
        "provider": "elevenlabs", "config": {"voice_id": "V1", "voice_settings": {"stability": 1.0}}}})
    (tmp_path / "takes" / "a.wav").write_bytes(b"")
    _sidecar(tmp_path, "b", "elevenlabs", "Four five.")                     # read with the old settings
    (tmp_path / "c.wav.json").write_text(json.dumps({"provenance": {"tool": "elevenlabs", "inputs": {
        "vo_text": "Six.", "voice_id": "V1", "voice_settings": {"stability": 1.0}}}}))
    status = {r["beat"]: r["status"] for r in voice_cost.plan("fx")["beats"]}
    assert status == {"a": "synth", "b": "synth", "c": "current"}
