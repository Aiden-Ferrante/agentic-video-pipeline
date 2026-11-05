"""What a project is: an ordered list of module slugs, and nothing else.

A production was the top of the world until now — `config.slugs()` globs
`videos/*/shotlist.json` and hands back a flat list. That was true when there
was one video. It stopped being true the moment five slugs shared a `_note`, a
`grade` block and a save timestamp: they were one work, and the pipeline had no
word for it.

THIS REPO READS THE REGISTRY, IT DOES NOT OWN IT. The files live with
content-manager, which is the portfolio layer. That inversion is deliberate and
load-bearing: a project chooses which tools render which nodes, so
video-pipeline is ONE pipeline a project might use, not the place a project is
defined. Owning the registry here would bake in the assumption that every
project is built this way, which is exactly the assumption about to stop being
true.

FILES, NOT AN API, even though content-manager serves this over HTTP. A build
must not require a dashboard to be running — `doit program` has to work with
:8955 down, and reading a directory keeps that true. Point `PROJECTS_DIR`
elsewhere to relocate the registry.

DEGRADES TO NOTHING. No registry means no projects, and every module still
builds on its own. The project layer composes work; it must never be a
precondition for doing it.

STDLIB ONLY, DELIBERATELY. `map/serve.py` is a zero-dependency server that runs
outside `.venv`, and it needs the same answer to "which project owns this slug"
that the build side needs. One module both can import beats two that drift.
That is also why this file does not import `pipeline.config`: importing the
package pulls in modules that want doit and otio.

AUTHORITY. This declares IDENTITY and ORDER. It is deliberately not
authoritative for time (that stays `out/<slug>.edit.otio`) or for content (that
stays `shotlist.json`). A project file that starts holding durations is the
beginning of a third source of truth, which is how v1 died.
"""
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# A sibling of this repo's directory, not an absolute path: the registry lives
# with content-manager, and after the 2026-08-26 merge that is the module next
# door rather than a separate checkout. Still an env var first — this module's
# whole point is that the registry's location is not the pipeline's business.
PROJECTS = Path(os.environ.get(
    "PROJECTS_DIR", REPO.parent / "content-manager" / "projects"))
VIDEOS = REPO / "videos"

UNFILED = "unfiled"


def _on_disk() -> set[str]:
    if not VIDEOS.exists():
        return set()
    return {p.parent.name for p in VIDEOS.glob("*/shotlist.json")}


def load() -> list[dict]:
    """Every declared project, in filename order, each module tagged `missing`
    when it names a slug with no shot list.

    A missing module is REPORTED, never dropped. Silently omitting it would
    make a typo in this file look identical to a module that was never built —
    and the whole point of the project layer is to show what the work contains.
    """
    out = []
    if not PROJECTS.exists():
        return out
    disk = _on_disk()
    for path in sorted(PROJECTS.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            # A malformed project file must not blank the canvas: skip this one
            # and let the others render. It resurfaces as unfiled slugs.
            continue
        mods = []
        for m in doc.get("modules") or []:
            slug = m.get("slug")
            if not slug:
                continue
            mods.append({**m, "slug": slug, "missing": slug not in disk})
        out.append({"id": doc.get("id", path.stem),
                    "title": doc.get("title", path.stem),
                    "order": doc.get("order", 50),
                    "outline": doc.get("outline"),
                    # Carried for DISPLAY. Nothing resolves a tool from these
                    # yet, and the canvas says so where it shows them.
                    "pipeline": doc.get("pipeline"),
                    "tools": doc.get("tools") or {},
                    # The project's style binding. Resolved by pipeline/styles.py;
                    # a shot list's own `style` overrides it.
                    "style": doc.get("style"),
                    "modules": mods})
    # Explicit `order`, not filename order. The canvas opens on the first
    # project, and alphabetically `fixtures` beats `logic` — which would greet
    # you with a smoke test every morning. Ties fall back to the filename so the
    # result is still deterministic.
    out.sort(key=lambda p: (p["order"], p["id"]))
    return out


def owner_of(projects: list[dict] | None = None) -> dict[str, str]:
    """{slug: project id}. First declaration wins.

    Two projects claiming one slug is a contradiction the loader cannot resolve
    — order in the master timeline would depend on filename order, which is not
    a decision anyone made. Whoever declares it first keeps it, and `conflicts`
    names the rest so it is visible rather than arbitrary.
    """
    owner = {}
    for p in projects if projects is not None else load():
        for m in p["modules"]:
            owner.setdefault(m["slug"], p["id"])
    return owner


def conflicts(projects: list[dict] | None = None) -> dict[str, list[str]]:
    """{slug: [project ids]} for every slug claimed more than once."""
    seen: dict[str, list[str]] = {}
    for p in projects if projects is not None else load():
        for m in p["modules"]:
            seen.setdefault(m["slug"], []).append(p["id"])
    return {s: ids for s, ids in seen.items() if len(ids) > 1}


def unfiled(projects: list[dict] | None = None) -> list[str]:
    """Productions on disk that no project claims.

    Not an error — a new video is unfiled until you file it. It is surfaced so
    that "not in a project yet" and "not on disk" stay different facts.
    """
    projects = projects if projects is not None else load()
    return sorted(_on_disk() - set(owner_of(projects)))


def grouped() -> list[dict]:
    """Declared projects, plus a synthetic `unfiled` project when anything is
    left over. This is the canvas's whole view of the world.
    """
    projects = load()
    loose = unfiled(projects)
    if loose:
        # Always last: an unfiled slug is a question, not the headline.
        projects.append({
            "id": UNFILED, "title": "Unfiled", "order": 99, "outline": None,
            "modules": [{"slug": s, "missing": False} for s in loose]})
    return projects
