"""Imagery node: a beat with visual.kind == "imagery" becomes one clip.

Not a peer of animation. This is period texture — a portrait of Bayes, a page
of Euclid, a NASA plate — for beats where the honest visual is a real
historical artefact rather than a diagram. It is deliberately NOT b-roll.

Two sources, and the order matters for a channel about first principles:

  fetched   the default. Wikimedia Commons, NASA, the Internet Archive and
            Openverse are the only image sources that need no API key, and for
            this subject they are also simply better: you want Euler's actual
            diagram, not a stock photo of a chalkboard.
  generated diffusers, locally. Appropriate for backplates and texture, never
            for anything a viewer might read as evidence.

Rights are captured at FETCH time, into the sidecar, always. Reconstructing
where a picture came from six months later is an archaeology project, and
YouTube's Content ID makes it an expensive one.

Reproducibility: a `query` searches and tells you what it picked; a `title`
pins an exact Commons file. Ship with titles. A search that silently returns
something different next month is a build that cannot be reproduced.
"""
import html
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import beats, config, library

UA = "video-pipeline/0.1 (personal educational channel) python-urllib"
TIMEOUT = 30

# A still has no natural length. It is held for this long, and `fit` then
# stretches or cuts it to the narration like any other clip.
NOMINAL_SECONDS = 5.0

# Commons reports its own licence codes. Map the ones that actually appear to
# SPDX WITH A VERSION — 'CC-BY-4.0', never 'CC-BY', because the version changes
# what is owed. Anything unmapped is kept verbatim and flagged for review.
SPDX = {
    "pd": "CC-PDDC", "cc0": "CC0-1.0", "cc-zero": "CC0-1.0",
    "cc-by-2.0": "CC-BY-2.0", "cc-by-2.5": "CC-BY-2.5",
    "cc-by-3.0": "CC-BY-3.0", "cc-by-4.0": "CC-BY-4.0",
    "cc-by-sa-2.0": "CC-BY-SA-2.0", "cc-by-sa-2.5": "CC-BY-SA-2.5",
    "cc-by-sa-3.0": "CC-BY-SA-3.0", "cc-by-sa-4.0": "CC-BY-SA-4.0",
}


def still_path(slug: str, beat_id: str) -> Path:
    return config.video_dir(slug) / "assets" / f"{beat_id}.still.png"


def _download_path(slug: str, beat_id: str) -> Path:
    """Where the fetched bytes land before normalisation.

    Commons serves whatever the uploader gave it — the Bayes portrait is a GIF,
    others are JPEG or WebP — so the download keeps no extension and is decoded
    to a real PNG afterwards. Trusting the URL's extension is how a GIF ends up
    named .png and every later step guesses wrong.
    """
    return config.video_dir(slug) / "assets" / f"{beat_id}.still.download"


# Commons rate-limits a burst of fetches aggressively, and a video is a burst
# by nature — one build asks for every plate at once. Without this, the Nth beat
# takes an unretried 429 and doit halts the ENTIRE build behind it: no
# alignment, no timeline, no master, and a log whose only clue is one line about
# beat 16. A transient throttle must cost seconds, not the whole run.
RETRIES = 5
BACKOFF = 2.0


def _open(url: str):
    """Fetch a URL, waiting out throttles and transient server errors."""
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(RETRIES):
        try:
            return urllib.request.urlopen(request, timeout=TIMEOUT).read()
        except urllib.error.HTTPError as exc:
            # 429 is the throttle; 5xx is the far end having a moment. Anything
            # else (404, 403) is a real answer and retrying only wastes time.
            if exc.code != 429 and exc.code < 500:
                raise
            if attempt == RETRIES - 1:
                raise
            # Honour Retry-After when the server states a figure, since guessing
            # under it is what earns a longer ban.
            stated = (exc.headers or {}).get("Retry-After")
            delay = float(stated) if (stated or "").strip().isdigit() else BACKOFF * (2 ** attempt)
            print(f"  [warn] HTTP {exc.code} from the image source; "
                  f"retrying in {delay:.0f}s ({attempt + 1}/{RETRIES - 1})")
            time.sleep(delay)
        except urllib.error.URLError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(BACKOFF * (2 ** attempt))
    raise RuntimeError(f"exhausted {RETRIES} attempts fetching {url}")


def _get(url: str) -> dict:
    return json.loads(_open(url))


def _download(url: str, dest: Path) -> None:
    dest.write_bytes(_open(url))


def _plain(markup: str) -> str:
    """Commons returns HTML in its metadata fields; an attribution line is text."""
    return html.unescape(re.sub(r"<[^>]+>", "", markup or "")).strip()


def commons(spec: dict, width: int, original: bool = False) -> tuple[str, dict]:
    """(download_url, rights) from Wikimedia Commons. No API key exists to need.

    `original` returns the file itself rather than a scaled thumbnail: a video
    has no meaningful thumbnail, Commons answers with a poster JPEG, and a
    poster JPEG silently becomes a five-second freeze frame."""
    base = "https://commons.wikimedia.org/w/api.php?action=query&format=json"
    props = f"&prop=imageinfo&iiprop=url|extmetadata|size&iiurlwidth={width}"
    if spec.get("title"):
        title = spec["title"]
        if not title.lower().startswith("file:"):
            title = f"File:{title}"
        url = f"{base}&titles={urllib.parse.quote(title)}{props}"
    else:
        url = (f"{base}&generator=search&gsrnamespace=6&gsrlimit=10"
               f"&gsrsearch={urllib.parse.quote(spec['query'])}{props}")

    pages = list(_get(url).get("query", {}).get("pages", {}).values())
    pages = [p for p in pages if p.get("imageinfo")]
    if not pages:
        raise LookupError(f"Commons found nothing for {spec.get('title') or spec['query']!r}")

    page = pages[min(spec.get("pick", 0), len(pages) - 1)]
    info = page["imageinfo"][0]
    meta = info.get("extmetadata", {})
    code = (meta.get("License", {}).get("value") or "").lower()
    short = meta.get("LicenseShortName", {}).get("value", "")
    spdx = SPDX.get(code)
    if not spdx:
        print(f"  [warn] unmapped Commons licence {code!r} ({short}) — recorded verbatim, "
              f"check what it obliges before publishing")
        spdx = code.upper() or "UNKNOWN"

    if not spec.get("title"):
        print(f"  [note] search picked {page['title']!r}. Pin it as "
              f"\"title\": \"{page['title']}\" so this build stays reproducible.")

    src = info["url"] if original else (info.get("thumburl") or info["url"])
    return src, library.rights(
        spdx,
        title=_plain(meta.get("ObjectName", {}).get("value")) or page["title"],
        creator=_plain(meta.get("Artist", {}).get("value")),
        source_url=info.get("descriptionurl", ""),
        # CC0/PD owe nothing; everything else does, and credits() prints only
        # what is owed so this flag decides whether it appears at all.
        attribution_required=spdx not in ("CC0-1.0", "CC-PDDC"),
        modified=True,
        modification_note="scaled and letterboxed to the project frame",
    )


def nasa(spec: dict, width: int) -> tuple[str, dict]:
    """NASA's image library. Keyless, and public domain by policy."""
    url = ("https://images-api.nasa.gov/search?media_type=image&q="
           + urllib.parse.quote(spec["query"]))
    items = _get(url).get("collection", {}).get("items", [])
    if not items:
        raise LookupError(f"NASA found nothing for {spec['query']!r}")
    item = items[min(spec.get("pick", 0), len(items) - 1)]
    data = item["data"][0]
    assets = _get(item["href"]) if item.get("href") else []
    picks = [a for a in assets if a.endswith((".jpg", ".png"))] if isinstance(assets, list) else []
    if not picks:
        raise LookupError(f"NASA item {data.get('nasa_id')} exposes no still image")

    return picks[0], library.rights(
        "CC-PDDC",
        title=data.get("title", ""), creator=data.get("center", "NASA"),
        source_url=f"https://images.nasa.gov/details/{data.get('nasa_id','')}",
        attribution_required=False, modified=True,
        modification_note="scaled and letterboxed to the project frame",
    )


SOURCES = {"commons": commons, "nasa": nasa}


def _normalise(src: Path, dest: Path) -> bool:
    """Decode one frame of whatever we got into a plain PNG."""
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-i", str(src),
         "-frames:v", "1", "-update", "1", str(dest)],
        capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"  could not decode the fetched image\n{r.stderr[-400:]}")
        return False
    return True


def _generate(slug: str, beat_id: str, spec: dict, dest: Path) -> None:
    """Put a generated still on disk, preferring the take that was chosen.

    Two paths, and the order is the point. If the Gen Lab already produced this
    exact recipe/prompt/seed and a human picked it, that FILE is copied — the
    build then ships the frame that was actually looked at, rather than trusting
    a model to be bit-identical across a ComfyUI or driver upgrade. Only when
    there is no pick does this re-run the model, which is what makes a shot list
    someone else clones still build.

    Replaced the old Stable Diffusion 1.5 bridge on 2026-08-30. That path ran
    diffusers inside VOICE_ROOT — the robot's environment, which config.py
    explicitly warns not to put a modern diffusers into — and at 768x512. The
    seam is now a socket to the imagegen service, so nothing heavy is imported
    in this process at all.
    """
    from . import comfy, pool, recipes

    recipe = spec.get("recipe", "flux2-klein-4b")
    if recipe not in recipes.IMAGE_RECIPES:
        raise ValueError(f"unknown image recipe {recipe!r}; "
                         f"expected one of {sorted(recipes.IMAGE_RECIPES)}")
    prompt, seed = spec["prompt"], int(spec.get("seed", 0))

    picked = pool.selected(slug, "imagery", recipe=recipe, prompt=prompt, seed=seed)
    if picked and picked.exists():
        shutil.copyfile(picked, dest)
        return

    if not comfy.available():
        raise RuntimeError(
            f"beat {beat_id} needs a generated still and ComfyUI is not "
            f"answering on {comfy.HOST} — start the 'imagegen' app")
    graph = recipes.image_graph(recipe, prompt, seed,
                                width=spec.get("width"), height=spec.get("height"))
    sub = comfy.submit(graph, client_id=f"pipeline-{slug}-{beat_id}")
    if "error" in sub:
        raise RuntimeError(f"ComfyUI rejected the graph: {sub['error']}")
    res = comfy.wait(sub["prompt_id"])
    if "error" in res:
        raise RuntimeError(f"ComfyUI failed: {res['error']}")
    if not res.get("files"):
        raise RuntimeError("ComfyUI produced no image")
    dest.write_bytes(comfy.fetch(res["files"][0]))


def _still(slug: str, beat_id: str, spec: dict, width: int) -> dict:
    """Put a normalised PNG on disk and return its rights block."""
    dest = still_path(slug, beat_id)
    dest.parent.mkdir(parents=True, exist_ok=True)

    source = spec.get("source", "commons")
    if source == "file":
        src = Path(spec["path"]).expanduser()
        if not _normalise(src, dest):
            raise RuntimeError(f"could not read {src}")
        return library.rights(spec.get("spdx", "UNKNOWN"),
                              title=src.name, source_url=str(src),
                              attribution_required=bool(spec.get("attribution", True)))
    if source == "generate":
        _generate(slug, beat_id, spec, dest)
        # Output of a model, not a licensed work. Nothing is owed to anyone,
        # but the sidecar must still say a model made it.
        return library.rights("LicenseRef-model-output", title=spec["prompt"][:80],
                              attribution_required=False)

    if source not in SOURCES:
        raise ValueError(f"unknown imagery source {source!r}; "
                         f"expected one of {sorted(SOURCES) + ['generate', 'file']}")
    url, rights = SOURCES[source](spec, width)
    raw = _download_path(slug, beat_id)
    _download(url, raw)
    ok = _normalise(raw, dest)
    raw.unlink(missing_ok=True)
    if not ok:
        raise RuntimeError(f"fetched {url} but could not decode it")
    rights["retrieved_from"] = url
    return rights


def _rel(path):
    try:
        return path.relative_to(config.REPO)
    except ValueError:
        return path


def _footage(slug: str, beat_id: str, spec: dict, frag: dict, dest) -> bool:
    """Fetch a clip, cut `seconds` out of it, and fit it to the project frame.

    Sources are keyless on purpose: a Commons title resolves through the client
    already here, and `url` takes a direct file — NOAA Ocean Exploration serves
    public-domain 1080p over plain HTTPS, which is most of the marine arc.

    `start` picks the moment. Wildlife clips open on an empty frame far more
    often than not, so taking the head of a clip is usually taking the worst
    of it.
    """
    width, height = frag["resolution"]
    seconds = frag.get("seconds") or NOMINAL_SECONDS
    start = float(spec.get("start", 0.0))

    if spec.get("title") or spec.get("query"):
        src_url, rights = commons(spec, width, original=True)
    else:
        # A direct file. NOAA Ocean Exploration is the reason this branch
        # exists: US government work, public domain, 1080p, plain HTTPS, and
        # no catalogue to query.
        src_url = spec["url"]
        rights = library.rights(
            spec.get("spdx", "LicenseRef-public-domain"),
            title=spec.get("credit_title", ""),
            creator=spec.get("credit", ""),
            source_url=spec.get("source_url", src_url),
            attribution_required=bool(spec.get("attribution_required", False)))
    rights["modified"] = True
    rights["modification_note"] = (
        f"trimmed to {seconds:.1f}s from {start:.0f}s and fitted to the project frame")
    raw = _download_path(slug, beat_id).with_suffix(".src")
    raw.parent.mkdir(parents=True, exist_ok=True)
    if not raw.exists() or raw.stat().st_size == 0:
        _download(src_url, raw)

    bars = frag.get("letterbox", "0x0d1117")
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={bars},"
          f"setsar=1,format=yuv420p")
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-ss", f"{start:.2f}",
         "-i", str(raw), "-t", f"{seconds:.3f}", "-an", "-vf", vf,
         "-r", str(frag["fps"]), "-c:v", "h264_nvenc", "-preset", "p5",
         "-cq", "19", str(dest)],
        capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"[{slug}/{beat_id}] footage->clip FAILED\n{r.stderr[-400:]}")
        return False

    library.write(
        dest, library="imagery", rights_block=rights,
        provenance_block=library.provenance(
            spec.get("provider", "commons"), stem="visuals", source_type="human",
            inputs={"title": spec.get("title", ""), "url": spec.get("url", ""),
                    "start": start, "seconds": seconds}),
        selected=True)
    owed = library.attribution_line({"rights": rights})
    print(f"[{slug}/{beat_id}] footage {seconds:.1f}s from {start:.0f}s "
          f"-> {_rel(dest)}"
          + (f"\n  credit owed: {owed}" if owed else "\n  no attribution owed"))
    return True


def render(slug: str, beat_id: str) -> bool:
    frag = json.loads(beats.fragment_path(slug, beat_id, "imagery").read_text())
    spec = frag["visual"]
    width, height = frag["resolution"]

    # Checked before the fetch, not after: a locked beat should cost neither the
    # GPU nor a request to Commons.
    dest = config.asset_path(slug, beat_id)
    if beats.locked(spec, dest):
        print(f"[{slug}/{beat_id}] LOCKED — kept the existing clip, "
              f"nothing fetched or generated")
        return True

    # FOOTAGE, not a still. The arc this was built for is the evolution of
    # life, and a photograph of a jellyfish animated by a depth model is a
    # worse jellyfish than a jellyfish. Real motion is also free: no GPU, no
    # i2v roll of the dice, and it is already 1080p.
    #
    # Deliberately the same node, the same task and the same fragment as a
    # still: the output contract is "an mp4 of this beat's length at the
    # project frame", and how it was obtained is this function's business
    # alone. A new CLIP_KIND would have forked shorts.py and the timeline for
    # no gain.
    if spec.get("source") == "footage":
        try:
            return _footage(slug, beat_id, spec, frag, dest)
        except Exception as exc:
            print(f"[{slug}/{beat_id}] footage FAILED: {type(exc).__name__}: {exc}")
            return False

    try:
        rights = _still(slug, beat_id, spec, width)
    except Exception as exc:
        print(f"[{slug}/{beat_id}] imagery FAILED: {type(exc).__name__}: {exc}")
        return False

    # A beat can ask for the still to MOVE. The depth work needs torch, so it
    # runs in the heavy env behind the same bridge the image generator uses.
    #
    # Rendered at the beat's final length, not at NOMINAL_SECONDS: a moving clip
    # that is later held to fill its narration glides and then stops dead, which
    # is worse than never moving. `seconds` comes from the projection and is
    # null unless the slug is timed from the script.
    motion = spec.get("motion")
    if motion:
        seconds = frag.get("seconds") or NOMINAL_SECONDS
        fps = frag["fps"]
        request = {k: v for k, v in motion.items() if k != "kind"}
        request.update({
            "still": str(still_path(slug, beat_id)), "out": str(dest),
            "width": width, "height": height, "fps": fps, "seconds": seconds,
        })
        if motion.get("kind") == "i2v":
            from . import visual
            # Frames at the PROJECT's rate, not the model's native 24. assemble
            # concatenates with -c copy, so a clip at a different frame rate
            # would silently desync everything downstream of it.
            request["num_frames"] = int(round(seconds * fps))
            request["ffmpeg"] = config.ffmpeg()
            result = visual.bridge("animate", request)
        else:
            from . import voice
            request["ffmpeg"] = config.ffmpeg()
            result = voice.bridge("animate", request)
        if not result.get("ok"):
            print(f"[{slug}/{beat_id}] motion FAILED: {result.get('error')}")
            return False
        library.write(
            dest, library="imagery", rights_block=rights,
            provenance_block=library.provenance(
                spec.get("source", "commons"), stem="visuals",
                source_type="model" if spec.get("source") == "generate" else "human",
                model=result.get("model", ""), prompt=spec.get("prompt", ""),
                seed=spec.get("seed"),
                inputs={"query": spec.get("query", ""), "title": spec.get("title", ""),
                        "motion": motion}),
            selected=True,
        )
        owed = library.attribution_line({"rights": rights})
        print(f"[{slug}/{beat_id}] imagery+{motion.get('kind', 'motion')} "
              f"{result.get('seconds')}s -> {dest.relative_to(config.REPO)}"
              + (f"\n  credit owed: {owed}" if owed else "\n  no attribution owed"))
        return True

    # Fit inside the frame and letterbox rather than crop: a historical plate
    # cropped to 16:9 loses the part someone drew deliberately.
    # The `loop` FILTER, not the demuxer's -loop: the latter is image2-specific
    # and Commons serves whatever the uploader gave it.
    bars = frag.get("letterbox", "0x0d1117")
    hold = (f"loop=loop=-1:size=1:start=0,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={bars},"
            f"setsar=1,format=yuv420p")
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error",
         "-i", str(still_path(slug, beat_id)), "-vf", hold,
         "-t", f"{NOMINAL_SECONDS}", "-r", str(frag["fps"]),
         "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", str(dest)],
        capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"[{slug}/{beat_id}] still->clip FAILED\n{r.stderr[-400:]}")
        return False

    library.write(
        dest, library="imagery", rights_block=rights,
        provenance_block=library.provenance(
            spec.get("source", "commons"), stem="visuals",
            source_type="model" if spec.get("source") == "generate" else "human",
            model=spec.get("model", ""), prompt=spec.get("prompt", ""),
            seed=spec.get("seed"),
            inputs={"query": spec.get("query", ""), "title": spec.get("title", "")}),
        selected=True,
    )
    owed = library.attribution_line({"rights": rights})
    print(f"[{slug}/{beat_id}] imagery -> {dest.relative_to(config.REPO)}"
          + (f"\n  credit owed: {owed}" if owed else "\n  no attribution owed"))
    return True
