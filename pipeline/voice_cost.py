"""What the next build would spend on narration — read before a cloud voice runs, never after.

    python -m pipeline.voice_cost <slug>            the table, the total, the cap, the month's quota
    python -m pipeline.voice_cost <slug> --json     the same as JSON (the MCP tool and the agent read this)

A beat is SPENT when it has no recorded take in use and its voice is not already the bound provider's read of the
current words with the current voice: the wav sidecar's tool, `inputs.vo_text`, `voice_id` and `voice_settings` are
compared with the shot list and the resolved binding. Local providers cost nothing and are reported as such.
"""
import importlib.util
import json
import sys

from . import beats, config, library, providers, voice


def _bridge():
    spec = importlib.util.spec_from_file_location("elevenlabs_render", config.REPO / "bridges" / "elevenlabs_render.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def voice_ready(v: dict) -> bool:
    """The bridge's own rule (it refuses to synthesise with a voice that is not ready), so the two cannot drift."""
    return _bridge().voice_ready(v)


def plan(slug: str) -> dict:
    binding = providers.resolved_map(slug).get("narration") or {"provider": "local", "config": {}}
    provider, cfg = binding["provider"], binding.get("config", {})
    voice_id = cfg.get("voice_id", "")
    settings = cfg.get("voice_settings") or {}
    rows = []
    for b in beats.load(slug)["beats"]:
        text = (beats.narration_text(b) or "").strip()
        if not text:
            continue
        bid = b["id"]
        if voice.uses_take(slug, bid):
            rows.append({"beat": bid, "status": "take", "chars": 0})
            continue
        doc = library.read(voice.wav_path(slug, bid)) or {}
        prov = doc.get("provenance") or {}
        inputs = prov.get("inputs") or {}
        current = (prov.get("tool") == provider and inputs.get("vo_text", "").strip() == text
                   and (provider != "elevenlabs" or (inputs.get("voice_id") == voice_id
                                                      and (inputs.get("voice_settings") or {}) == settings)))
        rows.append({"beat": bid, "status": "current" if current else "synth",
                     "chars": 0 if current else len(text), "was": prov.get("tool", "none")})
    todo = [r for r in rows if r["status"] == "synth"]
    out = {"slug": slug, "provider": provider, "voice_id": voice_id, "beats": rows,
           "synth_beats": len(todo), "synth_chars": sum(r["chars"] for r in todo)}
    if provider == "elevenlabs":
        br = _bridge()
        out["cap_beats_per_run"] = br._cap()
        key = br.api_key()
        if key:
            import urllib.request
            try:
                req = urllib.request.Request(f"{br.API}/user/subscription", headers={"xi-api-key": key})
                sub = json.load(urllib.request.urlopen(req, timeout=20))
                out["quota"] = {"used": sub.get("character_count"), "limit": sub.get("character_limit"),
                                "resets_unix": sub.get("next_character_count_reset_unix")}
                v = json.load(urllib.request.urlopen(urllib.request.Request(
                    f"{br.API}/voices/{voice_id}", headers={"xi-api-key": key}), timeout=20))
                out["voice"] = {"name": v.get("name"), "category": v.get("category"), "ready": br.voice_ready(v)}
            except Exception as e:                       # the quota is information, never a gate
                out["quota"] = {"error": str(e)[:120]}
    else:
        out["synth_chars"] = 0
    return out


def main(argv):
    slug = argv[0]
    p = plan(slug)
    if "--json" in argv:
        print(json.dumps(p, indent=1))
        return
    for r in p["beats"]:
        if r["status"] == "synth":
            print(f"  {r['beat']:6} {r['chars']:5} chars   (now: {r['was']})")
    takes = sum(r["status"] == "take" for r in p["beats"])
    cur = sum(r["status"] == "current" for r in p["beats"])
    print(f"{slug}: {p['provider']} — {p['synth_beats']} beats to synthesise, {p['synth_chars']} chars "
          f"({takes} recorded takes, {cur} already current)")
    if "cap_beats_per_run" in p:
        print(f"  spend cap: {p['cap_beats_per_run']} beats per doit run (ELEVENLABS_MAX_BEATS)")
    q = p.get("quota") or {}
    if "limit" in q:
        print(f"  quota: {q['used']} of {q['limit']} chars used this period; this build adds {p['synth_chars']}")
    v = p.get("voice") or {}
    if v:
        print(f"  voice: {v['name']} ({v['category']}) — {'ready' if v['ready'] else 'NOT READY (still training)'}")


if __name__ == "__main__":
    main(sys.argv[1:])
