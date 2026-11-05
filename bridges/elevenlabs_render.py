#!/usr/bin/env python3
"""ElevenLabs narration adapter. Same contract as every bridge: subcommand in
argv[1], one JSON request on stdin, one JSON verdict on stdout.

The first cloud adapter, and deliberately shaped like the local ones even
though it needs no heavy environment — it runs on the pipeline's own venv,
stdlib urllib only — so that cloud and local are indistinguishable to
voice.render: an interpreter, a script, a command, a verdict.

THE KEY LIVES IN THE ENVIRONMENT AND NOWHERE ELSE. `ELEVENLABS_API_KEY` is
read here, at the last possible moment; providers.resolve refuses
credential-shaped values in the registry; and a missing key is a clean
{"ok": false} refusal, never a stack trace. This repo had zero credentials
before this file, and the .env/.gitignore hygiene landed in the same commit.

THE SPEND GUARD IS A HARD CAP. `ELEVENLABS_MAX_BEATS` bounds how many beats a
single doit run may synthesise (default 10; unset means the default, NOT
unlimited). The failure it exists for is not extravagance but a stale
dependency graph: one wrong byte in a fragment re-synthesises a whole module,
and a bill should require intent, not an invalidation accident.

Commands:
  synth  {"text", "out", "voice_id"?, "model_id"?, "voice_settings"?,
          "previous_text"?, "next_text"?} ->
         {"ok", "backend": "elevenlabs", "model", "voice_id", "request_id"}

`voice_settings` passes through verbatim (stability, similarity_boost, style,
use_speaker_boost). `previous_text`/`next_text` are the adjacent beats'
narration: without them every beat is synthesized cold and opens with fresh
announcer energy — the single most AI-sounding artifact of per-beat synthesis.
With them the engine shapes prosody as one continuous read.
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"          # "Rachel", the API's stock default
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_MAX_BEATS = 10
# Keyed by the parent pid: doit runs task actions in-process, so every beat's
# bridge subprocess shares the same parent — the cap naturally scopes to one
# run and resets with the next. Env override exists for the tests.
_COUNTER = os.environ.get("ELEVENLABS_RUN_COUNTER",
                          f"/tmp/elevenlabs-spend-{os.getppid()}")


def _cap() -> int:
    raw = os.environ.get("ELEVENLABS_MAX_BEATS", "") or _env_file_value("ELEVENLABS_MAX_BEATS")
    try:
        return int(raw) if raw else DEFAULT_MAX_BEATS
    except ValueError:
        return DEFAULT_MAX_BEATS


def _spent() -> int:
    """Beats synthesised by this doit run. Each task is its own process, so
    the count lives in a scratch file keyed by the parent doit process — the
    caller exports ELEVENLABS_RUN_COUNTER per run; the default file makes a
    lone invocation still count against something."""
    try:
        with open(_COUNTER) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _record_spend() -> None:
    try:
        with open(_COUNTER, "w") as f:
            f.write(str(_spent() + 1))
    except OSError:
        pass


def _env_file_value(name: str) -> str:
    """The repo's own .env (content-operation/.env, gitignored), read only when the environment lacks the value —
    so an agent-driven build needs no `source` step. The environment always wins."""
    path = os.environ.get("ELEVENLABS_ENV_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
    try:
        with open(path) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k.strip() == name and not line.lstrip().startswith("#"):
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def api_key() -> str:
    return os.environ.get("ELEVENLABS_API_KEY", "").strip() or _env_file_value("ELEVENLABS_API_KEY")


def voice_ready(v: dict) -> bool:
    """A Professional Voice Clone is usable once a model has finished fine-tuning on it; any other category (an
    instant clone, a stock voice) is usable as soon as it exists."""
    if v.get("category") != "professional":
        return True
    state = (v.get("fine_tuning") or {}).get("state") or {}
    return bool(v.get("high_quality_base_model_ids")) or any(x == "fine_tuned" for x in state.values())


def _not_ready(key: str, voice_id: str) -> str:
    """Why this voice must not be paid for yet, or "" — so a stale graph cannot spend on a clone mid-training."""
    try:
        req = urllib.request.Request(f"{API}/voices/{voice_id}", headers={"xi-api-key": key})
        with urllib.request.urlopen(req, timeout=20) as resp:
            v = json.load(resp)
    except urllib.error.HTTPError as exc:
        return f"elevenlabs HTTP {exc.code} reading voice {voice_id}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"elevenlabs unreachable: {exc}"
    if not voice_ready(v):
        return (f"voice {v.get('name', voice_id)!r} is a Professional Voice Clone still training — nothing "
                f"synthesised. `python -m pipeline.voice_cost <slug>` says when it is ready.")
    return ""


def synth(req: dict) -> dict:
    key = api_key()
    if not key:
        return {"ok": False,
                "error": "ELEVENLABS_API_KEY is not set — the key lives in the "
                         "environment, never in the registry. `set -a; source "
                         ".env` before the build, or switch the project's "
                         "narration provider back to local."}

    spent, cap = _spent(), _cap()
    if spent >= cap:
        return {"ok": False,
                "error": f"spend cap: this run already synthesised {spent} "
                         f"beats and ELEVENLABS_MAX_BEATS={cap}. Raise the cap "
                         f"deliberately if the count is expected — a surprise "
                         f"here usually means an invalidation accident."}

    text = (req.get("text") or "").strip()
    out = req.get("out")
    if not text or not out:
        return {"ok": False, "error": "synth needs `text` and `out`"}
    voice_id = req.get("voice_id") or DEFAULT_VOICE
    model_id = req.get("model_id") or DEFAULT_MODEL
    if voice_id != DEFAULT_VOICE:
        why = _not_ready(key, voice_id)
        if why:
            return {"ok": False, "error": why}

    payload = {"text": text, "model_id": model_id}
    # `field`, emphatically not `key` — a loop variable named `key` here once
    # shadowed the API key and sent the literal string "next_text" as the
    # xi-api-key header. The server said "invalid API key"; it was not wrong.
    for field in ("voice_settings", "previous_text", "next_text"):
        if req.get(field):
            payload[field] = req[field]
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{API}/text-to-speech/{voice_id}?output_format=pcm_48000",
        data=body, method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json",
                 "Accept": "audio/pcm"})
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            audio = resp.read()
            request_id = resp.headers.get("request-id", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        return {"ok": False, "error": f"elevenlabs HTTP {exc.code}: {detail}"}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "error": f"elevenlabs unreachable: {exc}"}

    # Raw PCM to WAV without pulling in anything beyond the stdlib.
    import wave
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(audio)

    _record_spend()
    return {"ok": True, "backend": "elevenlabs", "model": model_id,
            "voice_id": voice_id, "request_id": request_id,
            "seconds": len(audio) / (2 * 48000)}


COMMANDS = {"synth": synth}


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = COMMANDS.get(command)
    if handler is None:
        print(json.dumps({"ok": False, "error": f"unknown command {command!r}"}))
        return
    try:
        req = json.loads(sys.stdin.read() or "{}")
        print(json.dumps(handler(req)))
    except Exception as exc:                      # noqa: BLE001 — verdict, not traceback
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))


if __name__ == "__main__":
    main()
