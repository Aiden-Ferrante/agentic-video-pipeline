"""The one asset convention, shared by all five media. SCAFFOLDING.

Every asset — a voice take, a generated image, a licensed music bed — gets a
sidecar JSON next to it. The sidecar is the truth; anything else (an index, a
cache) is derived and rebuildable.

That is not a style preference, it is forced by measurement. On this machine
Pillow silently drops every PNG text chunk on save, ffmpeg's PNG encoder cannot
write them at all, and mixing a licensed track into a render destroys its
embedded tags entirely. Metadata embedded in a file does not survive our own
pipeline, let alone a platform upload.

Five things recur in every medium, and they are the whole contract:

  1. a stable opaque id, plus a content hash
  2. one winner — a boolean, never a rating scale; downstream never learns
     that alternatives existed
  3. provenance captured at production time, never reconstructed later
  4. this sidecar as truth, embedded metadata as redundancy
  5. an invalidation key we own

Where the media genuinely differ:

  script     the only one git handles natively; lives per-video, not in a library
  voice      the only one whose REJECTED variants keep value — a performance
             is unrepeatable, so takes are not cheap to regenerate
  animation  the only one where the SOURCE is the asset and the render is a
             cache; it already lives as a library at ~/Desktop/code/manim
  imagery    the only one where the standard already exists and generators
             embed it for free — except diffusers, which writes nothing
  sound      the only one with legal exposure; its metadata defends a claim
             rather than finds a file, which is why the ledger must be primary
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config

LIBRARIES = ("script", "voice", "animation", "imagery", "sound")

# IPTC DigitalSourceType — a plain controlled vocabulary, no certificates and no
# trust list, and C2PA reuses the same terms so it stays forward-compatible.
# Note the precision: Manim is a pure algorithm with no training data.
SOURCE_TYPES = {
    "human": "digitalCapture",
    "algorithm": "algorithmicMedia",           # Manim
    "model": "trainedAlgorithmicMedia",        # diffusion, TTS
    "composite": "compositeSynthetic",         # a finished video
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(path: Path) -> str:
    """Algorithm-prefixed, always. A bare hex string you cannot migrate is the
    single most common regret in content-addressed stores."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def rights(spdx: str, *, title="", creator="", creator_url="", source_url="",
           attribution_required=True, attribution_override="",
           modified=False, modification_note="", retrieved="") -> dict:
    """The block that must never be lost.

    `spdx` carries the VERSION ('CC-BY-4.0', not 'CC-BY'). Freesound's
    3.0 -> 4.0 migration in 2022 was opt-in rather than automatic, so the
    version genuinely varies per asset and cannot be assumed.

    `source_url` is the LANDING page, never the raw file — that is what an
    attribution line has to link to.

    `modified` exists because CC-BY 4.0 s3(a)(1)(B) obliges you to state that
    you changed the work. It is not part of the usual TASL formula and nothing
    else will remind you.
    """
    return {
        "spdx_license": spdx,
        "title": title,
        "creator": creator,
        "creator_url": creator_url,
        "source_url": source_url,
        "attribution_required": bool(attribution_required),
        "attribution_override": attribution_override,
        "modified": bool(modified),
        "modification_note": modification_note,
        "retrieved_utc": retrieved or utcnow(),
    }


def provenance(tool: str, *, tool_version="", source_type="algorithm",
               model="", prompt="", seed=None, inputs=None, stem="") -> dict:
    """Captured when the asset is produced. Reconstructing this later is the
    failure mode in every one of the five media.

    `tool` is the provider that actually ran — the bridge's own answer
    ("kokoro", "chatterbox", "commons"), not the requested one; `stem` is
    which stem the asset serves, so a sidecar answers "who made this, for
    what" without consulting the pipeline. `inputs` holds free-form
    provenance facts — use content_hash() values when an input is a file, raw
    values otherwise. It is NOT an invalidation key; invalidation lives in the
    doit fragments, and a provenance record is more useful carrying the actual
    text than a hash of it.
    """
    out = {
        "tool": tool,
        "tool_version": tool_version,
        "digital_source_type": SOURCE_TYPES.get(source_type, source_type),
        "model": model,
        "prompt": prompt,
        "seed": seed,
        "inputs": inputs or {},
        "produced_utc": utcnow(),
    }
    if stem:
        out["stem"] = stem
    return out


def sidecar_path(asset: Path) -> Path:
    """`<file>.json`, beside the asset — the darktable/digiKam convention of a
    sidecar that keeps the original extension, so `b01.mp4.json` is
    unambiguous and never collides with `b01.png.json`."""
    return asset.with_suffix(asset.suffix + ".json")


def write(asset: Path, *, library: str, rights_block=None, provenance_block=None,
          selected=None, **extra) -> Path:
    """Write (or update) an asset's sidecar. Returns the sidecar path."""
    if library not in LIBRARIES:
        raise ValueError(f"unknown library {library!r}; expected one of {LIBRARIES}")
    doc = {
        "id": asset.stem,
        "library": library,
        "hash": content_hash(asset),
        "size_bytes": asset.stat().st_size,
        "rights": rights_block,
        "provenance": provenance_block,
        # One winner, a boolean. Never a rating scale — no medium here has a
        # use for "3 out of 5 stars", and a scale invites re-litigating choices.
        "selected": selected,
        **extra,
    }
    path = sidecar_path(asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path


def read(asset: Path) -> dict | None:
    path = sidecar_path(asset)
    return json.loads(path.read_text()) if path.exists() else None


def all_sidecars(root: Path = None) -> list[dict]:
    """Every sidecar under the repo. At thousands of files a glob is still
    faster than maintaining an index, which is why there isn't one yet."""
    root = root or config.REPO
    out = []
    for path in sorted(root.rglob("*.json")):
        if path.parent.name == "beats":       # derived shot-list fragments
            continue
        try:
            doc = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(doc, dict) and doc.get("library") in LIBRARIES:
            out.append(doc)
    return out


def attribution_line(doc: dict) -> str | None:
    """One credit line, TASL order (Title, Author, Source, Licence).

    Returns None when no attribution is owed — CC0 and the YouTube Audio
    Library's own licence require none, and padding the credits with things
    that don't need crediting makes the ones that do easier to miss.
    """
    r = doc.get("rights") or {}
    if not r.get("attribution_required"):
        return None
    if r.get("attribution_override"):
        return r["attribution_override"]

    bits = []
    if r.get("title"):
        bits.append(f'"{r["title"]}"')
    if r.get("creator"):
        bits.append(f'by {r["creator"]}')
    if r.get("source_url"):
        bits.append(f'({r["source_url"]})')
    if r.get("spdx_license"):
        bits.append(f'— licensed under {r["spdx_license"]}')
    if r.get("modified"):
        bits.append("— modified" + (f': {r["modification_note"]}'
                                    if r.get("modification_note") else ""))
    return " ".join(bits) if bits else None


def credits(root: Path = None) -> str:
    """The block that goes in the video description. Deduplicated, sorted."""
    lines = {ln for ln in (attribution_line(d) for d in all_sidecars(root)) if ln}
    return "\n".join(sorted(lines))


# ------------------------------------------------------------------ stubs

def cas_put(asset: Path) -> str:
    """Move an asset into a content-addressed store and return its hash.

    Deliberately unbuilt. Content-addressing with one consumer is speculative
    generality: there is one video today, and zero of the 198 existing renders
    are byte-identical duplicates, so dedupe would buy nothing yet. Earn this
    when two videos genuinely share an asset.
    """
    raise NotImplementedError(
        "cas_put is scaffolding. Blocked on: having two videos that actually "
        "share an asset. Until then the sidecar alone carries the meaning.")


def index_rebuild(root: Path = None) -> int:
    """Rebuild a queryable index from the sidecars.

    Deliberately unbuilt. SQLite is the right shape when it is needed — WAL
    plus BEGIN IMMEDIATE handles concurrent writers fine, and DuckDB can ATTACH
    it in place for analytics. But it would be a derived cache over data that
    `grep` still searches faster than we could maintain a schema.
    """
    raise NotImplementedError(
        "index_rebuild is scaffolding. Blocked on: grep over sidecars becoming "
        "too slow. At thousands of files it has not.")
