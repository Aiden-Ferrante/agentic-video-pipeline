"""Publish node: everything YouTube needs, except the upload.

The upload is deliberately NOT automated. YouTube's Data API requires OAuth —
there is no keyless path, so this is the one node where the local-first rule
simply cannot be met — and self-hosted YouTube tokens are a well-known source
of silent breakage. Pressing publish is also the last point at which a human
looks at the thing before the world does, which is worth keeping.

So this emits `out/publish.md`: title, description, chapters, credits, and the
list of files to attach. Copy it into the upload form.

Chapters come from the timeline, not from the shot list, because a reorder or a
trim moves every timestamp after it. Beats are grouped into chapters by a
`chapter` field; beats without one continue the chapter before them.

Descriptions can be drafted by the local model — Ollama, keyless — but a draft
is a suggestion. `"description"` in the shot list always wins.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request

from . import assemble, beats, config, library, sound, thumbnail

# This box runs more than one Ollama, and they do not hold the same models, so
# both the host and the model are DISCOVERED rather than hardcoded. A drafting
# step that dies because a model was renamed is worse than no drafting step.
HOSTS = [h for h in os.environ.get(
    "OLLAMA_HOSTS", "http://127.0.0.1:11435,http://127.0.0.1:11434").split(",") if h]
# Rough order of usefulness for prose. Matched as a prefix, so a tag suffix
# (":latest", ":q8_0") does not have to be spelled out.
PREFERRED = ("gemma4", "glm-4.7-flash", "qwen3:30b", "llama3.1:70b", "llama3.1:8b",
             "nemotron", "qwen2.5")

# YouTube requires the first chapter to start at 00:00, and at least three
# chapters, or it shows none at all.
MIN_CHAPTERS = 3


def publish_path(slug: str):
    return config.video_dir(slug) / "out" / "publish.md"


def _stamp(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def chapters(slug: str) -> list[tuple[str, float]]:
    """(title, start) per chapter, timed against the finished master."""
    titles = {b["id"]: b.get("chapter") for b in beats.load(slug).get("beats", [])}
    out = []
    for beat, start, _ in assemble.beat_spans(slug):
        title = titles.get(beat)
        if title and (not out or out[-1][0] != title):
            out.append((title, start))
    # YouTube ignores the whole list unless the first mark is at zero.
    if out and out[0][1] > 0:
        out.insert(0, ("Introduction", 0.0))
    return out


def _pick_model() -> tuple[str, str] | None:
    """(host, model) for the best local model actually pulled, or None."""
    for host in HOSTS:
        try:
            with urllib.request.urlopen(f"{host}/api/tags", timeout=8) as fh:
                names = [m["name"] for m in json.load(fh).get("models", [])]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        for want in PREFERRED:
            for name in names:
                if name.startswith(want):
                    return host, name
        if names:
            return host, names[0]
    return None


def draft_description(slug: str, timeout: int = 300) -> str:
    """Ask the local model for a description. Optional, and never authoritative."""
    picked = _pick_model()
    if not picked:
        print(f"  [note] no local model reachable on {', '.join(HOSTS)} — "
              f"write the description by hand.")
        return ""
    host, model = picked

    doc = beats.load(slug)
    script = "\n".join(beats.narration_text(b) for b in doc.get("beats", []))
    prompt = (
        "Write a YouTube description for an educational maths video. Two short "
        "paragraphs, plain and specific, no hype, no emoji, no hashtags, no "
        "call to action. Describe what the viewer will understand by the end.\n\n"
        f"Title: {doc.get('title', slug)}\n\nNarration:\n{script}\n")
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False,
                          "think": False}).encode()
    request = urllib.request.Request(
        f"{host}/api/generate", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as fh:
            text = json.load(fh).get("response", "").strip()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  [note] no description drafted ({type(exc).__name__}) from "
              f"{model} on {host}. Write one by hand.")
        return ""
    print(f"  drafted by {model} on {host} — read it before you post it")
    return text


def build(slug: str, draft: bool = True) -> bool:
    doc = beats.load(slug)
    master = assemble.master_path(slug)
    if not master.exists():
        print(f"[{slug}] no master yet")
        return False

    title = doc.get("title", slug)
    description = doc.get("description") or (draft_description(slug) if draft else "")
    marks = chapters(slug)
    credits = library.credits(config.video_dir(slug))
    # A bed lives in the shared library, outside this video's folder, so its
    # credit has to be collected separately or it silently goes unattributed.
    bed = sound.bed_path(slug)
    if bed.exists():
        line = library.attribution_line(library.read(bed) or {})
        if line and line not in credits:
            credits = "\n".join(filter(None, [credits, line]))

    body = [f"# {title}", "",
            f"*{assemble.duration(master):.0f}s — {len(doc.get('beats', []))} beats*", "",
            "## Description", "", description or "_none written yet_", ""]

    if len(marks) >= MIN_CHAPTERS:
        body += ["## Chapters", "",
                 *[f"{_stamp(at)} {name}" for name, at in marks], ""]
    elif marks:
        body += ["## Chapters", "",
                 f"_only {len(marks)} — YouTube needs at least {MIN_CHAPTERS} and the "
                 f"first at 00:00, so these were left out. Add `\"chapter\"` to more "
                 f"beats._", ""]

    body += ["## Credits", "", credits or "_nothing is owed — every asset is CC0 "
             "or public domain._", "", "## Files", ""]
    body.append(f"- master: `{master.relative_to(config.REPO)}`")
    for extra, label in (("srt", "subtitles"), ("ass", "burn-in subtitles")):
        path = config.video_dir(slug) / "out" / f"{slug}.{extra}"
        if path.exists():
            body.append(f"- {label}: `{path.relative_to(config.REPO)}`")
    thumb = thumbnail.thumbnail_path(slug)
    if thumb.exists():
        body.append(f"- thumbnail: `{thumb.relative_to(config.REPO)}`")
    for short in sorted((config.video_dir(slug) / "out" / "shorts").glob("*.mp4")):
        body.append(f"- short: `{short.relative_to(config.REPO)}`")

    body += ["", "---", "",
             "Upload by hand. YouTube's Data API needs OAuth, so there is no keyless "
             "path, and this is the last point where a person looks at the video "
             "before the world does."]

    out = publish_path(slug)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(body) + "\n")
    print(f"[{slug}] publish notes: {len(marks)} chapters, "
          f"{'credits owed' if credits else 'no credits owed'} -> "
          f"{out.relative_to(config.REPO)}")
    return True
