"""The finish stage as doit sees it: which videos opt in, how they split into acts, what each step reads.

The scripts themselves live in `finish/` (they run under two different envs — the pipeline's and Stable
Audio's — so they stay scripts, not imports). This module only answers questions for `dodo.py`, at load time:

    acts        one per chapter, in shot-list order — the same grouping `finish/acts_plan.py` writes
    timing      `out/finish/timing/<act>.json`: the act's length and each beat's [start, length]. The score
                depends on THIS, not on the fit clips, so a picture-only change never re-scores an act.

A video opts in through its style (`ml-series` binds `finish.enabled`) or its shot list's own `finish` block.
"""
import os
import re
import subprocess

from . import align, beats, config, fit, voice

SCRIPTS = config.REPO / "finish"
KIT = config.REPO / "libraries" / "sound" / "kit"
BASE_TAKE = config.REPO / "libraries" / "sound" / "analog-awe-s12.flac"


PER_VIDEO = ("acts", "beds")          # finish keys that belong to one video, not the template


def enabled(slug: str) -> bool:
    """The style's `finish` binding (ml-series) or the shot list's own block opts a video in."""
    try:
        return bool((beats.effective(slug).get("finish") or {}).get("enabled"))
    except (OSError, ValueError):
        return False


def settings(slug: str) -> dict:
    """The template: the effective `finish` block without the per-video parts or notes."""
    fin = beats.effective(slug).get("finish") or {}
    return {k: v for k, v in fin.items() if k not in PER_VIDEO and not k.startswith("_")}


def per_video(slug: str) -> dict:
    fin = beats.load(slug).get("finish") or {}
    return {k: fin.get(k) for k in PER_VIDEO}


def score_spec(slug: str, act: dict, st: dict, spec: dict) -> dict:
    """The act's score spec from the shot list (keyed by chapter), the style's prompt tail applied."""
    s = ((spec.get("acts") or {}).get(act["chapter"]) or {}).get("score") or {}
    tail = (st.get("score") or {}).get("tail", "")
    if "arc" in s:
        arc = {**s["arc"], "steps": [{**x, "prompt": x["prompt"] if x["prompt"] == "@take" else x["prompt"] + tail}
                                      for x in s["arc"]["steps"]]}
        return {"arc": {k: v for k, v in arc.items() if not k.startswith("_")}}
    if "mood" in s:
        return {"prompt": s["mood"] + tail, "seed": s["seed"], "open_with_take": bool(s.get("open_with_take"))}
    return {}


def act_cues(slug: str, act: dict, spec: dict) -> dict:
    by_id = {b["id"]: b for b in beats.load(slug)["beats"]}
    cues = [[bid, c["word"], c.get("nth", 0), c["fx"], c["gain"], c.get("offset", 0.0)]
            for bid in act["ids"] if bid in by_id for c in by_id[bid].get("sfx") or []]
    beds = [[d["from"], d["to"], d["fx"], d["gain"]] for d in spec.get("beds") or [] if d["from"] in act["ids"]]
    return {"cues": cues, "beds": beds}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def cue_problems(slug: str) -> list[str]:
    """Every sound-effect cue whose word is not said (often enough) in its beat — the failure a script edit causes
    silently (2026-10-08: m00a's "those" became "these"). Same normalisation sfx_mix uses on the alignment."""
    out = []
    for b in beats.load(slug)["beats"]:
        said = [_norm(w) for w in beats.narration_text(b).split()]
        for c in b.get("sfx") or []:
            w, nth = c.get("word"), c.get("nth", 0)
            if w is None or str(c.get("fx", "")).startswith("@"):
                continue
            n = said.count(_norm(w))
            if n == 0 or (nth >= 0 and nth >= n) or (nth < 0 and -nth > n):
                out.append(f"{b['id']}: {c['fx']} on {w!r} (#{nth}) — the line says it {n}×")
    return out


def work(slug: str):
    return config.REPO / "videos" / slug / "out" / "finish"


def acts(slug: str) -> list[dict]:
    """Acts by chapter. With the template's `card` on, each act opens on its title card and closes on a held last
    frame — two clips that sit in the act's id list like beats (`_card01`, `_tail01`), so every finish step's
    back-to-back arithmetic places them without knowing what they are."""
    out = []
    for b in beats.load(slug)["beats"]:
        ch = b.get("chapter", "")
        if not out or out[-1]["chapter"] != ch:
            out.append({"chapter": ch, "ids": []})
        out[-1]["ids"].append(b["id"])
    card = card_settings(slug)
    for i, a in enumerate(out, 1):
        a["n"] = i
        a["slug"] = f"act{i:02d}-" + re.sub(r"[^a-z0-9]+", "-", a["chapter"].lower()).strip("-")
        if card:
            # `skip_first` (2026-10-14, the human: "open immediately with the paper"): act one starts on its first line
            a["ids"] = [*([] if i == 1 and card.get("skip_first") else [card_id(i)]), *a["ids"], tail_id(i)]
    return out


# ---------------------------------------------------------------- act title cards (2026-10-09)
# The human: "the transitions from one act to another is super rough. I don't want the fade in for my voice, only for
# the music … a title card for each act with the new music fading in while hanging for 2-4 seconds". The dip between
# acts now falls on a held last frame (voice already done) and a title card (voice not yet begun): only music fades.

CARD = {"enabled": False, "hold": 3.0, "tail": 0.6, "first_lead": 1.0, "font": "Crimson Text",
        "bg": "0x1c1714", "ink": "0xF0EBE0", "muted": "0x9A8F80", "title_size": 92, "label_size": 30}
NUMERALS = "One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve".split()


def card_settings(slug: str) -> dict:
    c = {**CARD, **(settings(slug).get("card") or {})}
    return c if c.get("enabled") else {}


def card_id(n: int) -> str:
    return f"_card{n:02d}"


def tail_id(n: int) -> str:
    return f"_tail{n:02d}"


def is_card(bid: str) -> bool:
    return bid.startswith(("_card", "_tail"))


def card_spec(slug: str, a: dict) -> dict:
    """Everything the card's pixels depend on: its words, its timing (the dip it is entered through), its look."""
    c, D = card_settings(slug), float(settings(slug).get("dip_seconds", 2.5))
    lead = D if a["n"] > 1 else float(c["first_lead"])
    toc = [x["chapter"] for x in acts(slug)] if c.get("toc") else []
    return {"label": "ACT " + (NUMERALS[a["n"] - 1].upper() if a["n"] <= len(NUMERALS) else str(a["n"])),
            "title": a["chapter"], "toc": toc, "n": a["n"], "lead": lead, "hold": float(c["hold"]), "seconds": round(lead + float(c["hold"]), 3),
            "tail": round(float(c["tail"]) + (D if a["n"] < len(acts(slug)) else 0.0), 3),
            **{k: c[k] for k in ("font", "bg", "ink", "muted", "title_size", "label_size")}}


def _alpha(t0: float, t1: float, fin: float = 0.8, fout: float = 0.6) -> str:
    return (f"if(lt(t\\,{t0:.3f})\\,0\\,if(lt(t\\,{t0 + fin:.3f})\\,(t-{t0:.3f})/{fin}\\,"
            f"if(lt(t\\,{t1 - fout:.3f})\\,1\\,max(0\\,({t1:.3f}-t)/{fout}))))")


def render_card(slug: str, n: int) -> bool:
    """The act's title card as a fit clip: the series' dark ground, ACT label small and spaced, the chapter title in
    the caption face. Words fade in once the dip has arrived and out just before the first line is spoken."""
    a = next(x for x in acts(slug) if x["n"] == n)
    s = card_spec(slug, a)
    dest = fit.fit_path(slug, card_id(n))
    dest.parent.mkdir(parents=True, exist_ok=True)
    txt = work(slug) / "cards"
    txt.mkdir(parents=True, exist_ok=True)
    (txt / f"{n:02d}.label.txt").write_text("   ".join(" ".join(w) for w in s["label"].split()))
    (txt / f"{n:02d}.title.txt").write_text(s["title"])
    al = _alpha(s["lead"], s["seconds"])
    draw = lambda f, size, color, y: (f"drawtext=textfile='{txt / f}':font='{s['font']}':fontsize={size}:"
                                      f"fontcolor={color}:x=(w-text_w)/2:y={y}:alpha='{al}'")
    vf = ",".join([draw(f"{n:02d}.label.txt", s["label_size"], s["muted"], "h/2-80"),
                   draw(f"{n:02d}.title.txt", s["title_size"], s["ink"], "h/2-12")])
    if s["toc"]:
        # `toc` (2026-10-14, the human: "all acts shown as a table of contents"): every act listed, this one lit
        rows, pitch = len(s["toc"]), 78
        top = 540 - (rows - 1) * pitch / 2
        parts = [draw(f"{n:02d}.label.txt", s["label_size"], s["muted"], f"{top - 120:.0f}")]
        for k, t in enumerate(s["toc"], 1):
            (txt / f"{n:02d}.toc{k}.txt").write_text(f"{k}    {t}")
            here = k == s["n"]
            parts.append(draw(f"{n:02d}.toc{k}.txt", 62 if here else 40, s["ink"] if here else s["muted"],
                              f"{top + (k - 1) * pitch - (31 if here else 20):.0f}").replace(
                f"alpha='{al}'", f"alpha='{al}*{1.0 if here else 0.55}'"))
        vf = ",".join(parts)
    r = subprocess.run([config.ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"color=c={s['bg']}:s=1920x1080:r=30:d={s['seconds']:.3f}", "-vf", vf,
                        "-t", f"{s['seconds']:.3f}", "-an", *config.video_encoder(19), "-pix_fmt", "yuv420p",
                        "-video_track_timescale", "15360", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[{slug}/{card_id(n)}] card FAILED\n{r.stderr[-400:]}")
        return False
    print(f"[{slug}/{card_id(n)}] {s['label']} — {s['title']} ({s['seconds']}s)")
    return True


def render_tail(slug: str, n: int) -> bool:
    """The act's last frame, held: the room the dip to black takes, after the voice has finished."""
    a = next(x for x in acts(slug) if x["n"] == n)
    s, last = card_spec(slug, a), fit.fit_path(slug, a["ids"][-2])
    dest = fit.fit_path(slug, tail_id(n))
    r = subprocess.run([config.ffmpeg(), "-y", "-v", "error", "-sseof", "-0.2", "-i", str(last),
                        "-vf", f"select='eq(n\\,0)',setpts=PTS-STARTPTS,fps=30,tpad=stop_mode=clone:stop_duration={s['tail']:.3f}",
                        "-t", f"{s['tail']:.3f}", "-an", *config.video_encoder(19), "-pix_fmt", "yuv420p",
                        "-video_track_timescale", "15360", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[{slug}/{tail_id(n)}] tail FAILED\n{r.stderr[-400:]}")
        return False
    print(f"[{slug}/{tail_id(n)}] held {s['tail']}s")
    return True


def acts_json(slug):
    return work(slug) / "acts.json"


def timing_path(slug, act):
    return work(slug) / "timing" / f"{act}.json"


def score_path(slug, act):
    return work(slug) / "score" / f"{act}.flac"


def narrated_path(slug, act):
    return work(slug) / "narrated" / f"{act}.mp4"


def act_path(slug, act):
    return work(slug) / "acts" / f"{act}.mp4"


def settings_path(slug):
    return work(slug) / "settings.json"


def cues_path(slug, act):
    return work(slug) / "cues" / f"{act}.json"


def film_path(slug):
    return work(slug) / f"{slug}-full.mp4"


def voice_deps(slug: str, ids: list[str]) -> list:
    """Each voiced beat's wav, its sidecar (which says take / cloud / scratch) and its alignment — unconditional,
    like task_fit's deps, so the dependency SET never changes between runs."""
    if not beats.needs_voice(slug):
        return []
    narrated = set(beats.narrated_ids(slug))
    out = []
    for b in ids:
        if b in narrated:
            w = voice.wav_path(slug, b)
            out += [voice.spoken_path(slug, b), w.with_name(w.name + ".json"), align.align_path(slug, b)]
    return out


def fit_deps(slug: str, ids: list[str]) -> list:
    return [fit.fit_path(slug, b) for b in ids]


def stable_audio_python():
    return config.STABLE_AUDIO_ROOT / ".venv" / "bin" / "python"


def run(slug: str, script: str, *args, python=None) -> bool:
    """One finish script, for one video. A doit python-action: True on success."""
    py = str(python or (config.REPO / ".venv" / "bin" / "python"))
    env = {**os.environ, "FINISH_SLUG": slug}
    r = subprocess.run([py, str(SCRIPTS / script), *map(str, args)], cwd=SCRIPTS, env=env)
    return r.returncode == 0


def seconds_of(timecode) -> float:
    """'4:12', '1:02:03', '252.5' or a number → seconds."""
    if isinstance(timecode, (int, float)):
        return float(timecode)
    parts = [float(p) for p in str(timecode).strip().split(":")]
    out = 0.0
    for p in parts:
        out = out * 60 + p
    return out


def _act_seconds(slug: str, a: dict) -> float:
    """The act's length as film.py measures it (the act file's video stream), else the plan's."""
    p = act_path(slug, a["slug"])
    if p.exists():
        r = subprocess.run([config.ffprobe(), "-v", "error", "-select_streams", "v", "-show_entries",
                            "stream=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
        try:
            return float(r.stdout.strip())
        except ValueError:
            pass
    return float(a["seconds"])


def locate(slug: str, timecode) -> dict:
    """Where a moment of the FINISHED FILM lives: act, beat, the offset inside the beat, the words being said, the
    sound cues on that beat. The film joins acts with dips that overlap by `dip_seconds` — the same arithmetic
    film.py uses — and a moment inside a dip names both sides. This is how a note ("at 4:12 the hit is too loud")
    becomes an edit."""
    import json
    t = seconds_of(timecode)
    acts_doc = json.loads(acts_json(slug).read_text())
    st = json.loads(settings_path(slug).read_text()) if settings_path(slug).exists() else {}
    D = float(st.get("dip_seconds", 2.5))
    durs = [_act_seconds(slug, a) for a in acts_doc]
    starts, acc = [0.0], durs[0]
    for d in durs[1:]:
        starts.append(acc - D); acc += d - D
    if not 0 <= t <= acc:
        return {"error": f"{t:.2f}s is outside the film (0–{acc:.2f}s)"}
    k = max(i for i, s in enumerate(starts) if s <= t)
    a, local = acts_doc[k], t - starts[k]
    bid, (b0, blen) = next(((b, v) for b, v in a["beats"].items() if v[0] <= local < v[0] + v[1]),
                           list(a["beats"].items())[-1])
    off = local - b0
    said, word = [], None
    ap = align.align_path(slug, bid)
    if ap.exists():
        al = json.loads(ap.read_text()); s0 = al.get("speech_start") or 0.0
        ws = [(w["start"] - s0, w["end"] - s0, w["word"]) for w in al.get("words", [])]
        said = [w for s, e, w in ws if off - 2.5 <= s <= off + 2.5]
        word = next((w for s, e, w in ws if s <= off <= e + 0.15), None)
    beat = next((b for b in beats.load(slug)["beats"] if b["id"] == bid), {})
    dip = None
    if k > 0 and t < starts[k] + D:
        dip = f"dip from {acts_doc[k - 1]['slug']} into {a['slug']}"
    elif k < len(acts_doc) - 1 and t > starts[k + 1]:
        dip = f"dip from {a['slug']} into {acts_doc[k + 1]['slug']}"
    return {"t": round(t, 2), "act": a["slug"], "chapter": a["chapter"], "act_offset": round(local, 2),
            "beat": bid, "beat_offset": round(off, 2), "beat_seconds": blen, "word": word,
            "said_around": " ".join(said), "sfx": beat.get("sfx") or [], "in_dip": dip}
