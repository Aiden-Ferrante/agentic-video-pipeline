# Shared libraries

Assets here are reusable **across** videos. Per-video material does not live
here — see the split below, which is not uniform and shouldn't be forced to be.

| Medium | Where it lives | Why |
|---|---|---|
| **script** | `videos/<slug>/script.md` | belongs to exactly one video; git handles it natively |
| **voice** | `videos/<slug>/voice/` | a take reads one line of one script; not reusable, but the takes are worth keeping because a performance is unrepeatable |
| **animation** | `~/Desktop/code/manim` | already a library — 55 scenes over a shared `scm_style.py`. The **source** is the asset; the mp4 is a cache |
| **imagery** | `libraries/imagery/` | a period portrait is reusable across a series |
| **sound** | `libraries/sound/` | a music bed is reusable across twenty videos |
| **styles** | `libraries/styles/` | how a KIND of video reads, sounds and looks — bound per project, overridable per shot list; see its README |

Every asset carries a `<file>.json` sidecar written by `pipeline/library.py`.
The sidecar is the truth. Embedded metadata is redundancy only — measured on
this box, Pillow drops PNG text chunks on save, ffmpeg's PNG encoder cannot
write them, and mixing destroys audio tags outright.

**Sound is the only medium with legal exposure.** Its sidecar exists to defend
a claim, not to find a file, so the rights block is mandatory there. Record the
licence *version* (`CC-BY-4.0`, never `CC-BY`) — Freesound's 3.0→4.0 migration
was opt-in, so it genuinely varies per asset.
