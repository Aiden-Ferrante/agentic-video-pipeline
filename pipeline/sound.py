"""Sound node: a music bed for the whole video, ducked under the narration.

Two sources, and the reason there are now two is worth recording.

This node used to say "curate, never generate", and the argument was sound at
the time: every open-weights music generator worth using shipped CC-BY-NC
weights — MusicGen, AudioGen, MAGNeT, AudioLDM 2, Tango 2 — and MIT inference
code does not launder NC weights. **That premise expired.** ACE-Step 1.5 ships
Apache-2.0 weights, so a locally generated bed is now commercially usable, and
the case for refusing to generate went with it. Benchmarked on this box
2026-08-30: the XL SFT tier renders a 60s bed in ~37s.

  library    a named file in `libraries/sound/`, curated by hand. Still the
             right answer for anything you did not make.
  generate   ACE-Step via the imagegen service, driven from the Gen Lab.

What did NOT change is the discipline below, and it now covers both.

**A bed without a sidecar is refused.** That is the whole discipline of this
node. YouTube processed billions of Content ID claims and false claims against
royalty-free music are a documented pattern; the difference between a two
minute dispute and an archaeology project is whether the licence, the source
URL and the retrieval date were written down at the time. Refusing to build is
cheaper than discovering the gap after upload.

Beds live in `libraries/sound/` because a bed is reusable across twenty videos,
unlike a voice take. The shot list names one:

    "sound": {"bed": "quiet-strings", "gain_db": -22, "fade": 2.0}
"""
import json
import shutil
import subprocess

from . import assemble, beats, config, library

# How far under the programme the bed sits before ducking is even applied.
# The bed's RESTING level — what you hear in a pause, not what you hear under
# speech. -22 was the old default and it conflated the two: the sidechain then
# ducked an already-buried bed into inaudibility. The mixing convention is
# music 18-25 dB below voice WHILE SPEAKING, which is what the duck produces;
# at rest the bed should simply be present.
DEFAULT_GAIN_DB = -9.0
DEFAULT_FADE = 2.0


def library_dir():
    return config.REPO / "libraries" / "sound"


def bed_path(slug: str):
    return config.video_dir(slug) / "assets" / "_bed.wav"


def spec(slug: str) -> dict | None:
    """The authored music block, read from its projection.

    From `_music.json`, not the shotlist: the fragment is what task_sound
    file_deps, so the bytes read here and the bytes doit invalidates on are the
    same bytes — editing `gain_db` now rebuilds the bed, which it silently did
    not before. Falls back to the shotlist during task ENUMERATION on a slug
    whose beats/ has never been generated; `music` is the stem name, `sound`
    the historical spelling, both accepted.
    """
    # ENUMERATION reads the authored file; DEPENDENCY tracking hangs off the
    # fragment. That is dodo.py's founding split, and reading the fragment here
    # broke it: authoring a bed for the first time left _music.json still null,
    # so task_music did not enumerate at all until `beats` had been run in a
    # separate invocation. The fragment is still what task_music file_deps, so
    # editing gain_db still rebuilds the bed — which is the whole reason the
    # projection exists.
    doc = beats.effective(slug)
    block = doc.get("music") or doc.get("sound") or {}
    return block if (block.get("bed") or block.get("source") == "generate") else None


def source_for(name: str):
    """The bed's file in the library. Any audio extension, one name."""
    for ext in (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus"):
        candidate = library_dir() / f"{name}{ext}"
        if candidate.exists():
            return candidate
    return None


def generated_path(slug: str):
    """Where a generated bed lands. Inside the video, not the shared library:
    it was made for this programme and nothing else has a claim on it."""
    return config.video_dir(slug) / "assets" / "_bed.source.flac"


def _generated_bed(slug: str, block: dict):
    """Produce (or recover) the bed this shot list asks for, with its sidecar.

    Prefers the take a human picked in the Gen Lab over re-rolling the model,
    for the same reason imagery does: the build should ship the bed that was
    listened to. The sidecar is written either way — the licence gate in
    build() is not relaxed for generated audio, it is simply satisfiable now.
    """
    from . import comfy, pool, recipes

    recipe = block.get("recipe", "ace-xl-sft")
    if recipe not in recipes.MUSIC_RECIPES:
        raise ValueError(f"unknown music recipe {recipe!r}; "
                         f"expected one of {sorted(recipes.MUSIC_RECIPES)}")
    tags = block.get("tags", "")
    if not tags:
        raise ValueError("a generated bed needs `tags` describing the music")
    seed = int(block.get("seed", 0))
    dest = generated_path(slug)
    dest.parent.mkdir(parents=True, exist_ok=True)

    picked = pool.selected(slug, "sound", recipe=recipe, prompt=tags, seed=seed)
    if picked and picked.exists():
        shutil.copyfile(picked, dest)
    else:
        if not comfy.available():
            raise RuntimeError(f"ComfyUI is not answering on {comfy.HOST} — "
                               f"start the 'imagegen' app")
        graph = recipes.music_graph(recipe, tags, seed,
                                    seconds=float(block.get("seconds", 60.0)),
                                    bpm=int(block.get("bpm", 90)),
                                    keyscale=block.get("keyscale", "C major"))
        sub = comfy.submit(graph, client_id=f"pipeline-{slug}-bed")
        if "error" in sub:
            raise RuntimeError(f"ComfyUI rejected the graph: {sub['error']}")
        res = comfy.wait(sub["prompt_id"])
        if "error" in res:
            raise RuntimeError(res["error"])
        if not res.get("files"):
            raise RuntimeError("ComfyUI produced no audio")
        dest.write_bytes(comfy.fetch(res["files"][0]))

    library.write(
        dest, library="sound",
        rights_block=library.rights("LicenseRef-model-output", title=tags[:80],
                                    attribution_required=False),
        provenance_block=library.provenance("comfyui", source_type="model",
                                            model=recipe, prompt=tags, seed=seed,
                                            stem="music"),
        selected=True)
    return dest


def build(slug: str) -> bool:
    block = spec(slug)
    if not block:
        print(f"[{slug}] no bed requested")
        return False

    if block.get("source") == "generate":
        try:
            src = _generated_bed(slug, block)
        except Exception as exc:
            print(f"[{slug}] bed generation failed: {exc}")
            return False
    else:
        name = block["bed"]
        src = source_for(name)
        if src is None:
            print(f"[{slug}] no bed named {name!r} in "
                  f"{library_dir().relative_to(config.REPO)}")
            return False

    rights = (library.read(src) or {}).get("rights")
    if not rights or not rights.get("spdx_license"):
        print(f"[{slug}] REFUSING {src.name}: no sidecar with a licence.\n"
              f"  Write {library.sidecar_path(src).name} recording spdx_license, "
              f"creator and source_url at the time you fetched it. Reconstructing "
              f"this after a Content ID claim is an archaeology project.")
        return False

    total = assemble.total_seconds(slug)
    if total <= 0:
        print(f"[{slug}] no timeline yet — nothing to lay a bed under")
        return False

    gain = float(block.get("gain_db", DEFAULT_GAIN_DB))
    fade = float(block.get("fade", DEFAULT_FADE))
    dest = bed_path(slug)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Loop to cover the programme, trim to exactly its length, then fade both
    # ends so the bed never starts or stops on a hard edge.
    chain = (f"aloop=loop=-1:size=2147483647,atrim=duration={total:.3f},"
             f"asetpts=PTS-STARTPTS,volume={gain}dB,"
             f"afade=t=in:st=0:d={fade},"
             f"afade=t=out:st={max(total - fade, 0):.3f}:d={fade}")
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-i", str(src), "-af", chain,
         "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"[{slug}] bed FAILED\n{r.stderr[-400:]}")
        return False

    library.write(
        dest, library="sound",
        # The bed's own rights, carried onto the derived file — this is the
        # copy that ends up mixed into a render, where tags do not survive.
        rights_block={**rights, "modified": True,
                      "modification_note": f"looped, trimmed to {total:.2f}s, "
                                           f"{gain}dB, faded"},
        provenance_block=library.provenance(
            "ffmpeg", source_type=(library.read(src) or {})
            .get("provenance", {}).get("digital_source_type", "human"),
            stem="music",
            inputs={"bed": src.name, "hash": library.content_hash(src)}),
        selected=True,
    )
    owed = library.attribution_line({"rights": rights})
    print(f"[{slug}] bed {name!r} {total:.2f}s at {gain}dB -> "
          f"{dest.relative_to(config.REPO)}"
          + (f"\n  credit owed: {owed}" if owed else "\n  no attribution owed"))
    return True
