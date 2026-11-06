# agentic-video-pipeline

[![tests](https://github.com/Aiden-Ferrante/agentic-video-pipeline/actions/workflows/tests.yml/badge.svg)](https://github.com/Aiden-Ferrante/agentic-video-pipeline/actions/workflows/tests.yml)

A build system that turns a written script into a finished, narrated video. An agent drives it through an MCP server, and a human keeps the creative calls. Each stage is a separate adapter: voice (ElevenLabs or local TTS), forced alignment, Manim animation, ComfyUI imagery, Stable Audio score, and assembly. The stages talk to each other through one shared contract, the timed **shot list**.

**Result:** it produces the long-form videos on my YouTube channel, [Ferrante²](https://www.youtube.com/@ferrante_2x). The test suite (187 tests) runs offline from a fresh clone in under a second, without any of the media tools installed.

This is a code-only snapshot of a private repo. Rendered media, voice takes and per-video projects are left out. One real shot list is included in `examples/axioms/`.

## Quickstart

```bash
git clone https://github.com/Aiden-Ferrante/agentic-video-pipeline.git && cd agentic-video-pipeline
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # 187 passed, offline
```

A full render also needs ffmpeg, Manim, a ComfyUI server, local voice and Stable Audio models, and, for cloud narration, an `ELEVENLABS_API_KEY` in the environment. See `RUNBOOK.md`. The tests don't need any of these.

## How it works

```
script ──compile_script──▶ shotlist.json ──doit──▶ voice ─▶ align ─▶ pace/fit
                              (contract)               ├──▶ animation (Manim)
                                                       ├──▶ imagery (ComfyUI / archival)
                                                       └──▶ score + SFX (Stable Audio)
                                                                 ▼
                                   review ◀── film ◀── mix ◀── assemble (OpenTimelineIO)
                                     │
                     "at 4:12 …" → film_at() → the beat, word and cue at that moment
```

- **One contract between stages.** Each stage reads the shot list and writes a JSON sidecar recording what it made, from which inputs, and with which tool. Sidecars are tracked in git and media is not, so a sidecar answers "who made this, for what" after the file is gone.
- **One adapter contract for every external tool** (`pipeline/bridge.py`): interpreter, script, subcommand, JSON in, one JSON verdict out. Local models and the ElevenLabs API wear the same contract (`bridges/`), so the build doesn't care which provider runs a stem (`pipeline/providers.py`).
- **A task graph, not a script** (`dodo.py`, built on doit). Stages rebuild only when their inputs change, and `doit film:<slug>` runs the whole chain.
- **Agent tools with a human in the loop** (`pipeline/mcp_server.py`):
  - `compile_script`, `script_diff` and `adopt_script` turn a reworded script into shot-list changes, and report orphaned sound cues instead of silently dropping them.
  - `film_at` maps a reviewer's timestamp note to a beat.
  - `retime`, `reorder` and `check_edit_drift` reshape the timeline.
  - The human writes the script, records or approves the voice, and gives notes. The agent does the rest.
- **Spend and safety guards:**
  - `pipeline/voice_cost.py` prices a build before it runs.
  - The ElevenLabs bridge has a hard per-run cap and refuses a voice clone that hasn't finished training.
  - A credential-shaped value in a project file is refused, because keys live only in the environment.

## What went wrong / what I learned

- **Two adapters had quietly drifted apart.** The voice and visual bridges were written separately. One caught `TimeoutExpired` and the other raised it, and they logged 12 and 15 stderr lines. I pulled out a single `bridge.py` contract and both now delegate to it.
- **A byte diff caught what the tests didn't.** A corrupted registry file would have silently sent every video to the default providers and re-synthesised the already-voiced narration, which costs money. The fix: a bad path now fails its own beat with a warning instead of the whole build.
- **The spend cap is a hard cap on purpose.** The failure it guards against isn't extravagance. It's an invalidation accident, where one edit makes doit think every beat needs a new voice.
- **References by id break when scripts change.** Archival plates were keyed by beat id, and a rewrite broke one silently. They're now keyed by subject.
- **Silence is a stem, not an error.** Unnarrated title and divider cards used to fail voiced builds. Every stage now treats missing narration as an empty stem.

## Layout

| Path | What |
|---|---|
| `pipeline/` | stages (voice, align, pace, fit, animation, imagery, sound, assemble, captions, thumbnail, shorts), the shot-list/screenplay compiler, the MCP server |
| `bridges/` | out-of-process adapters: ElevenLabs, local voice/alignment |
| `finish/` | act cuts, captions, final mix and timestamp lookup |
| `genlab/`, `map/` | small local web UIs: a generation lab for choosing image variants, and a canvas for the workflow map |
| `libraries/` | provenance sidecars for the music, SFX and style templates (the media itself is not included) |
| `examples/axioms/` | a real 63-beat shot list |
| `MAP.md`, `RUNBOOK.md` | the workflow design and the one-video loop |
| `tests/` | 187 offline tests |

MIT licensed.
