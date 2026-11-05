"""The ComfyUI seam. Zero dependencies, stdlib HTTP only.

ComfyUI runs as its own service (`imagegen` in Mission Control, :8188) with its
own heavy environment. This module never imports torch, never loads a model and
never learns a node name — it queues a graph somebody else built, waits, and
hands back the produced files. That is the same arrangement as the voice and
visual bridges, except the seam is a socket rather than a subprocess, so there
is no third env to keep alive here.

If the service is down, every call returns a clear error rather than raising:
the Gen Lab has to be able to say "start imagegen" instead of stack-tracing.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
TIMEOUT = 15


def _url(path: str, host: str = None) -> str:
    return f"http://{host or HOST}{path}"


def available(host: str = None) -> bool:
    try:
        urllib.request.urlopen(_url("/system_stats", host), timeout=3)
        return True
    except Exception:
        return False


def models(host: str = None) -> dict:
    """Which checkpoints/unets/encoders this ComfyUI can actually see.

    The Gen Lab asks before offering a recipe: a menu entry for a model whose
    weights are not on disk is a promise the box cannot keep.
    """
    try:
        with urllib.request.urlopen(_url("/object_info", host), timeout=TIMEOUT) as r:
            oi = json.load(r)
    except Exception as exc:
        return {"error": str(exc)}

    def opts(node, field):
        try:
            return list(oi[node]["input"]["required"][field][0])
        except (KeyError, IndexError, TypeError):
            return []

    return {
        "checkpoints": opts("CheckpointLoaderSimple", "ckpt_name"),
        "unets": opts("UNETLoader", "unet_name"),
        "text_encoders": opts("CLIPLoader", "clip_name"),
        "vaes": opts("VAELoader", "vae_name"),
    }


def submit(graph: dict, client_id: str = "genlab", host: str = None) -> dict:
    """Queue a graph. Returns {"prompt_id": ...} or {"error": ...}."""
    body = json.dumps({"prompt": graph, "client_id": client_id}).encode()
    req = urllib.request.Request(_url("/prompt", host), data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        # ComfyUI answers a bad graph with a 400 and a useful JSON body; losing
        # it to a bare "HTTP 400" is what makes graph bugs hard to find.
        try:
            return {"error": json.load(exc).get("error", str(exc)), "detail": True}
        except Exception:
            return {"error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"error": str(exc)}


def result(prompt_id: str, host: str = None) -> dict | None:
    """None while still running; a dict with `files` or `error` once done."""
    try:
        with urllib.request.urlopen(_url(f"/history/{prompt_id}", host), timeout=TIMEOUT) as r:
            hist = json.load(r)
    except Exception as exc:
        return {"error": str(exc)}
    if prompt_id not in hist:
        return None
    entry = hist[prompt_id]
    status = entry.get("status", {})
    if status.get("status_str") == "error":
        return {"error": json.dumps(status.get("messages", []))[:800]}
    files = []
    for node_out in entry.get("outputs", {}).values():
        for key in ("images", "audio", "gifs"):
            for f in node_out.get(key, []):
                files.append({"filename": f["filename"],
                              "subfolder": f.get("subfolder", ""),
                              "type": f.get("type", "output")})
    return {"files": files}


def wait(prompt_id: str, timeout: float = 1800, host: str = None) -> dict:
    t0 = time.perf_counter()
    while True:
        r = result(prompt_id, host)
        if r is not None:
            r["seconds"] = round(time.perf_counter() - t0, 2)
            return r
        if time.perf_counter() - t0 > timeout:
            return {"error": f"timed out after {timeout}s"}
        time.sleep(0.3)


def fetch(f: dict, host: str = None) -> bytes:
    """Pull one produced file's bytes out of ComfyUI's output store."""
    q = urllib.parse.urlencode({"filename": f["filename"],
                                "subfolder": f.get("subfolder", ""),
                                "type": f.get("type", "output")})
    with urllib.request.urlopen(_url(f"/view?{q}", host), timeout=120) as r:
        return r.read()
