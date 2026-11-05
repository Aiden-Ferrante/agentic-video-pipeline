#!/usr/bin/env python3
"""Gen Lab — generate many variants, pick one, commit it to the shot list.

    python3 genlab/serve.py [port]        default 8832

Zero dependencies. The heavy work happens in ComfyUI (`imagegen` in Mission
Control, :8188); this server holds no model, no torch and no build state beyond
the handle on a running batch. Kill it mid-batch and the variants already
written stay on disk, because the pool IS the state — there is no database.

Security model, same as the canvas: the client sends NAMES, never paths. A slug
is checked against the videos that exist, a recipe against a fixed table, a
variant against the files in its own batch directory. Nothing the page can say
resolves to an arbitrary path. Generation and commit are loopback-only —
committing rewrites shotlist.json, which is the one file here that cannot be
regenerated from anything else.
"""
import json
import os
import re
import sys
import threading
import time
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from pipeline import comfy, pool, recipes  # noqa: E402

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# The fleet's shared tokens, served at /fleet.css so the page can link them.
# Same call the canvas makes: a missing file costs the palette and nothing else.
FLEET_CSS = Path(os.environ.get(
    "FLEET_UI_CSS", Path.home() / "Desktop/code/fleet-ui/fleet.css"))

# What each running batch is doing, for the progress bar only. The variants
# themselves are read back off disk, so losing this dict loses nothing but a
# spinner.
RUNS: dict[str, dict] = {}
RUNS_LOCK = threading.Lock()


def slugs() -> list[str]:
    return sorted(p.parent.name for p in (REPO / "videos").glob("*/shotlist.json"))


def beats_of(slug: str) -> list[dict]:
    try:
        doc = json.loads((REPO / "videos" / slug / "shotlist.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for b in doc.get("beats", []):
        vis = b.get("visual") or {}
        out.append({"id": b.get("id", ""), "kind": vis.get("kind", ""),
                    "source": vis.get("source", ""),
                    "vo": (b.get("vo_text") or "")[:90]})
    return out


def catalogue() -> dict:
    """Recipes, annotated with whether their weights are actually present.

    Offering a model whose files are missing is a promise the box cannot keep,
    so the page greys it out and says which file is absent.
    """
    up = comfy.available()
    have = comfy.models() if up else {}
    def note(table):
        out = []
        for key, r in table.items():
            gaps = recipes.missing(r, have) if up else list(
                n for names in r.get("needs", {}).values() for n in names)
            out.append({"key": key, "label": r["label"], "steps": r["steps"],
                        "note": r.get("note", ""), "missing": gaps,
                        "width": r.get("width"), "height": r.get("height")})
        return out
    return {"comfy_up": up, "comfy_host": comfy.HOST,
            "imagery": note(recipes.IMAGE_RECIPES),
            "sound": note(recipes.MUSIC_RECIPES),
            "slugs": slugs()}


def _run_batch(slug, kind, batch_id, req):
    """Generate the variants one at a time, writing each as it lands.

    Serial on purpose: ComfyUI holds one model resident and a second concurrent
    request would evict and reload it between variants, which on this box costs
    more than the generation itself.
    """
    count = req["count"]
    for i in range(count):
        seed = req["seed_start"] + i
        with RUNS_LOCK:
            RUNS[batch_id] = {"slug": slug, "kind": kind, "done": i,
                              "total": count, "state": "running"}
        try:
            if kind == "imagery":
                graph = recipes.image_graph(req["recipe"], req["prompt"], seed,
                                            width=req.get("width"),
                                            height=req.get("height"))
            else:
                graph = recipes.music_graph(req["recipe"], req["prompt"], seed,
                                            seconds=req.get("seconds", 60.0),
                                            bpm=req.get("bpm", 90),
                                            keyscale=req.get("keyscale", "C major"))
            sub = comfy.submit(graph, client_id=f"genlab-{batch_id}")
            if "error" in sub:
                raise RuntimeError(sub["error"])
            res = comfy.wait(sub["prompt_id"])
            if "error" in res:
                raise RuntimeError(res["error"])
            if not res.get("files"):
                raise RuntimeError("ComfyUI produced no file")
            data = comfy.fetch(res["files"][0])
            pool.add_variant(slug, kind, batch_id, i, data,
                             recipe=req["recipe"], prompt=req["prompt"], seed=seed,
                             extra={"seconds_wall": res.get("seconds")})
        except Exception as exc:
            with RUNS_LOCK:
                RUNS[batch_id] = {"slug": slug, "kind": kind, "done": i,
                                  "total": count, "state": "error",
                                  "error": str(exc)[:400]}
            return
    with RUNS_LOCK:
        RUNS[batch_id] = {"slug": slug, "kind": kind, "done": count,
                          "total": count, "state": "done"}


def start(req: dict) -> tuple[int, dict]:
    slug, kind = str(req.get("slug", "")), str(req.get("kind", ""))
    if slug not in slugs():
        return 400, {"ok": False, "error": f"unknown video {slug!r}"}
    if kind not in pool.KINDS:
        return 400, {"ok": False, "error": f"unknown kind {kind!r}"}
    table = recipes.IMAGE_RECIPES if kind == "imagery" else recipes.MUSIC_RECIPES
    recipe = str(req.get("recipe", ""))
    if recipe not in table:
        return 400, {"ok": False, "error": f"unknown recipe {recipe!r}"}
    if not comfy.available():
        return 503, {"ok": False,
                     "error": f"ComfyUI is not answering on {comfy.HOST} — "
                              f"start the 'imagegen' app in Mission Control"}
    gaps = recipes.missing(table[recipe], comfy.models())
    if gaps:
        return 503, {"ok": False, "error": f"missing weights: {', '.join(gaps)}"}

    prompt = str(req.get("prompt", "")).strip()
    if not prompt:
        return 400, {"ok": False, "error": "a prompt is required"}
    count = max(1, min(int(req.get("count", 4)), 12))
    spec = {
        "recipe": recipe, "prompt": prompt, "count": count,
        "seed_start": int(req.get("seed_start", int(time.time()) % 100000)),
    }
    if kind == "imagery":
        spec["width"] = int(req.get("width") or table[recipe]["width"])
        spec["height"] = int(req.get("height") or table[recipe]["height"])
        spec["seconds"] = float(req.get("seconds", 5.0))
    else:
        spec["seconds"] = float(req.get("seconds", 60.0))
        spec["bpm"] = int(req.get("bpm", 90))
        spec["keyscale"] = str(req.get("keyscale", "C major"))

    batch_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    pool.start_batch(slug, kind, batch_id, spec)
    with RUNS_LOCK:
        RUNS[batch_id] = {"slug": slug, "kind": kind, "done": 0,
                          "total": count, "state": "running"}
    threading.Thread(target=_run_batch, args=(slug, kind, batch_id, spec),
                     daemon=True).start()
    return 200, {"ok": True, "batch": batch_id}


def variant_bytes(slug, kind, batch, variant) -> tuple[bytes, str] | None:
    """Read one pool file. Every component is validated as a NAME first, so
    this cannot be walked out of the pool directory."""
    if slug not in slugs() or kind not in pool.KINDS:
        return None
    if not (SAFE_ID.match(batch) and SAFE_ID.match(variant)):
        return None
    path = pool.batch_dir(slug, kind, batch) / variant
    if not path.is_file() or path.suffix != pool.EXT[kind]:
        return None
    mime = "image/png" if kind == "imagery" else "audio/flac"
    return path.read_bytes(), mime


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def do_GET(self):
        parts = self.path.split("?")
        path = parts[0]
        q = dict(p.split("=", 1) for p in parts[1].split("&")
                 if "=" in p) if len(parts) > 1 else {}
        from urllib.parse import unquote
        q = {k: unquote(v) for k, v in q.items()}

        if path == "/health":
            return self._json(200, {"ok": True, "comfy": comfy.available(),
                                    "videos": len(slugs())})
        if path == "/api/catalogue":
            return self._json(200, catalogue())
        if path == "/api/beats":
            slug = q.get("slug", "")
            if slug not in slugs():
                return self._json(404, {"error": "unknown video"})
            return self._json(200, {"beats": beats_of(slug)})
        if path == "/api/pool":
            slug, kind = q.get("slug", ""), q.get("kind", "")
            if slug not in slugs() or kind not in pool.KINDS:
                return self._json(404, {"error": "unknown video or kind"})
            with RUNS_LOCK:
                running = {k: v for k, v in RUNS.items()
                           if v["slug"] == slug and v["kind"] == kind}
            return self._json(200, {"batches": pool.batches(slug, kind),
                                    "running": running})
        if path == "/api/variant":
            got = variant_bytes(q.get("slug", ""), q.get("kind", ""),
                                q.get("batch", ""), q.get("variant", ""))
            if got is None:
                return self._json(404, {"error": "no such variant"})
            body, mime = got
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=31536000")
            self.end_headers()
            return self.wfile.write(body)
        if path == "/fleet.css":
            try:
                body = FLEET_CSS.read_bytes()
            except OSError:
                body = b"/* fleet.css not found - default palette */\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/css")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/api/generate", "/api/commit"):
            return self._json(404, {"ok": False, "error": "no such endpoint"})
        if not self._local():
            return self._json(403, {"ok": False,
                                    "error": "local-only; browse from this machine"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"ok": False, "error": "bad request"})

        if path == "/api/generate":
            code, payload = start(req)
            return self._json(code, payload)

        slug, kind = str(req.get("slug", "")), str(req.get("kind", ""))
        batch, variant = str(req.get("batch", "")), str(req.get("variant", ""))
        beat = str(req.get("beat", ""))
        if slug not in slugs() or kind not in pool.KINDS:
            return self._json(400, {"ok": False, "error": "unknown video or kind"})
        if not (SAFE_ID.match(batch) and SAFE_ID.match(variant)):
            return self._json(400, {"ok": False, "error": "bad batch or variant"})
        if beat and not SAFE_ID.match(beat):
            return self._json(400, {"ok": False, "error": "bad beat id"})
        result = pool.commit(slug, kind, batch, variant, beat_id=beat)
        return self._json(200 if result.get("ok") else 400, result)

    def log_message(self, *args):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8832
    handler = partial(Handler, directory=str(HERE))
    print(f"gen lab on http://127.0.0.1:{port}  (comfy {comfy.HOST})")
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    main()
