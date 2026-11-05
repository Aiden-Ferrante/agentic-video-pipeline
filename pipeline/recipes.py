"""Workflow graphs for the ComfyUI seam — one function per model, pure data.

Every graph here is a transcription of the workflow ComfyUI itself ships in
`comfyui_workflow_templates_json`. That is deliberate and it is the single most
important rule in this file: each model has a sampler chain it was tuned
against, and substituting a generic one FAILS SILENTLY — you get a picture or a
track, just a markedly worse one. Cite the template before changing a setting.

  HiDream-O1  needs ModelSamplingAuraFlow-free pixel sampling via SamplerCustom
              with ModelNoiseScale 7.6 + SamplerLCM, and is trained at ~4MP.
  FLUX.2      derives its sigmas from latent sequence length (Flux2Scheduler),
              so a plain KSampler cannot reproduce its schedule.
  ACE-Step    lyrics="[instrumental]" is what suppresses singing; an empty
              lyrics field makes the model improvise vocals over the bed.

No HTTP, no filesystem, no timing. Just dicts.
"""

# Each recipe's native settings. `steps` differ by an order of magnitude
# between distilled and full tiers, so they belong with the recipe, never in
# a caller's default.
IMAGE_RECIPES = {
    "hidream-o1": {
        "label": "HiDream-O1 (8B, MIT)",
        "steps": 28, "width": 2048, "height": 2048,
        "needs": {"checkpoints": ["hidream_o1_image_dev_bf16.safetensors"]},
        "note": "best photoreal quality/speed here; trained at ~4MP, degrades below it",
    },
    "flux2-klein-4b": {
        "label": "FLUX.2 klein-4B (Apache-2.0)",
        "steps": 4, "width": 1024, "height": 1024,
        "needs": {"unets": ["flux-2-klein-4b.safetensors"],
                  "text_encoders": ["qwen_3_4b.safetensors"],
                  "vaes": ["flux2-vae.safetensors"]},
        "note": "4 steps — the fast idea-generator; the only image model here free for commercial use",
    },
    "flux2-dev": {
        "label": "FLUX.2 dev (32B, NON-COMMERCIAL)",
        "steps": 20, "width": 1024, "height": 1024,
        "needs": {"unets": ["flux2_dev_fp8mixed.safetensors"],
                  "text_encoders": ["mistral_3_small_flux2_fp8.safetensors"],
                  "vaes": ["flux2-vae.safetensors"]},
        "note": "strong on hard-surface detail; weights are non-commercial — never for a published frame",
    },
}

MUSIC_RECIPES = {
    "ace-turbo": {
        "label": "ACE-Step 1.5 turbo (Apache-2.0)",
        "steps": 8, "cfg": 1.0,
        "needs": {"checkpoints": ["ace_step_1.5_turbo_aio.safetensors"]},
        "note": "~6x realtime — the idea generator",
    },
    "ace-xl-sft": {
        "label": "ACE-Step 1.5 XL SFT (Apache-2.0)",
        "steps": 50, "cfg": 7.0,
        "needs": {"unets": ["acestep_v1.5_xl_sft_bf16.safetensors"],
                  "text_encoders": ["qwen_0.6b_ace15.safetensors",
                                    "qwen_4b_ace15.safetensors"],
                  "vaes": ["ace_1.5_vae.safetensors"]},
        "note": "the finals tier",
    },
}

INSTRUMENTAL = "[instrumental]"


# ------------------------------------------------------------------ imagery

def _flux2(unet, clip, prompt, width, height, steps, seed, guider):
    g = {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": clip, "type": "flux2", "device": "default"}},
        "vae": {"class_type": "VAELoader",
                "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "pos": {"class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["clip", 0]}},
        "latent": {"class_type": "EmptyFlux2LatentImage",
                   "inputs": {"width": width, "height": height, "batch_size": 1}},
        "sigmas": {"class_type": "Flux2Scheduler",
                   "inputs": {"steps": steps, "width": width, "height": height}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "sample": {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["noise", 0], "guider": ["guider", 0],
                              "sampler": ["sampler", 0], "sigmas": ["sigmas", 0],
                              "latent_image": ["latent", 0]}},
        "decode": {"class_type": "VAEDecode",
                   "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage",
                 "inputs": {"images": ["decode", 0], "filename_prefix": "genlab"}},
    }
    g.update(guider)
    return g


def image_graph(recipe: str, prompt: str, seed: int, *, width=None, height=None,
                steps=None) -> dict:
    if recipe not in IMAGE_RECIPES:
        raise ValueError(f"unknown image recipe {recipe!r}; "
                         f"expected one of {sorted(IMAGE_RECIPES)}")
    r = IMAGE_RECIPES[recipe]
    w = width or r["width"]
    h = height or r["height"]
    st = steps or r["steps"]

    if recipe == "hidream-o1":
        return {
            "ckpt": {"class_type": "CheckpointLoaderSimple",
                     "inputs": {"ckpt_name": "hidream_o1_image_dev_bf16.safetensors"}},
            "noisescale": {"class_type": "ModelNoiseScale",
                           "inputs": {"model": ["ckpt", 0], "noise_scale": 7.6}},
            "pos": {"class_type": "CLIPTextEncode",
                    "inputs": {"text": prompt, "clip": ["ckpt", 1]}},
            "neg": {"class_type": "CLIPTextEncode",
                    "inputs": {"text": "", "clip": ["ckpt", 1]}},
            "latent": {"class_type": "EmptyHiDreamO1LatentImage",
                       "inputs": {"width": w, "height": h, "batch_size": 1}},
            "sigmas": {"class_type": "BasicScheduler",
                       "inputs": {"model": ["noisescale", 0], "scheduler": "normal",
                                  "steps": st, "denoise": 1.0}},
            "sampler": {"class_type": "SamplerLCM",
                        "inputs": {"s_noise": 1.0, "s_noise_end": 1.0,
                                   "noise_clip_std": 2.5}},
            "sample": {"class_type": "SamplerCustom",
                       "inputs": {"model": ["noisescale", 0], "add_noise": True,
                                  "noise_seed": seed, "cfg": 1.0,
                                  "positive": ["pos", 0], "negative": ["neg", 0],
                                  "sampler": ["sampler", 0], "sigmas": ["sigmas", 0],
                                  "latent_image": ["latent", 0]}},
            "decode": {"class_type": "VAEDecode",
                       "inputs": {"samples": ["sample", 0], "vae": ["ckpt", 2]}},
            "save": {"class_type": "SaveImage",
                     "inputs": {"images": ["decode", 0], "filename_prefix": "genlab"}},
        }

    if recipe == "flux2-dev":
        return _flux2("flux2_dev_fp8mixed.safetensors",
                      "mistral_3_small_flux2_fp8.safetensors",
                      prompt, w, h, st, seed,
                      {"guide": {"class_type": "FluxGuidance",
                                 "inputs": {"conditioning": ["pos", 0], "guidance": 4.0}},
                       "guider": {"class_type": "BasicGuider",
                                  "inputs": {"model": ["unet", 0],
                                             "conditioning": ["guide", 0]}}})

    if recipe == "flux2-klein-4b":
        return _flux2("flux-2-klein-4b.safetensors", "qwen_3_4b.safetensors",
                      prompt, w, h, st, seed,
                      {"neg": {"class_type": "ConditioningZeroOut",
                               "inputs": {"conditioning": ["pos", 0]}},
                       "guider": {"class_type": "CFGGuider",
                                  "inputs": {"model": ["unet", 0], "positive": ["pos", 0],
                                             "negative": ["neg", 0], "cfg": 1.0}}})

    raise ValueError(f"unknown image recipe {recipe!r}")


# -------------------------------------------------------------------- music

def _ace_tail(model_ref, clip_ref, vae_ref, tags, seconds, steps, cfg, seed,
              bpm, keyscale):
    return {
        "shift": {"class_type": "ModelSamplingAuraFlow",
                  "inputs": {"model": model_ref, "shift": 3.0}},
        "pos": {"class_type": "TextEncodeAceStepAudio1.5",
                "inputs": {"clip": clip_ref, "tags": tags, "lyrics": INSTRUMENTAL,
                           "seed": seed, "bpm": bpm, "duration": float(seconds),
                           "timesignature": "4", "language": "en",
                           "keyscale": keyscale, "generate_audio_codes": True,
                           "cfg_scale": 2.0, "temperature": 0.85, "top_p": 0.9,
                           "top_k": 0, "min_p": 0.0}},
        "neg": {"class_type": "ConditioningZeroOut",
                "inputs": {"conditioning": ["pos", 0]}},
        "latent": {"class_type": "EmptyAceStep1.5LatentAudio",
                   "inputs": {"seconds": float(seconds), "batch_size": 1}},
        "sample": {"class_type": "KSampler",
                   "inputs": {"model": ["shift", 0], "seed": seed, "steps": steps,
                              "cfg": cfg, "sampler_name": "euler",
                              "scheduler": "simple", "denoise": 1.0,
                              "positive": ["pos", 0], "negative": ["neg", 0],
                              "latent_image": ["latent", 0]}},
        "decode": {"class_type": "VAEDecodeAudio",
                   "inputs": {"samples": ["sample", 0], "vae": vae_ref}},
        # FLAC, not MP3: this is a master that still has to survive a duck, a
        # fade and the programme's own encode. Encoding twice is audible.
        "save": {"class_type": "SaveAudio",
                 "inputs": {"audio": ["decode", 0], "filename_prefix": "genlab"}},
    }


def music_graph(recipe: str, tags: str, seed: int, *, seconds=60.0, steps=None,
                bpm=90, keyscale="C major") -> dict:
    if recipe not in MUSIC_RECIPES:
        raise ValueError(f"unknown music recipe {recipe!r}; "
                         f"expected one of {sorted(MUSIC_RECIPES)}")
    r = MUSIC_RECIPES[recipe]
    st = steps or r["steps"]

    if recipe == "ace-turbo":
        g = {"ckpt": {"class_type": "CheckpointLoaderSimple",
                      "inputs": {"ckpt_name": "ace_step_1.5_turbo_aio.safetensors"}}}
        g.update(_ace_tail(["ckpt", 0], ["ckpt", 1], ["ckpt", 2], tags, seconds,
                           st, r["cfg"], seed, bpm, keyscale))
        return g

    if recipe == "ace-xl-sft":
        g = {
            "unet": {"class_type": "UNETLoader",
                     "inputs": {"unet_name": "acestep_v1.5_xl_sft_bf16.safetensors",
                                "weight_dtype": "default"}},
            "clip": {"class_type": "DualCLIPLoader",
                     "inputs": {"clip_name1": "qwen_0.6b_ace15.safetensors",
                                "clip_name2": "qwen_4b_ace15.safetensors",
                                "type": "ace", "device": "default"}},
            "vae": {"class_type": "VAELoader",
                    "inputs": {"vae_name": "ace_1.5_vae.safetensors"}},
        }
        g.update(_ace_tail(["unet", 0], ["clip", 0], ["vae", 0], tags, seconds,
                           st, r["cfg"], seed, bpm, keyscale))
        return g

    raise ValueError(f"unknown music recipe {recipe!r}")


def missing(recipe_def: dict, available: dict) -> list[str]:
    """Which of a recipe's required weight files ComfyUI cannot see."""
    gaps = []
    for kind, names in recipe_def.get("needs", {}).items():
        have = set(available.get(kind) or [])
        gaps.extend(n for n in names if n not in have)
    return gaps
