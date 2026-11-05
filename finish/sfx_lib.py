"""Act SFX kit from real recordings: the Sonniss GDC 2026 bundle (royalty-free, commercial use, no attribution;
licence in libraries/sound/sonniss-gdc2026/License; retrieved 2026-09-23). Each kit sound is the FIRST whole
event in its source file (files often hold several takes), or a window of a drone, at 44.1 kHz stereo.
Out: paths.KIT/<name>.flac + provenance.json (source file, vendor, licence, window)."""
import json, pathlib, subprocess, numpy as np, soundfile as sf
from paths import KIT as OUT, SONNISS as LIB
OUT.mkdir(parents=True, exist_ok=True); SR = 44100
KIT = {  # name: (filename pattern, mode, max seconds, fade out s) — mode "event" = first event, float = window start (s)
 # v5 (2026-09-23) — "more sci-fi": designed sci-fi sources wherever v4 had a realistic one
 "tick":      ("Interface Sci-Fi Ping Down", "event", 0.8, 0.05),
 "data":      ("Readout Thin Long", "event", 4.4, 0.4),
 "line":      ("ArcPowerUpDesign04", "event", 2.5, 0.6),
 "chime":     ("Interface Arp Reveal Down Long", "event", 1.2, 0.1),
 "tension":   ("Transition Braam Slow Dark Creepy", "event", 4.5, 0.6),
 "hit":       ("Tone Downer (Reverb)", "event", 5.0, 1.0),
 "swell":     ("Swell Creature Strange Alien", "event", 4.0, 0.8),
 "shimmer":   ("Button Arp Twinkle", "event", 1.9, 0.2),
 "whoosh":    ("Impact Electric Tonal Deep", "event", 3.7, 0.5),
 "glitch":    ("Corrupted_Data Error", "event", 2.5, 0.4),
 "robot":     ("Robot Motor Dark Thump", "event", 1.1, 0.1),
 "craft":     ("Unidentified Encounter", "event", 6.0, 1.5),
 "rumble":    ("Interior Thunder Rumble", "event", 9.0, 2.5),
 "braam":     ("Cinematic Horn Braam, Epic, Cinematic, Dark, Instrument, Huge-32", "event", 7.0, 2.0),
 "burst":     ("Bass Burst Sweep Heavy", "event", 0.4, 0.05),
 "reverse":   ("astral travel", 4.0, 3.0, 0.8),
 "wind":      ("Wind Whipping Constricted", 0.5, 5.0, 1.5),
 "latch":     ("Click Deep Mechanism Latch", "event", 0.4, 0.03),
 "arc":       ("ArcDesign15", "event", 1.1, 0.1),
 "glitch_hi": ("Glitch_High_Electronic", "event", 0.8, 0.08),
 "bubble":    ("Liquid Impact, Bubble, Sci Fi, Hit 04", "event", 2.0, 0.3),
 "deny":      ("Interface Deny Low Fat Dark", "event", 1.2, 0.1),
 "bed":       ("Roomtone Space Ship Interior Muted", 5.0, 60.0, 3.0),
 "stone":     ("Interface Accept Glassy Snap", "event", 0.6, 0.05),
 "title":     ("Cinematic Horn Braam, Epic, Cinematic, Dark, Instrument, Huge-67", "event", 8.0, 2.5),
}
def first_event(x, gap=0.35, floor_db=-50):
    e = np.abs(x).max(1); thr = e.max() * 10 ** (floor_db / 20)
    on = np.flatnonzero(e > thr * 10);  i = max(0, on[0] - int(0.003 * SR)) if len(on) else 0
    quiet = e < thr; run = 0
    for j in range(i + int(0.05 * SR), len(e)):
        run = run + 1 if quiet[j] else 0
        if run > gap * SR: return x[i: j - run + int(0.05 * SR)]
    return x[i:]
prov = {}
for name, (pat, mode, cap, fo) in KIT.items():
    src = next(p for p in LIB.rglob("*.wav") if pat.lower() in p.name.lower())
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-af", "aresample=44100:resampler=soxr", "-ac", "2", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="float32").reshape(-1, 2).copy()
    if mode == "event": y = first_event(x); start = None
    else: start = mode; y = x[int(start * SR):]; fi = int(0.3 * SR); y[:fi] *= np.linspace(0, 1, fi)[:, None]
    y = y[: int(cap * SR)].copy(); f = min(len(y), int(fo * SR)); y[-f:] *= np.linspace(1, 0, f)[:, None]
    y = y / max(1e-6, np.abs(y).max()) * 0.89
    sf.write(OUT / f"{name}.flac", y, SR)
    prov[name] = {"source": str(src.relative_to(LIB)), "vendor": src.relative_to(LIB).parts[0].split(" - ")[0],
                  "licence": "Sonniss GDC 2026 Game Audio Bundle — royalty-free, commercial, no attribution",
                  "retrieved": "2026-09-23", "window": {"start": start, "seconds": round(len(y) / SR, 3)}}
    print(f"{name:8} {len(y)/SR:5.2f}s  {src.name[:70]}")
(OUT / "provenance.json").write_text(json.dumps(prov, indent=2))
