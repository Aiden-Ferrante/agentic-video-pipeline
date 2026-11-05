"""The variant pool: generate many, keep them, commit one to the shot list.

The unit of work is a BATCH — one prompt, N seeds — living at

    videos/<slug>/pool/<kind>/<batch_id>/
        batch.json          the request: recipe, prompt, seeds, when
        v00.png             a variant, plus v00.png.json beside it
        ...

Two rules make this safe to bolt onto an existing pipeline.

**The shot list stays the only authored file.** Committing a pick writes the
RECIPE into the beat — recipe, prompt, seed, size — not a path to a blob. The
video is still reproducible from shotlist.json alone, which is the property the
whole pipeline is built on.

**The pick is also kept as an artefact.** `selected()` finds it again by its
parameters, so a rebuild copies the exact file you chose instead of re-running
the model and hoping determinism held across a ComfyUI upgrade. The recipe is
the contract; the file is the evidence.

Rejected variants are NOT deleted. A runner-up you can return to next week is
the entire point of generating nine of something, and the disk cost of a batch
is trivial next to the cost of re-finding an idea.
"""
import json
import shutil
from pathlib import Path

from . import beats, config, library

KINDS = ("imagery", "sound")
EXT = {"imagery": ".png", "sound": ".flac"}

# Model output. Nothing is owed to anyone, but the sidecar must still say a
# machine made it — that is an IPTC digitalSourceType obligation, not a licence
# one, and it survives into C2PA.
MODEL_OUTPUT_SPDX = "LicenseRef-model-output"


def pool_dir(slug: str, kind: str) -> Path:
    return config.video_dir(slug) / "pool" / kind


def batch_dir(slug: str, kind: str, batch_id: str) -> Path:
    return pool_dir(slug, kind) / batch_id


def _display(path: Path) -> str:
    """Repo-relative when that is meaningful, absolute otherwise. VIDEOS can be
    pointed outside the repo (config supports it, and every test does it), so
    relative_to() is not safe to call unguarded."""
    try:
        return str(path.relative_to(config.REPO))
    except ValueError:
        return str(path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def start_batch(slug: str, kind: str, batch_id: str, request: dict) -> Path:
    d = batch_dir(slug, kind, batch_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "batch.json").write_text(json.dumps(
        {"id": batch_id, "kind": kind, "slug": slug, **request,
         "created_utc": library.utcnow()}, indent=2, sort_keys=True) + "\n")
    return d


def add_variant(slug: str, kind: str, batch_id: str, index: int, data: bytes,
                *, recipe: str, prompt: str, seed: int, extra: dict = None) -> Path:
    """Write one variant plus its sidecar. Returns the asset path."""
    d = batch_dir(slug, kind, batch_id)
    d.mkdir(parents=True, exist_ok=True)
    asset = d / f"v{index:02d}{EXT[kind]}"
    asset.write_bytes(data)
    library.write(
        asset,
        library=kind,
        rights_block=library.rights(MODEL_OUTPUT_SPDX, title=prompt[:80],
                                    attribution_required=False),
        provenance_block=library.provenance(
            "comfyui", source_type="model", model=recipe, prompt=prompt,
            seed=seed, inputs=extra or {}),
        selected=False,
    )
    return asset


def batches(slug: str, kind: str) -> list[dict]:
    """Newest first, each with its variants and which one won."""
    root = pool_dir(slug, kind)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        meta = _read_json(d / "batch.json")
        if not meta:
            continue
        variants = []
        for asset in sorted(d.glob(f"v*{EXT[kind]}")):
            side = library.read(asset) or {}
            variants.append({
                "file": asset.name,
                "path": _display(asset),
                "abs": str(asset),
                "seed": (side.get("provenance") or {}).get("seed"),
                "selected": bool(side.get("selected")),
                "size_bytes": side.get("size_bytes") or asset.stat().st_size,
            })
        meta["variants"] = variants
        out.append(meta)
    return out


def selected(slug: str, kind: str, *, recipe: str, prompt: str, seed: int) -> Path | None:
    """The already-picked file for these parameters, if the lab produced one.

    This is what lets a build ship exactly the take that was chosen rather than
    re-rolling the model. Matched on the parameters that define the output, not
    on a stored path, so moving a batch on disk does not break a video.
    """
    for batch in batches(slug, kind):
        for v in batch["variants"]:
            if not v["selected"]:
                continue
            side = library.read(Path(v["abs"])) or {}
            p = side.get("provenance") or {}
            if (p.get("model") == recipe and p.get("prompt") == prompt
                    and p.get("seed") == seed):
                return Path(v["abs"])
    return None


def _mark(asset: Path, chosen: bool) -> None:
    side = library.read(asset)
    if side is None:
        return
    side["selected"] = chosen
    library.sidecar_path(asset).write_text(
        json.dumps(side, indent=2, sort_keys=True) + "\n")


def commit(slug: str, kind: str, batch_id: str, variant: str,
           *, beat_id: str = "") -> dict:
    """Make one variant the winner and write its recipe into the shot list.

    For imagery that means a beat's `visual` block; for sound, the programme's
    single `sound` block. Both keep every field the model needs to reproduce
    the take, because the shot list has to stand alone.
    """
    d = batch_dir(slug, kind, batch_id)
    asset = d / variant
    if not asset.exists():
        return {"ok": False, "error": f"no variant {variant} in batch {batch_id}"}
    meta = _read_json(d / "batch.json") or {}
    side = library.read(asset) or {}
    prov = side.get("provenance") or {}

    # One winner per batch: clear the others so a sidecar never claims two.
    for other in d.glob(f"v*{EXT[kind]}"):
        _mark(other, other == asset)

    doc = _load_shotlist(slug)
    if kind == "imagery":
        if not beat_id:
            return {"ok": False, "error": "imagery commit needs a beat id"}
        block = {
            "kind": "imagery",
            "source": "generate",
            "recipe": prov.get("model") or meta.get("recipe"),
            "prompt": prov.get("prompt") or meta.get("prompt", ""),
            "seed": prov.get("seed"),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "seconds": meta.get("seconds", 5.0),
        }
        target = None
        for b in doc.get("beats", []):
            if b.get("id") == beat_id:
                target = b
                break
        if target is None:
            return {"ok": False, "error": f"no beat {beat_id} in {slug}"}
        # Keep any authored motion — that is a directing choice about the shot,
        # not a property of which variant won.
        motion = (target.get("visual") or {}).get("motion")
        if motion:
            block["motion"] = motion
        target["visual"] = block
    else:
        doc["sound"] = {
            **{k: v for k, v in (doc.get("sound") or {}).items()
               if k in ("gain_db", "fade")},
            "source": "generate",
            "recipe": prov.get("model") or meta.get("recipe"),
            "tags": prov.get("prompt") or meta.get("tags", ""),
            "seed": prov.get("seed"),
            "seconds": meta.get("seconds", 60.0),
            "bpm": meta.get("bpm", 90),
            "keyscale": meta.get("keyscale", "C major"),
        }

    _save_shotlist(slug, doc)
    return {"ok": True, "kind": kind, "beat": beat_id, "asset": _display(asset)}


def _shotlist_path(slug: str) -> Path:
    return config.video_dir(slug) / "shotlist.json"


def _load_shotlist(slug: str) -> dict:
    return _read_json(_shotlist_path(slug)) or {}


def _save_shotlist(slug: str, doc: dict) -> None:
    """Write through a temp file — a half-written shot list is the one file in
    this repo that cannot be regenerated from anything else."""
    path = _shotlist_path(slug)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    shutil.move(str(tmp), str(path))
