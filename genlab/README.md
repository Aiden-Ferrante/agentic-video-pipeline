# Gen Lab

Generate many variants, pick one, commit it to the shot list. `:8832`.

```
make -C .. serve            # or: python3 genlab/serve.py 8832
```

Needs the `imagegen` app running (ComfyUI on `:8188`) — it is registered in
Mission Control as the `comfyui` capability that this app `requires`.

## The loop

1. Pick a video, a model and a count. One prompt, N seeds, one batch.
2. Variants land in `videos/<slug>/pool/<kind>/<batch>/` as they finish, each
   with a sidecar recording model, prompt, seed and hash.
3. Commit the one you want. Rejected variants stay — a runner-up you can return
   to next week is the whole reason for generating nine of something.

## What a commit actually writes

The **recipe**, into `shotlist.json` — recipe, prompt, seed, size — never a path
to a blob. The video stays reproducible from the shot list alone, which is the
property the rest of the pipeline is built on.

The picked file is also kept, and `pool.selected()` finds it again by those same
parameters, so a rebuild copies the take you chose instead of re-rolling the
model and trusting determinism across a ComfyUI upgrade. The recipe is the
contract; the file is the evidence.

## Boundaries

- `pipeline/recipes.py` — graphs as pure data. No HTTP, no filesystem.
- `pipeline/comfy.py` — the seam. Never imports torch; ComfyUI is a socket.
- `pipeline/pool.py` — pool storage and the shot-list write. No HTTP.
- `genlab/serve.py` — the surface. Knows no node names and no sampler settings.

The client sends **names, never paths**: a slug checked against the videos on
disk, a recipe against a fixed table, a variant against the files in its own
batch. Generate and commit are loopback-only — a commit rewrites the one file
here that cannot be regenerated from anything else.

## Model notes worth keeping

Measured on this box 2026-08-30, and they contradict the obvious guess:

| model | native | per item |
| --- | --- | --- |
| `flux2-klein-4b` | 1024², 4 steps | ~4s |
| `hidream-o1` | 2048², 28 steps | ~32s |
| `flux2-dev` | 1024², 20 steps | ~63s |
| `ace-turbo` | 8 steps | ~10s / 60s bed |
| `ace-xl-sft` | 50 steps | ~37s / 60s bed |

HiDream-O1 renders four times the pixels of FLUX.2 dev in half the time.

**The image and audio fast tiers are not equivalent.** Klein-4B at 4 steps is
usable output. ACE turbo at 8 steps puts a hard transient roughly every second
into a bed that asked for no drums — it is an audition tool, not a source of
anything that ships. Use `ace-xl-sft` for a bed you intend to keep.

ACE-Step also renders **short**: a 60s request came back 54–57s. Ask for ~10%
more than the programme needs.
