"""Styles: one named file that says how a KIND of video looks, sounds and reads.

Two styles existed before this module did, and nobody owned either. Logic's
"3B1B × Epic History" lived in a vault note, a registry `tools` block, six
copied `grade` blocks and a pace constant; Linear Algebra's plainer register
lived in the header of one script. A style makes that one thing, in
`libraries/styles/<id>.json`, beside the music beds — because a style is
reusable across videos the way a bed is, and per-video material is not.

Three sections, kept apart on purpose:

    writer    prose for the person (or the writers' room prompt): the rules,
              the register, the target length, the act shape. Nothing reads it.
    checks    numbers with thresholds. Reserved for the linter (next cycle);
              carried now so a style is complete on the day it is written.
    bindings  defaults the pipeline reads: timing, grade, letterbox, voice
              DELIVERY, music policy, chapter tag. This is what this module
              resolves.

WHERE IT SITS. A style is HOW, never WHAT or WHEN, so it is bound the way a
provider is: the owning project's registry names it (`"style": "epic-history"`),
a shot list may override with its own `style`, and any block authored in the
shot list wins over the template, field by field. Precedence: shot list, then
style, then the built-in default. Adopting styles therefore changes nothing
on disk — the two extracted styles reproduce the blocks they were extracted
from, byte for byte, which is the zero-invalidation contract this cycle is
held to.

IDENTITY VS DELIVERY. The registry keeps who narrates (provider, voice id,
model); a style keeps how the line is delivered (`voice_settings`). A style
that named a voice would make two projects with different narrators impossible
without forking the style, and a registry that carried delivery would put
taste in a file whose job is bindings.

IMMUTABLE ONCE SHIPPED. Six mastered modules bind `epic-history`. Editing it
re-renders all six — the `scm_style.py` lesson. Change a shipped style by
forking it (`epic-history-2`) and binding new modules to the fork; `version`
is in the file so the fork is legible.

DEGRADES TO NOTHING. No registry, no `style`, a style file that is missing
or malformed — every one resolves to "no style" with at most a warning, and
the shot list's own blocks carry the build exactly as they did before.
"""
import json
from pathlib import Path

from . import config

STYLES = config.REPO / "libraries" / "styles"

# The shot-list blocks a style may supply defaults for, and how each merges.
#   dict    style underlays; shot-list fields win one level deep
#   scalar  shot list wins when present
#   music   merged ONLY when the module authors a bed — a style never creates one
BLOCKS = {"timing": "dict", "grade": "dict", "chapter_tag": "dict", "finish": "dict",
          "letterbox": "scalar", "music": "music"}
# Delivery fields a style may carry for narration. Identity stays in the registry.
DELIVERY = ("voice_settings",)


def path_for(style_id: str) -> Path:
    return STYLES / f"{style_id}.json"


def load(style_id: str) -> dict | None:
    """The style file as a dict, or None (with a warning) when it cannot be read.
    Never raises: resolution runs during task enumeration."""
    p = path_for(style_id)
    try:
        doc = json.loads(p.read_text())
    except FileNotFoundError:
        print(f"[styles] WARNING no style file for {style_id!r} at {p}; "
              f"building from the shot list alone")
        return None
    except (OSError, ValueError) as e:
        print(f"[styles] WARNING {p.name} unreadable ({e}); building from the "
              f"shot list alone")
        return None
    if not isinstance(doc, dict):
        print(f"[styles] WARNING {p.name} is not an object; ignored")
        return None
    return doc


def bound_id(slug: str) -> str | None:
    """Which style a slug wears: its shot list's `style`, else its owning
    project's `style`, else none."""
    from . import beats, projects
    try:
        own = beats.load(slug).get("style")
    except (OSError, ValueError):
        # No shot list (a slug resolved before it exists, or a test's bare
        # registry): a style needs a document to underlay.
        return None
    if isinstance(own, str) and own.strip():
        return own.strip()
    owner = projects.owner_of().get(slug)
    if owner is None:
        return None
    for project in projects.load():
        if project["id"] == owner:
            s = project.get("style")
            return s.strip() if isinstance(s, str) and s.strip() else None
    return None


def style_file(slug: str) -> Path | None:
    """The file whose bytes decide a slug's style defaults, or None.

    A CONDITIONAL file_dep, the accepted pattern from task_tools' registry
    file: binding or unbinding a style changes the dep set of two cheap tasks
    (beats, tools) and nothing else, because the fragments they write are
    write-if-changed.
    """
    sid = bound_id(slug)
    if not sid:
        return None
    p = path_for(sid)
    return p if p.exists() else None


def identity(slug: str) -> dict | None:
    """{"id", "version", "title", "source"} for the canvas and the MCP seam."""
    from . import beats
    sid = bound_id(slug)
    if not sid:
        return None
    doc = load(sid) or {}
    try:
        own = beats.load(slug).get("style")
    except (OSError, ValueError):
        own = None
    source = "shot list" if own else "registry"
    return {"id": sid, "version": doc.get("version"), "title": doc.get("title", sid),
            "source": source, "exists": path_for(sid).exists()}


def bindings(slug: str) -> dict:
    """The style's `bindings` block for a slug, or {}."""
    sid = bound_id(slug)
    if not sid:
        return {}
    doc = load(sid)
    if not doc:
        return {}
    b = doc.get("bindings") or {}
    if not isinstance(b, dict):
        print(f"[styles] WARNING {sid}: `bindings` is not an object; ignored")
        return {}
    return b


def effective(doc: dict, b: dict) -> dict:
    """A shot list with a style's bindings merged UNDER it. Pure.

    `doc` is the raw authored document and is not modified: the merged copy is
    what the pipeline reads, never what gets saved — the compiler and Gen Lab
    write the authored file and must never bake a default into it.
    """
    if not b:
        return doc
    out = dict(doc)
    for key, how in BLOCKS.items():
        if key not in b:
            continue
        if how == "dict":
            if not isinstance(b[key], dict):
                continue
            out[key] = {**b[key], **(doc.get(key) or {})}
        elif how == "scalar":
            if key not in doc:
                out[key] = b[key]
        elif how == "music":
            # sound.spec's own test for "this module wants a bed". A block that
            # is only a note ("deliberately no bed") must stay a note: gain and
            # fade defaults on a bed that does not exist would change the
            # fragment's bytes and rebuild a timeline for nothing.
            block = doc.get("music") or doc.get("sound")
            wants = isinstance(block, dict) and (block.get("bed") or block.get("source") == "generate")
            if wants and isinstance(b[key], dict):
                out["music"] = {**b[key], **block}
    return out


def delivery(slug: str) -> dict:
    """The narration delivery defaults a style supplies: only the DELIVERY
    fields, so a style cannot smuggle a voice id past the registry."""
    n = bindings(slug).get("narration")
    if not isinstance(n, dict):
        return {}
    extra = [k for k in n if k not in DELIVERY]
    if extra:
        print(f"[styles] WARNING {bound_id(slug)}: narration carries {extra}; "
              f"a style binds delivery only ({', '.join(DELIVERY)}), identity "
              f"lives in the registry. Ignored.")
    return {k: v for k, v in n.items() if k in DELIVERY}


def available() -> list[dict]:
    """Every style on disk, for a picker."""
    out = []
    if not STYLES.exists():
        return out
    for p in sorted(STYLES.glob("*.json")):
        doc = load(p.stem) or {}
        out.append({"id": p.stem, "title": doc.get("title", p.stem),
                    "version": doc.get("version")})
    return out
