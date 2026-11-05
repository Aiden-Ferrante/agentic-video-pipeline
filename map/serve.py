#!/usr/bin/env python3
"""Status and build server for the pipeline canvas. Zero dependencies.

Serves the map page, a status endpoint derived from `doit list --status`, and
two doors: `/api/open` launches an application, `/api/run` builds a node.

    python3 map/serve.py [port]        default 8831

ON BUILDING FROM THE UI — this reverses an earlier decision, deliberately.
MAP.md recorded "no execution logic in the UI", aimed at Dagster: a node graph
with click-to-rematerialize for 151 MB, 72 packages and 6 daemons. That
rejection stands and nothing here adopts an orchestrator. What changed is the
reading of the rule: `/api/open` already spawned processes, so the principle was
never "the UI must not run things" — it was "the UI must not become a second
product with its own state". `/api/run` shells out to the same `doit` the CLI
uses, holds no build state of its own beyond the handle on a running process,
and knows nothing the filesystem does not. Kill this server mid-build and the
build finishes anyway.

The security model is the allowlist, unchanged: the client sends A NODE KEY AND
A SLUG, never a path and never a command. Every task name is derived here from a
fixed table, so the page cannot ask the box to run something of its choosing.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DOIT = REPO / ".venv" / "bin" / "doit"

# The one definition of "which project owns this slug", shared with the build
# side. `pipeline.projects` is stdlib-only precisely so this zero-dependency
# server can import it without dragging in doit or otio; keep it that way.
sys.path.insert(0, str(REPO))
from pipeline import config as config_mod  # noqa: E402
from pipeline import styles as styles_mod  # noqa: E402
from pipeline import projects as projects_mod  # noqa: E402

# The fleet's shared tokens, served at /fleet.css so the page can link them.
# A sibling checkout is not a guarantee: a missing file costs the palette and
# nothing else — same call content-manager makes when it inlines this file.
FLEET_CSS = Path(os.environ.get(
    "FLEET_UI_CSS", Path.home() / "Desktop/code/fleet-ui/fleet.css"))

# Which canvas node each doit task group reports on. A group may light up more
# than one node: `master` covers both the assembly step and its output.
# Every doit group maps somewhere. Until 2026-08-25 only four groups were
# listed, so imagery, voice, sound, shorts, thumbnail and publish sat on the
# canvas with no badge — permanently "no state", indistinguishable from
# unwired. `align` reports under voice (it is the recording's timings) and
# `fit` under timeline (it is picture yielding to narration); `captions` under
# publish, where the files are actually delivered.
NODES_FOR_GROUP = {
    "beats": ["shotlist"],
    "animation": ["animation"],
    "imagery": ["imagery"],
    # Brief cards: the placeholder the script compiler emits for a beat whose
    # picture is still prose. Its own node, because a stale card is a hole
    # someone should fill, and hiding it under shotlist would say "fresh".
    "brief": ["brief"],
    "narration": ["narration"],
    "align": ["narration"],
    "fit": ["timeline"],
    "timeline": ["timeline"],
    "music": ["music"],
    # Captions grew their own node with the stems pass — they were filed under
    # publish, which made a stale caption read as a stale upload.
    "captions": ["captions"],
    "master": ["master"],
    "short": ["shorts"],
    "thumbnail": ["thumbnail"],
    "publish": ["publish"],
    "program": ["program"],
}

# doit status letters: U = up to date, R = run needed, I = ignored.
LABEL = {"U": "fresh", "R": "stale", "I": "ignored"}


# doit keeps its state in a gdbm file and holds an EXCLUSIVE lock on it for the
# whole run, so `list --status` during a build does not return stale data — it
# dies. It dies by exiting non-zero with a traceback on stderr rather than
# raising here, which means an empty stdout is ambiguous: a locked database and
# an unconfigured one look identical. Matched on errno text because that is what
# gdbm gives us; the surrounding traceback moves between Python versions.
LOCKED = "Resource temporarily unavailable"


def slugs() -> list[str]:
    """Every production, straight off the filesystem.

    Deliberately NOT derived from the doit task list, which is empty whenever a
    build holds the lock — the page would then have no video to name at exactly
    the moment you want to press a button. A shot list on disk is a production;
    that is the same rule `config.slugs()` uses, and it never goes blank.
    """
    return sorted(p.parent.name for p in (REPO / "videos").glob("*/shotlist.json"))


def doit_tasks() -> tuple[dict, str]:
    """({task_name: status_letter}, health) for sub-tasks (group headers lack ':').

    health is "ok", "busy" — a build holds the lock — or "down". Telling the
    middle one apart is the whole point: a build is precisely when someone looks
    at this page, and an empty graph then reads as "nothing is wired" when the
    truth is "everything is, and it is running right now".
    """
    if not DOIT.exists():
        return {}, "down"
    # While a build THIS server started is running, do not touch doit at all.
    # `doit list` holds the gdbm lock for the length of the listing (~1.4s on
    # this graph), and a status poll landing during the child's startup — or a
    # child spawned during a poll — dies on that lock with a traceback. Found
    # by pressing a rebuild button, not by curling: the button's build failed
    # exit 3 about one time in three, timed against the 5s poll.
    try:
        with _DOIT_LOCK:
            # Re-checked INSIDE the lock, not before it. A poll that queued on
            # the lock while a build was being spawned would otherwise run its
            # listing the instant the spawn releases — and beat the child, which
            # needs about a second of interpreter startup, to the database.
            with _RUN_LOCK:
                proc = _RUN["proc"]
            if proc is not None and proc.poll() is None:
                return {}, "busy"
            # One listing serves every poller in the window. The listing takes
            # ~1.4s on this graph and holds the lock for all of it; without the
            # cache, this page plus content-manager plus anything else polling
            # forms a convoy where each 5s tick runs N sequential listings and
            # a build request queues behind all of them — measured at 16.9s to
            # answer a POST that does two seconds of work.
            if time.time() - _LIST_CACHE["t"] < 2.5:
                return _LIST_CACHE["out"]
            r = subprocess.run([str(DOIT), "list", "--all", "--status"],
                               cwd=REPO, capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return {}, "down"
    out = {}
    for line in r.stdout.splitlines():
        m = re.match(r"^(\S)\s+(\S+)", line)
        if m and ":" in m.group(2):
            out[m.group(2)] = m.group(1)
    result = (out, "ok") if out else ({}, "busy" if LOCKED in r.stderr else "down")
    # Only a real answer is worth caching; a locked or broken read must be
    # retried at the next tick, not remembered.
    if result[1] == "ok":
        _LIST_CACHE.update({"t": time.time(), "out": result})
    return result


def rollup(videos: dict) -> list[dict]:
    """Per-project freshness, folded up from the per-slug task states.

    A project is stale if ANY of its modules is, and the count is carried
    alongside so "one beat needs a re-render" and "nothing has ever been built"
    do not read the same. That distinction is the reason this exists: the
    stage-centric view can tell you `animation:axioms/b03` is stale, but it
    cannot tell you how the Logic episode is doing, because until now nothing
    knew the five slugs were one work.

    A module with no tasks at all is `none`, not `fresh`. An empty graph must
    never render as green — that was v1's failure mode, showing calm where it
    had no information.
    """
    out = []
    for proj in projects_mod.grouped():
        mods, fresh, stale = [], 0, 0
        for m in proj["modules"]:
            tasks = videos.get(m["slug"], [])
            f = sum(1 for t in tasks if t["state"] == "fresh")
            st = sum(1 for t in tasks if t["state"] == "stale")
            fresh += f
            stale += st
            mods.append({**m, "fresh": f, "stale": st,
                         "state": ("missing" if m.get("missing") else
                                   "stale" if st else "fresh" if f else "none")})
        out.append({**proj, "modules": mods, "fresh": fresh, "stale": stale,
                    "state": ("stale" if stale else "fresh" if fresh else "none")})
    return out


# The last graph we managed to read, served while a build holds the lock. A
# canvas that empties itself the moment you press a button is worse than no
# canvas — the graph you were reading is exactly what you want to keep looking
# at while the thing you started runs.
_LAST: dict = {}


def snapshot() -> dict:
    tasks, health = doit_tasks()

    if health == "busy" and _LAST:
        # Deliberately keep the previous nodes/videos and only move the clock:
        # the states are a moment stale, which is honest and useful, whereas
        # blanking the page loses information the reader already had.
        # slugs come off disk, so they stay correct even here — only the
        # freshness states are a moment old.
        return {**_LAST, "generated": time.time(), "health": "busy",
                "slugs": slugs(),
                # Project membership is a file on disk, not a build result, so
                # it stays correct during a build even though the freshness
                # letters carried in _LAST are a moment stale.
                "projects": rollup(_LAST.get("videos", {})),
                "conflicts": projects_mod.conflicts()}

    nodes, videos = {}, {}

    for name, letter in sorted(tasks.items()):
        group, _, rest = name.partition(":")
        slug = rest.split("/")[0]
        state = LABEL.get(letter, letter)
        videos.setdefault(slug, []).append({"task": name, "state": state})
        for node in NODES_FOR_GROUP.get(group, []):
            n = nodes.setdefault(node, {"fresh": 0, "stale": 0, "detail": []})
            n["fresh" if state == "fresh" else "stale"] += 1
            n["detail"].append({"task": name, "state": state})

    for n in nodes.values():
        n["state"] = "stale" if n["stale"] else ("fresh" if n["fresh"] else "none")

    projects = rollup(videos)

    snap = {"generated": time.time(), "wired": bool(tasks), "health": health,
            "nodes": nodes, "videos": videos, "slugs": slugs(),
            "projects": projects, "conflicts": projects_mod.conflicts()}
    if health == "ok":
        _LAST.clear()
        _LAST.update(snap)
    return snap


def _authoritative(slug: str) -> tuple[Path, bool]:
    """(path, promoted) — the same rule as pipeline/timeline.py, replicated in
    three lines rather than imported, because importing pipeline.timeline pulls
    in the otio package and this server is deliberately zero-dependency."""
    out = REPO / "videos" / slug / "out"
    edit = out / f"{slug}.edit.otio"
    return (edit, True) if edit.exists() else (out / f"{slug}.otio", False)


def _clips(doc: dict, kind: str) -> list[dict]:
    """The clips of the first track of `kind`, off the raw OTIO JSON. A .otio
    IS JSON, so nothing beyond the stdlib is needed to read durations — only
    to do timeline arithmetic, which this server never does."""
    for track in doc.get("tracks", {}).get("children", []):
        if track.get("kind") != kind:
            continue
        out = []
        for c in track.get("children", []):
            dur = c.get("source_range", {}).get("duration", {})
            rate, value = dur.get("rate") or 30.0, dur.get("value") or 0.0
            item = {"name": c.get("name") or "", "schema": c.get("OTIO_SCHEMA", ""),
                    "seconds": round(value / rate, 3),
                    "beat": c.get("metadata", {}).get("pipeline", {}).get("beat")}
            refs = c.get("media_references") or {}
            active = refs.get(c.get("active_media_reference_key") or "", {})
            url = active.get("target_url") or ""
            if url.startswith("file://"):
                item["media"] = url[7:]
            out.append(item)
        return out
    return []


def video_detail(slug: str) -> dict:
    """Everything the beat strip needs for one module, in one read.

    Beats come from the SHOT LIST (content: what should exist) and clips from
    the AUTHORITATIVE timeline (time: what the cut actually is) — merged, not
    substituted, because a beat authored but never rendered must show as a hole
    rather than vanish. Task states ride along from the last doit read; they
    may be a few seconds old, which is what `generated` is for.
    """
    sl_path = REPO / "videos" / slug / "shotlist.json"
    try:
        sl = json.loads(sl_path.read_text())
    except (OSError, ValueError):
        return {"slug": slug, "exists": False}

    tl_path, promoted = _authoritative(slug)
    clips = []
    if tl_path.exists():
        try:
            clips = _clips(json.loads(tl_path.read_text()), "Video")
        except (OSError, ValueError):
            clips = []
    by_beat = {c["beat"] or c["name"]: c for c in clips
               if c["schema"].startswith("Clip")}

    # Per-beat task states out of the last snapshot: task names look like
    # `animation:slug/b03`, so this is a suffix match per beat.
    states = {}
    for t in _LAST.get("videos", {}).get(slug, []):
        name = t["task"]
        if "/" in name:
            group = name.split(":")[0]
            beat = name.rsplit("/", 1)[1]
            states.setdefault(beat, {})[group] = t["state"]

    beats = []
    for b in sl.get("beats", []):
        bid = b.get("id")
        clip = by_beat.get(bid)
        visual = b.get("visual") or {}
        beats.append({
            "id": bid,
            "vo_text": b.get("vo_text", ""),
            "brief": b.get("brief", ""),
            "section": b.get("section", ""),
            "kind": visual.get("kind", "animation"),
            "locked": bool(visual.get("lock")),
            "seconds": clip["seconds"] if clip else None,
            "placed": clip is not None,
            "tasks": states.get(bid, {}),
            "stale": any(v == "stale" for v in states.get(bid, {}).values()),
        })

    total = round(sum(b["seconds"] or 0 for b in beats), 3)
    return {"slug": slug, "exists": True, "title": sl.get("title", slug),
            "promoted": promoted, "timeline": tl_path.name if tl_path.exists() else None,
            "seconds": total, "beats": beats, "script": script_binding(sl),
            "style": styles_mod.identity(slug),
            "briefs": sum(1 for b in beats if b["kind"] == "brief"),
            "generated": time.time()}


def script_binding(sl: dict) -> dict:
    """Which vault screenplay a shot list compiles from, and whether it is
    there. The path is the shot list's own `script` field, resolved the way
    pipeline/screenplay.py resolves it, so the page and the compiler agree."""
    rel = sl.get("script")
    if not rel:
        return {"bound": False}
    path = Path(rel).expanduser()
    if not path.is_absolute():
        path = config_mod.VAULT_ROOT / path
    return {"bound": True, "path": rel, "exists": path.exists()}


def script_report(slug: str) -> dict:
    """What compiling would change — the compiler's own diff, never a second
    implementation of its rules. Read-only: `write=False` is the whole
    difference between this and /api/compile."""
    from pipeline import screenplay
    try:
        return {"ok": True, **screenplay.diff(slug)}
    except FileNotFoundError as e:
        return {"ok": False, "bound": False, "message": str(e)}
    except ValueError as e:
        # Unmarked blocks: the compiler refuses until adopt has run.
        return {"ok": False, "needs_adopt": "adopt" in str(e), "message": str(e)}


def compile_script(slug: str) -> dict:
    """The compile door. Writes shotlist.json — the one file here that cannot
    be regenerated — so the POST handler keeps it loopback-only like /api/run."""
    from pipeline import screenplay
    try:
        rep = screenplay.compile(slug, write=True)
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "message": str(e)}
    _LIST_CACHE["t"] = 0.0   # the shot list moved; the next poll must re-read doit
    if not rep["written"]:
        return {"ok": True, **rep, "message": "already compiled — the shot list matches the script."}
    parts = []
    if rep["added"]:
        parts.append(f"{len(rep['added'])} new beat(s) as brief cards")
    if rep["changed"]:
        parts.append(f"{len(rep['changed'])} beat(s) reworded")
    if rep["reordered"]:
        parts.append("order changed")
    if rep["orphaned"]:
        parts.append(f"{len(rep['orphaned'])} orphan(s) kept")
    return {"ok": True, **rep,
            "message": "compiled: " + ", ".join(parts) + f". Build shot list, then the "
                       f"stale nodes, to see it."}


def project_detail(pid: str) -> dict:
    """The composed work: per-module runtime and the program timeline's state.

    Durations come from each module's AUTHORITATIVE timeline, matching what
    `doit program` composes — so a promoted trim shows up here at the same
    moment it would land in the program.
    """
    proj = next((p for p in projects_mod.load() if p["id"] == pid), None)
    if proj is None:
        return {"id": pid, "exists": False}
    mods = []
    for m in proj["modules"]:
        slug = m["slug"]
        entry = {**m}
        tl_path, promoted = _authoritative(slug)
        entry["promoted"] = promoted
        secs = None
        if tl_path.exists():
            try:
                secs = round(sum(c["seconds"]
                                 for c in _clips(json.loads(tl_path.read_text()),
                                                 "Video")), 3)
            except (OSError, ValueError):
                secs = None
        entry["seconds"] = secs
        mods.append(entry)
    prog = REPO / "out" / "projects" / f"{pid}.otio"
    return {"id": pid, "exists": True, "title": proj["title"],
            "tools": proj.get("tools") or {},
            "modules": mods,
            "seconds": round(sum(m["seconds"] or 0 for m in mods), 3),
            "program": prog.name if prog.exists() else None,
            "program_mtime": prog.stat().st_mtime if prog.exists() else None}


def drift(slug: str) -> dict:
    """Which clips were re-rendered under a promoted edit. On demand only —
    this hashes every referenced file, which is seconds of work, so it must
    never sit on the 5s poll. The hash matches pipeline/library.content_hash
    byte for byte (sha256, algorithm-prefixed); a divergence here would report
    phantom drift, which is worse than reporting none.
    """
    import hashlib
    tl_path, promoted = _authoritative(slug)
    if not promoted:
        return {"slug": slug, "promoted": False, "drifted": [], "checked": 0}
    try:
        doc = json.loads(tl_path.read_text())
    except (OSError, ValueError):
        return {"slug": slug, "promoted": True, "error": "unreadable edit"}
    drifted, checked = [], 0
    for track in doc.get("tracks", {}).get("children", []):
        for c in track.get("children", []):
            meta = c.get("metadata", {}).get("pipeline", {})
            was = meta.get("promoted_hash")
            refs = c.get("media_references") or {}
            url = (refs.get(c.get("active_media_reference_key") or "", {})
                   .get("target_url") or "")
            if not was or not url.startswith("file://"):
                continue
            path = Path(url[7:])
            if not path.exists():
                continue
            checked += 1
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if "sha256:" + h.hexdigest() != was:
                drifted.append(c.get("name") or "")
    return {"slug": slug, "promoted": True, "checked": checked,
            "drifted": drifted}


KDENLIVE = ["flatpak", "run", "--user", "org.kde.kdenlive"]

# Canvas nodes that have a door into an application. Anything not listed here
# simply has no launch path — the allowlist is the whole security model.
OPENABLE = {"timeline", "master", "program"}


def open_action(node: str, slug: str):
    """Resolve (argv, cwd, message, single) for a node's 'open' door.

    argv is None when there is nothing to launch — the message then explains
    why. The client sends a node key and a slug, never a path and never a
    command: every path is derived here, from the slug, against this fixed
    table, so the page cannot ask the box to run something of its choosing.
    """
    out = REPO / "videos" / slug / "out"

    if node == "timeline":
        otio = out / f"{slug}.otio"
        if not otio.exists():
            return None, None, f"No timeline yet — run `doit timeline:{slug}` first.", False
        # Kdenlive cannot take a .otio as an argument (verified on 26.04.3: it
        # imports it as a bin clip instead). Launch with cwd at the file so the
        # import dialog opens in the right place, and state the two clicks.
        return (KDENLIVE, str(otio.parent),
                f"Kdenlive opening. Import with:  Project → Import/Export → "
                f"OpenTimelineIO Project → {otio.name}", True)

    if node == "master":
        mp4 = out / "master.mp4"
        if not mp4.exists():
            return None, None, f"No master yet — run `doit master:{slug}` first.", False
        return (["xdg-open", str(mp4)], str(out),
                f"Opening {mp4.name} in your default player.", False)

    if node == "program":
        # Here `slug` is a PROJECT id — the POST handler validated it against
        # the registry before this table was consulted.
        otio = REPO / "out" / "projects" / f"{slug}.otio"
        if not otio.exists():
            return None, None, f"No program timeline yet — run `doit program:{slug}` first.", False
        return (KDENLIVE, str(otio.parent),
                f"Kdenlive opening. Import with:  Project → Import/Export → "
                f"OpenTimelineIO Project → {otio.name}", True)

    return None, None, f"No application wired for '{node}' yet.", False


# Canvas node -> doit task group. Written out rather than inverted from
# NODES_FOR_GROUP so that "may be built from the page" is an explicit allowlist
# and not an accident of whatever the display table happens to contain. Nodes
# absent here have no build path: `script` and `editor` are human work, `agent`
# edits the timeline through the MCP seam.
RUNNABLE = {
    "shotlist": "beats", "animation": "animation", "imagery": "imagery", "brief": "brief",
    "narration": "narration", "timeline": "timeline", "music": "music",
    "captions": "captions", "master": "master", "shorts": "short",
    "thumbnail": "thumbnail", "publish": "publish", "program": "program",
}

# One run at a time, because doit takes an exclusive lock on its database and a
# second invocation dies on it. The handle is all the state this server keeps.
RUN_LOG = REPO / ".last-run.log"
_RUN = {"task": None, "started": 0.0, "proc": None}
_RUN_LOCK = threading.Lock()
# Serialises this server's own uses of doit's database: a poll's `doit list`
# and a build's spawn must not overlap. Held only for the moment of listing or
# spawning, never for a build's duration — the build signals through _RUN.
_DOIT_LOCK = threading.Lock()
# The last good listing and when it was taken; see doit_tasks for why.
_LIST_CACHE = {"t": 0.0, "out": ({}, "down")}


def _tail(path: Path, lines: int = 12) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def run_state() -> dict:
    """What this server started and how it is going. Never guesses about builds
    it did not launch — those show up as `health: busy` on the status endpoint."""
    with _RUN_LOCK:
        proc, task, started = _RUN["proc"], _RUN["task"], _RUN["started"]
    if proc is None:
        return {"active": False}
    code = proc.poll()
    elapsed = round(time.time() - started, 1)
    if code is None:
        return {"active": True, "task": task, "seconds": elapsed}
    return {"active": False, "task": task, "seconds": elapsed, "exit": code,
            "ok": code == 0, "tail": _tail(RUN_LOG)}


# Nodes whose doit group has per-beat sub-tasks, so a single beat can be
# rebuilt from the strip. Assembly-level groups (timeline, master, program)
# have exactly one task per slug and a beat there is meaningless.
PER_BEAT = {"animation", "imagery", "brief", "narration"}


def start_run(node: str, slug: str, beat: str = "") -> tuple[int, dict]:
    """Build one node — or one beat of it — for one video.

    Refuses rather than queues. A queue would be build state living here instead
    of in doit, which is the line this server does not cross — and a refusal you
    can see beats a job that silently starts twenty minutes later.
    """
    group = RUNNABLE.get(node)
    if not group:
        return 400, {"ok": False,
                     "message": f"'{node}' has no build path — it is hand work."}
    if beat and node not in PER_BEAT:
        return 400, {"ok": False,
                     "message": f"'{node}' builds per video, not per beat."}

    with _RUN_LOCK:
        proc = _RUN["proc"]
        if proc is not None and proc.poll() is None:
            return 409, {"ok": False,
                         "message": f"{_RUN['task']} is still running "
                                    f"({round(time.time() - _RUN['started'])}s)."}

    # Someone else's build — a terminal, a timer — holds the lock. Starting now
    # would spawn a doit that dies on gdbm with a traceback the page cannot read.
    if doit_tasks()[1] == "busy":
        return 409, {"ok": False,
                     "message": "A build started outside this page holds doit's "
                                "database lock. Wait for it to finish."}

    task = f"{group}:{slug}/{beat}" if beat else f"{group}:{slug}"
    try:
        log = RUN_LOG.open("w")
    except OSError as e:
        return 500, {"ok": False, "message": f"cannot open run log: {e}"}
    try:
        # start_new_session so the build outlives this server: a render can run
        # for a quarter of an hour and restarting the canvas must not kill it.
        # Under _DOIT_LOCK so no in-flight `doit list` holds the gdbm lock at
        # the moment the child opens the database. The child then owns the lock
        # for the build; polls see the _RUN handle and stand off entirely.
        with _DOIT_LOCK:
            proc = subprocess.Popen([str(DOIT), task], cwd=REPO, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            # Registered under the SAME lock acquisition as the spawn, so any
            # poll queued behind it wakes up, sees the handle, and stands off
            # for the child's whole startup window.
            with _RUN_LOCK:
                _RUN.update({"task": task, "started": time.time(), "proc": proc})
            _LIST_CACHE["t"] = 0.0   # states are about to change; drop the cache
    except OSError as e:
        log.close()
        return 500, {"ok": False, "message": f"could not start doit: {e}"}
    finally:
        log.close()
    return 200, {"ok": True, "task": task,
                 "message": f"building {task} — watch the node badge."}


def kdenlive_running() -> bool:
    """Kdenlive does not single-instance itself under Flatpak, so without this
    a second click gives you a second editor — and a race over the same file."""
    try:
        r = subprocess.run(["flatpak", "ps", "--columns=application"],
                           capture_output=True, text=True, timeout=10)
        return "org.kde.kdenlive" in r.stdout
    except (subprocess.SubprocessError, OSError):
        return False


def launch(node: str, slug: str) -> dict:
    argv, cwd, message, single = open_action(node, slug)
    if argv is None:
        return {"ok": False, "message": message}
    if single and kdenlive_running():
        return {"ok": True, "message": "Kdenlive is already open — switch to it.\n" + message}
    try:
        subprocess.Popen(argv, cwd=cwd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return {"ok": False, "message": f"could not launch: {e}"}
    return {"ok": True, "message": message}


def health() -> dict:
    """The platform's {ok, checks, freshness} shape, for Mission Control.

    `ok` covers only what this server is itself responsible for: the page it
    serves and the repo it reads. A running build is `status: busy`, NOT
    `ok: false` — a service that reports itself broken while the thing it
    watches is working stops carrying information (same call content-manager
    makes about Postiz being down).
    """
    snap = snapshot()
    checks = {"doit": DOIT.exists(),
              "videos_dir": (REPO / "videos").is_dir(),
              "page": (HERE / "index.html").is_file()}
    newest = None
    for master in (REPO / "videos").glob("*/out/master.mp4"):
        stamp = master.stat().st_mtime
        newest = stamp if newest is None or stamp > newest else newest
    # isoformat(), not strftime("%z") — %z writes the offset as `-0400`, which
    # the fleet's freshness contract rejects. Not because it fails to parse,
    # but because only a hand-rolled format string can produce it, and a
    # hand-rolled formatter is how one payload ends up with two spellings.
    # See mission-control/backend/app/freshness.py.
    freshness = {"checked": datetime.now().astimezone().isoformat(timespec="seconds")}
    if newest:
        freshness["master"] = datetime.fromtimestamp(
            newest).astimezone().isoformat(timespec="seconds")
    return {"ok": all(checks.values()),
            "checks": {**checks, "graph_readable": snap["health"] == "ok"},
            "freshness": freshness,
            "status": "ok" if (all(checks.values()) and snap["health"] == "ok")
                      else ("busy" if snap["health"] == "busy" else "degraded")}


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/status":
            # `run` is what THIS server started; `health: busy` covers any build,
            # including one launched from a terminal. Both are needed: the first
            # gives a task name and an exit code, the second is the honest answer
            # when something else holds the lock.
            self._json(200, {**snapshot(), "run": run_state()})
            return
        if path == "/health":
            self._json(200, health())
            return
        # Read-only detail endpoints, fine over the tailnet like /api/status.
        # The name after the prefix is validated against what exists on disk,
        # same rule as the POST doors — an unknown name is 404, never a path.
        if path.startswith("/api/video/"):
            slug = path[len("/api/video/"):]
            if slug not in slugs():
                self._json(404, {"error": f"unknown video '{slug}'"})
                return
            self._json(200, video_detail(slug))
            return
        if path.startswith("/api/project/"):
            pid = path[len("/api/project/"):]
            self._json(200 if project_detail(pid).get("exists") else 404,
                       project_detail(pid))
            return
        if path.startswith("/api/script/"):
            slug = path[len("/api/script/"):]
            if slug not in slugs():
                self._json(404, {"error": f"unknown video '{slug}'"})
                return
            self._json(200, script_report(slug))
            return
        if path.startswith("/api/drift/"):
            slug = path[len("/api/drift/"):]
            if slug not in slugs():
                self._json(404, {"error": f"unknown video '{slug}'"})
                return
            self._json(200, drift(slug))
            return
        if path == "/fleet.css":
            try:
                body = FLEET_CSS.read_bytes()
            except OSError:
                body = b"/* fleet.css not found - default palette */\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/css")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/api/open", "/api/run", "/api/compile"):
            self._json(404, {"ok": False, "message": "no such endpoint"})
            return
        # All three doors act on this box — two spawn processes, one rewrites
        # the hand-authored shot list — so all are loopback-only. The page and
        # its status are fine to read over the tailnet; changing things is not.
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self._json(403, {"ok": False,
                             "message": "local-only; browse from this machine"})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        node, slug = str(req.get("node", "")), str(req.get("slug", ""))
        # The name is checked against what actually exists on disk, which is
        # what makes traversal impossible: '../../etc' is simply not in either
        # set. For `program` the name is a PROJECT id and is validated against
        # the registry; for everything else it is a video slug.
        if node == "program":
            valid = {p["id"] for p in projects_mod.load()}
        else:
            valid = {p.parent.name for p in (REPO / "videos").glob("*/shotlist.json")}
        if slug not in valid:
            self._json(400, {"ok": False, "message": f"unknown target '{slug}'"})
            return
        beat = str(req.get("beat", ""))
        if beat:
            # Same rule as the slug: validated against what the shot list
            # actually declares, so the task name can only ever be assembled
            # from parts that exist.
            try:
                ids = {b.get("id") for b in json.loads(
                    (REPO / "videos" / slug / "shotlist.json").read_text()
                ).get("beats", [])}
            except (OSError, ValueError):
                ids = set()
            if beat not in ids:
                self._json(400, {"ok": False,
                                 "message": f"unknown beat '{beat}'"})
                return
        if path == "/api/run":
            self._json(*start_run(node, slug, beat))
            return
        if path == "/api/compile":
            self._json(200, compile_script(slug))
            return
        self._json(200, launch(node, slug))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8831
    handler = partial(Handler, directory=str(HERE))
    print(f"pipeline map on http://localhost:{port}  (read-only)")
    # Loopback by default — `tailscale serve --https=8831` fronts it; PIPELINE_MAP_HOST overrides.
    host = os.environ.get("PIPELINE_MAP_HOST", "127.0.0.1")
    ThreadingHTTPServer((host, port), handler).serve_forever()
