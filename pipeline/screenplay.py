"""Screenplay node: the vault script compiles into the shot list.

The script is where the words are written; the shot list is where the beats
live; until now a person carried every line from one to the other by hand. This
module is the arrow between them, and it is a VERB, not a doit task: it edits
`shotlist.json`, the one hand-authored file per video, under the same rule
`pool.commit` already follows — a tool may edit the authored file in place, by
beat id, and must never touch what it does not own.

WHAT THE SCRIPT OWNS. For every beat it names: the narration (`vo_text`), the
order, and the `brief` (the VIDEO direction, kept as prose). Nothing else. An
existing beat's visual recipe — scene, plate, seeds, motion, lock — survives a
recompile untouched, so rewording a line re-times its picture and re-renders
nothing visual.

WHAT THE SCRIPT DOES NOT OWN. Silent beats it never mentions — TOC cards,
dividers, cold-open shots — are furniture. They keep their place after the
beat they followed. A narrated beat the script no longer names is reported as
an ORPHAN and kept; dropping it is a decision, and this tool does not make it.

TWO DIALECTS, one shape. Logic's REVAMP screenplays alternate `**NARRATOR:**`
and `**VIDEO:**` blocks; Linear Algebra's beat sheets put the id and brief on
one heading line, `**c00a** — *brief*`, with the narration beneath. Both parse
to the same blocks. Screenplays carry no ids until `adopt` writes them in as
`**NARRATOR [r07]:**` — matched to the existing shot list by narration text,
which on 2026-09-01 matched exactly for every narrated beat of three slugs.

IDS ARE AUTHORED, NEVER DERIVED. A content-hash id would orphan a render on
every rewording. A marked block keeps its id and its recipe through any edit;
an unmarked block is a new beat, and it gets the next free id in the dominant
series. That is the 0.9.0 rule ("beat ids change when narration changes")
made mechanical rather than remembered.
"""
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

from . import beats, config

# --- dialect A: beat sheet -----------------------------------------------
#   **c00a** — *animation: `HaveYouWondered`. Black, then a form resolving.*
#   narration paragraphs follow until the next heading.
HEAD_A = re.compile(r"^\*\*(?P<id>[A-Za-z][\w-]*)\*\*\s*[—–-]+\s*\*(?P<brief>.*?)\*\s*$")

# --- dialect B: screenplay -----------------------------------------------
#   **NARRATOR:**            or       **NARRATOR [r07]:**
#   narration paragraph(s)
#   **VIDEO:** the picture, possibly wrapped over several lines
NARR_B = re.compile(r"^\*\*NARRATOR(?:\s*\[(?P<id>[\w-]+)\])?:\*\*\s*$")
VIDEO_B = re.compile(r"^\*\*VIDEO(?:\s*\[(?P<id>[\w-]+)\])?:\*\*\s*(?P<brief>.*)$")

SECTION = re.compile(r"^#{1,6}\s+(?P<title>.*?)\s*$")
RULE = re.compile(r"^-{3,}\s*$")
ID_SERIES = re.compile(r"^(?P<prefix>[a-z]+)(?P<num>\d+)(?P<tail>[a-z]?)$")

DIALECTS = ("beats", "screenplay")


# ------------------------------------------------------------------ parsing

def _collapse(lines: list[str]) -> str:
    """Hard-wrapped prose becomes one line. Markup is kept — the shot list
    carries `*made of*` verbatim and the narrator reads the emphasis."""
    return re.sub(r"\s+", " ", " ".join(l.strip() for l in lines if l.strip())).strip()


def dialect_of(text: str) -> str:
    for line in text.splitlines():
        if HEAD_A.match(line):
            return "beats"
        if NARR_B.match(line) or VIDEO_B.match(line):
            return "screenplay"
    raise ValueError("not a screenplay: no `**NARRATOR:**` block and no "
                     "`**id** — *brief*` heading found")


def parse(text: str) -> dict:
    """{"dialect", "blocks": [{id, narration, brief, section, line}]}.

    Splits ONLY on the block markers, headings and rules. A parser that is
    clever about paragraph boundaries moves beats; this one is not clever.
    """
    d = dialect_of(text)
    blocks, section = [], ""
    cur = None          # the block collecting narration lines
    video = None        # (block, lines) while a VIDEO direction is wrapping

    def close():
        nonlocal cur, video
        if video is not None:
            video[0]["brief"] = _collapse(video[1])
            video = None
        if cur is not None:
            cur["narration"] = _collapse(cur["_lines"])
            del cur["_lines"]
            cur = None

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\n")
        m = SECTION.match(line)
        if m:
            close()
            section = m.group("title")
            continue
        if RULE.match(line):
            close()
            continue

        if d == "beats":
            m = HEAD_A.match(line)
            if m:
                close()
                cur = {"id": m.group("id"), "brief": m.group("brief").strip(),
                       "section": section, "line": n, "_lines": []}
                blocks.append(cur)
            elif cur is not None:
                cur["_lines"].append(line)
            continue

        # screenplay
        m = NARR_B.match(line)
        if m:
            close()
            cur = {"id": m.group("id"), "brief": "", "section": section,
                   "line": n, "_lines": []}
            blocks.append(cur)
            continue
        m = VIDEO_B.match(line)
        if m:
            if video is not None:
                video[0]["brief"] = _collapse(video[1])
                video = None
            if cur is not None:
                cur["narration"] = _collapse(cur["_lines"])
                del cur["_lines"]
                target, cur = cur, None
            else:
                # A picture with no words before it: a silent beat.
                target = {"id": m.group("id"), "brief": "", "narration": "",
                          "section": section, "line": n}
                blocks.append(target)
            if m.group("id") and not target.get("id"):
                target["id"] = m.group("id")
            video = (target, [m.group("brief")])
            continue
        if video is not None:
            if line.strip():
                video[1].append(line)
            else:
                video[0]["brief"] = _collapse(video[1])
                video = None
            continue
        if cur is not None:
            cur["_lines"].append(line)

    close()
    return {"dialect": d, "blocks": blocks}


# ------------------------------------------------------------- the script

def script_path(slug: str, override: str | None = None) -> Path:
    """The vault file a slug compiles from: `script` in the shot list,
    vault-relative or absolute. The binding lives beside the content it
    drives, not in the registry — the registry chooses HOW, never WHAT."""
    if override:
        return Path(override).expanduser()
    rel = beats.load(slug).get("script")
    if not rel:
        raise FileNotFoundError(
            f"{slug}: shotlist.json has no `script` field — set it to the vault-"
            f"relative path of the screenplay (under {config.VAULT_ROOT})")
    p = Path(rel).expanduser()
    return p if p.is_absolute() else config.VAULT_ROOT / p


# ------------------------------------------------------------------ adopt

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[*_`]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _series(doc: dict) -> tuple[str, int]:
    """The dominant id prefix among narrated beats and its zero-pad width."""
    counts, width = {}, 2
    for b in doc.get("beats", []):
        m = ID_SERIES.match(b["id"])
        if m and beats.narration_text(b).strip():
            counts[m.group("prefix")] = counts.get(m.group("prefix"), 0) + 1
            width = max(width, len(m.group("num")))
    prefix = max(counts, key=counts.get) if counts else "r"
    return prefix, width


def _next_id(taken: set[str], prefix: str, width: int) -> str:
    n = 1
    for t in taken:
        m = ID_SERIES.match(t)
        if m and m.group("prefix") == prefix:
            n = max(n, int(m.group("num")) + 1)
    return f"{prefix}{n:0{width}d}"


def adopt(slug: str, script: str | None = None, *, write: bool = True) -> dict:
    """Write id markers into a marker-less screenplay.

    Narrated blocks match existing beats by normalised narration text, in
    order, each beat claimed once. Unmatched narrated blocks get fresh ids.
    Silent VIDEO-only blocks ALWAYS get a fresh id: they cannot be matched by
    text, and guessing by position is how a cold-open shot ends up wearing a
    TOC card's id. They are reported separately so a person can re-key one to
    an existing silent beat by hand where that is what it is.
    """
    path = script_path(slug, script)
    text = path.read_text()
    parsed = parse(text)
    doc = beats.load(slug)
    if parsed["dialect"] == "beats":
        return {"dialect": "beats", "written": False, "note": "beat sheets carry ids already"}

    by_text: dict[str, list[str]] = {}
    for b in doc.get("beats", []):
        t = _norm(beats.narration_text(b))
        if t:
            by_text.setdefault(t, []).append(b["id"])
    taken = {b["id"] for b in doc.get("beats", [])}
    prefix, width = _series(doc)

    matched, fresh, silent = [], [], []
    marks: dict[int, str] = {}       # line number -> id
    for blk in parsed["blocks"]:
        if blk.get("id"):
            continue
        if not blk["narration"]:
            bid = _next_id(taken, prefix, width)
            silent.append(bid)
            taken.add(bid)
            marks[blk["line"]] = bid
            continue
        ids = by_text.get(_norm(blk["narration"]))
        if ids:
            bid = ids.pop(0)
            matched.append(bid)
        else:
            bid = _next_id(taken, prefix, width)
            fresh.append(bid)
        taken.add(bid)
        marks[blk["line"]] = bid

    lines = text.splitlines(keepends=True)
    for n, bid in marks.items():
        lines[n - 1] = re.sub(r"^\*\*(NARRATOR|VIDEO):\*\*", rf"**\1 [{bid}]:**", lines[n - 1])
    out = "".join(lines)
    changed = out != text
    if write and changed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(out)
        shutil.move(str(tmp), str(path))
    return {"dialect": "screenplay", "script": str(path), "matched": matched,
            "fresh": fresh, "silent_fresh": silent,
            "unclaimed": sorted(i for ids in by_text.values() for i in ids),
            "written": bool(write and changed)}


# ---------------------------------------------------------------- compile

def _set_narration(beat: dict, text: str) -> None:
    stems_map = beat.get("stems") or {}
    if "narration" in stems_map:
        stems_map["narration"]["text"] = text
    else:
        beat["vo_text"] = text


def compile(slug: str, script: str | None = None, *, write: bool = True) -> dict:
    """Merge the script into the shot list. Returns the report either way;
    `write=False` is the diff."""
    path = script_path(slug, script)
    parsed = parse(path.read_text())
    doc = beats.load(slug)
    old = doc.get("beats", [])
    by_id = {b["id"]: b for b in old}

    unmarked = [b["line"] for b in parsed["blocks"] if not b.get("id")]
    if unmarked:
        raise ValueError(
            f"{path.name}: {len(unmarked)} block(s) carry no id (first at line "
            f"{unmarked[0]}). Run `adopt` first — ids are authored, never derived.")

    scripted = [b["id"] for b in parsed["blocks"]]
    dup = {i for i in scripted if scripted.count(i) > 1}
    if dup:
        raise ValueError(f"{path.name}: duplicate beat id(s) {sorted(dup)}")

    added, changed = [], []
    result: list[dict] = []
    for blk in parsed["blocks"]:
        beat = by_id.get(blk["id"])
        if beat is None:
            beat = {"id": blk["id"], "vo_text": blk["narration"],
                    "visual": {"kind": "brief"}}
            if blk["brief"]:
                beat["brief"] = blk["brief"]
            added.append(blk["id"])
        else:
            before = json.dumps(beat, sort_keys=True)
            if beats.narration_text(beat) != blk["narration"]:
                _set_narration(beat, blk["narration"])
            if blk["brief"] and beat.get("brief") != blk["brief"]:
                beat["brief"] = blk["brief"]
            if json.dumps(beat, sort_keys=True) != before:
                changed.append(blk["id"])
        result.append(beat)

    # Furniture and orphans: anything the script did not name, re-seated after
    # the nearest preceding beat that is still in the cut.
    scripted_set = set(scripted)
    orphaned = []
    anchor_of: dict[str | None, list[dict]] = {}
    last_anchor = None
    for b in old:
        if b["id"] in scripted_set:
            last_anchor = b["id"]
        else:
            anchor_of.setdefault(last_anchor, []).append(b)
            if beats.narration_text(b).strip():
                orphaned.append(b["id"])
    seated: list[dict] = list(anchor_of.get(None, []))
    for b in result:
        seated.append(b)
        seated.extend(anchor_of.get(b["id"], []))

    old_order = [b["id"] for b in old]
    new_order = [b["id"] for b in seated]
    moved = old_order != [i for i in new_order if i in set(old_order)]

    doc["beats"] = seated
    written = False
    if write and (added or changed or moved):
        _save(slug, doc)
        written = True
    return {"slug": slug, "script": str(path), "dialect": parsed["dialect"],
            "added": added, "changed": changed, "orphaned": orphaned,
            "reordered": moved, "beats": len(seated), "written": written}


def diff(slug: str, script: str | None = None) -> dict:
    return compile(slug, script, write=False)


def _save(slug: str, doc: dict) -> None:
    """Through a temp file, like pool.commit: a half-written shot list is the
    one file here that cannot be regenerated from anything else."""
    path = config.video_dir(slug) / "shotlist.json"
    tmp = path.with_suffix(".json.tmp")
    # ensure_ascii=False: this file is read and edited by a person, and an
    # em-dash that round-trips as \u2014 is a diff nobody can review.
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    shutil.move(str(tmp), str(path))


# -------------------------------------------------------------------- CLI

def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("diff", "compile", "adopt"):
        print("usage: python -m pipeline.screenplay diff|compile|adopt <slug> "
              "[--script PATH] [--dry-run]", file=sys.stderr)
        return 2
    verb, slug = argv[0], argv[1]
    script = None
    if "--script" in argv:
        script = argv[argv.index("--script") + 1]
    dry = "--dry-run" in argv
    if verb == "diff":
        out = diff(slug, script)
    elif verb == "compile":
        out = compile(slug, script, write=not dry)
    else:
        out = adopt(slug, script, write=not dry)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
