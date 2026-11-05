"""Which tool renders each stem, resolved per project. The ports-and-adapters seam.

A canvas node is a PORT — a stage with a contract about what it eats and emits.
A tool behind it is an ADAPTER. Which adapter a production uses is a BINDING,
declared in the owning project's `tools` block in the registry — and this
module is the RESOLVER, the piece that was missing while `tools` sat in
logic.json marked "declared, NOT yet read by anything".

TOOLS CHOOSE HOW, NEVER WHAT OR WHEN. The registry stays non-authoritative for
content (shotlist.json) and time (the promoted edit) — a provider entry may
change which engine renders a beat's narration, never what the narration says
or how long the beat runs. That line is the projects.py authority table, and
it is load-bearing: the day a tools block starts carrying content is the day
the registry becomes a third source of truth, which is how v1 died.

DEGRADES TO THE DEFAULTS. No registry, no owning project, no tools block, an
unknown provider — every one of these resolves to the built-in default with at
most a warning. The warn-don't-raise rule is beats.timing()'s: resolution runs
during task work, and an authoring typo must fail its own beat with a message,
not take the build system down.

Two spellings are accepted per stem and normalised to one shape:

    "narration": "kokoro"                                # string shorthand
    "narration": {"provider": "elevenlabs", "voice_id": "..."}   # dict form

Legacy flat keys (voice, animation, imagery, sound) alias to their stems
forever — a project file is authored by a person, and old spellings must keep
meaning what they meant.
"""
from . import projects, stems

# The built-in adapter per stem — exactly the behaviour the pipeline had
# before this module existed. "visuals" resolves per sub-key because animation
# and imagery are different generators behind one stem.
DEFAULTS = {
    "narration": "local",     # the voice bridge: kokoro, or chatterbox on a reference
    "visuals.animation": "manim",
    "visuals.imagery": "commons",   # per-beat source/motion switches stay in imagery.py
    "music": "library",
    "captions": "align",
}


def _tools_for(slug: str) -> dict:
    owner = projects.owner_of().get(slug)
    if owner is None:
        return {}
    for project in projects.load():
        if project["id"] == owner:
            return project.get("tools") or {}
    return {}


def _raw_entry(tools: dict, stem: str, sub: str | None):
    """The authored entry for a stem, honouring nesting and legacy spellings."""
    key = f"{stem}.{sub}" if sub else stem
    # nested stem form: {"visuals": {"animation": "manim"}}
    block = tools.get(stem)
    if sub and isinstance(block, dict) and "provider" not in block:
        if sub in block:
            return block[sub]
    elif not sub and block is not None:
        return block
    # legacy flat spellings
    for legacy, target in stems.LEGACY_TOOL_KEYS.items():
        if legacy in tools:
            t_stem, *t_sub = target
            if t_stem == stem and (t_sub[0] if t_sub else None) == sub:
                return tools[legacy]
    return None


def resolve(slug: str, stem: str, sub: str | None = None) -> dict:
    """{"provider": str, "config": dict} for a stem of a slug's production.

    `sub` narrows a multi-generator stem ("visuals" + "animation"/"imagery").
    Always answers; never raises on authoring mistakes.
    """
    key = f"{stem}.{sub}" if sub else stem
    default = DEFAULTS.get(key, "local")
    entry = _raw_entry(_tools_for(slug), stem, sub)

    if entry is None:
        return {"provider": default, "config": _underlay_delivery(slug, key, {})}
    if isinstance(entry, str):
        name = entry.strip()
        if _looks_like_credential(name):
            print(f"[providers] WARNING {slug}/{key}: the tools entry looks like "
                  f"a credential — keys live in the environment, never in the "
                  f"registry. Using the default.")
            return {"provider": default, "config": {}}
        # Descriptive prose from the pre-resolver era ("none (recorded by
        # hand)", "diffusers/qwen-image") passes through as the provider name;
        # adapters that do not recognise it fall back themselves.
        return {"provider": name or default,
                "config": _underlay_delivery(slug, key, {})}
    if isinstance(entry, dict):
        name = str(entry.get("provider") or default)
        cfg = _underlay_delivery(slug, key,
                                 {k: v for k, v in entry.items() if k != "provider"})
        leaked = [k for k, v in cfg.items()
                  if isinstance(v, str) and _looks_like_credential(v)]
        if leaked:
            print(f"[providers] WARNING {slug}/{key}: config field(s) "
                  f"{leaked} look like credentials — keys live in the "
                  f"environment, never in the registry.")
        return {"provider": name, "config": cfg}

    print(f"[providers] WARNING {slug}/{key}: unrecognised tools entry "
          f"{type(entry).__name__}; using the default {default!r}")
    return {"provider": default, "config": {}}


def _underlay_delivery(slug: str, key: str, cfg: dict) -> dict:
    """The style's narration DELIVERY under the registry's config.

    Registry wins, one level deep: a project that sets `voice_settings.style`
    keeps it while the rest of the style's settings fill in around it. Only
    narration has delivery; every other stem passes through untouched.
    """
    if key != "narration":
        return cfg
    from . import styles
    base = styles.delivery(slug)
    if not base:
        return cfg
    out = dict(base)
    for k, v in cfg.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _looks_like_credential(value: str) -> bool:
    """A cheap tripwire, not a scanner: long opaque tokens and known key
    prefixes. False negatives are fine — the point is to catch the reflex of
    pasting a key where a name goes."""
    v = value.strip()
    if any(v.startswith(p) for p in ("sk-", "sk_", "key-", "Bearer ", "eyJ")):
        return True
    return len(v) >= 32 and " " not in v and v.isalnum() and not v.islower()


def resolved_map(slug: str) -> dict:
    """Every stem's resolved binding for a slug — the payload of _tools.json.

    THE FRAGMENT IS WHY SWITCHING PROVIDERS REBUILDS. doit invalidates on
    bytes, and a provider chosen at render time out of the registry would be
    invisible to it — the fps precedent in beats.py: what changes the output
    must live inside the dependency bytes. task_tools writes this map;
    task_narration depends on it; flipping a project's narration provider
    changes the bytes and the narration chain rebuilds while visuals sit still.
    """
    return {
        "narration": resolve(slug, "narration"),
        "visuals.animation": resolve(slug, "visuals", "animation"),
        "visuals.imagery": resolve(slug, "visuals", "imagery"),
        "music": resolve(slug, "music"),
        "captions": resolve(slug, "captions"),
    }


def write_fragment(slug: str) -> bool:
    """task_tools' action: resolve and persist, write-if-changed."""
    from . import beats
    path = beats.tools_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    beats._write_if_changed(path, resolved_map(slug))
    return True


def registry_file(slug: str):
    """The registry file whose bytes decide a slug's bindings, or None.

    task_tools' conditional file_dep — the same accepted pattern as the
    promoted edit in task_master: a slug gaining or losing an owner changes
    the dep set of exactly one cheap task.
    """
    owner = projects.owner_of().get(slug)
    if owner is None:
        return None
    candidate = projects.PROJECTS / f"{owner}.json"
    return candidate if candidate.exists() else None
