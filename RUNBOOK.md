# Runbook — one video, one week

The loop an agent runs for the human (STEPWISE REFINEMENT — The Line, 2026-10-08). The human writes the script
and gives notes in chat; everything else is a command here.

## 1 · Start a video

- Registry: add the module to `content-manager/projects/<series>.json`. ML videos bind `"style": "ml-series"` — the
  template (acts, a generated score per act, word-cued sound, captions, dips to black, the clone narrating).
- Screenplay in the vault (`Projects/Videos/<series>/…`), pointed at by the shot list's `script`.
- `compile_script` (MCP) → `shotlist.json`. It reports `cue_problems` if a reworded line orphaned a sound cue.

## 2 · Script → film

```
python -m pipeline.voice_cost <slug>        # what the voice will cost, quota, whether the clone is ready
doit film:<slug>                            # everything: voice, alignment, pauses, pictures, fit, acts, score, mix, film
```

- The film: `videos/<slug>/out/finish/<slug>-full.mp4`. (`out/master.mp4` is the narration-only master.)
- Voice: the ElevenLabs clone (registry `tools.narration`); a recorded take at `voice/takes/<beat>.wav` wins.
  The spend cap (`ELEVENLABS_MAX_BEATS` in `.env`) stops a run at that many beats — raise it on purpose for a
  first full voicing. A Professional Voice Clone still training is refused, nothing spent.
- If doit says the lock is busy, `./doit_retry.sh …` waits for the canvas's poll.

## 3 · Notes → edits

- "at 4:12 …" → `film_at(<slug>, "4:12")` (MCP) or `finish.locate`: act, beat, offset, the word being said,
  that beat's sound cues, whether it is in a dip.
- Where the edit goes:

| note about | edit | rebuilds |
|---|---|---|
| words | the vault screenplay → `compile_script` | that beat's voice, picture timing, its act |
| a pause | the beat's `pause` (`[{"word", "nth", "seconds"}]`, silence after that word) or `hold` (s after the last word); every beat's rules: the style's `timing.pauses` | that beat's paced wav, its picture, its act — never re-voiced |
| a sound effect | the beat's `sfx` in `shotlist.json` (word, nth, fx, gain dB, offset s) | that act's mix, the film |
| an act's music | `finish.acts.<chapter>.score` (mood + seed, or the Act I `arc`) | that act's score, mix, film |
| a picture | the beat's `visual` / its kit shot | that beat (and the beats importing the same kit file) |
| a picture that "restarts" at a cut | the later beat's `params.clock_after: [earlier beats]` + the first beat's params (a Triptych also `continue: true`): one timeline, no re-assembly | that beat |
| the template | `libraries/styles/ml-series.json` — fork it, never edit a shipped style | every video binding it |

- Then `doit film:<slug>` again. A heard score is never regenerated: kept, cut, or continued.

## 4 · Publish

`doit publish:<slug>` writes what the upload form needs. The upload itself stays manual.

## Where things live

| what | where |
|---|---|
| finish scripts | `video-pipeline/finish/` (paths in `finish/paths.py`) |
| template | `libraries/styles/ml-series.json` |
| a video's choices | `videos/<slug>/shotlist.json` (`finish`, beats' `sfx`) |
| the score's take, the SFX kit | `libraries/sound/` (sidecars tracked, media not) |
| model envs | `~/models/voice`, `~/models/stable-audio` (`VOICE_ROOT`, `STABLE_AUDIO_ROOT`) |
| the key | `content-operation/.env` (never the registry) |
