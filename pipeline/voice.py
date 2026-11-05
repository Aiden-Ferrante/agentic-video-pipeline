"""Voice node: a beat's narration becomes voice/<beat>.wav.

Two sources, one code path. A real take at voice/takes/<beat>.wav always wins;
otherwise the line is synthesised. That precedence is deliberately a FILE
question and not a mode flag — there is no "scratch mode" to leave switched on
by accident, and a video can be half-recorded without the pipeline caring.

Scratch VO exists so the edit can be built before anything is recorded. It is
worth cloning your own voice for it (drop a ~10s sample at voice/reference.wav):
the scratch timing is then close to the timing the real take will have, so the
animation fitting done against scratch survives the swap.

This machine has no microphone. Takes are recorded elsewhere and copied in;
nothing here captures audio.
"""
import json
import subprocess

from . import beats, config, library

TAKE_DIR = "takes"
REFERENCE = "reference.wav"

# EBU R128 for YouTube. -14 LUFS integrated, -1 dBTP ceiling, 48k.
TARGET_I, TARGET_TP, TARGET_LRA, RATE = -14.0, -1.0, 11.0, 48000


def voice_dir(slug: str):
    return config.video_dir(slug) / "voice"


def wav_path(slug: str, beat_id: str):
    return voice_dir(slug) / f"{beat_id}.wav"


def spoken_path(slug: str, beat_id: str):
    """The narration as the film lays it: the wav with pace's pauses inside (pipeline/pace.py)."""
    return voice_dir(slug) / f"{beat_id}.paced.wav"


def take_path(slug: str, beat_id: str):
    """A real recording, dropped in by hand. Wins over synthesis."""
    return voice_dir(slug) / TAKE_DIR / f"{beat_id}.wav"


def reference_path(slug: str):
    """A voice sample for cloning the scratch VO. Optional."""
    return voice_dir(slug) / REFERENCE


def narration_config(slug: str) -> dict:
    """The resolved narration binding's config (_tools.json — the bytes the narration task file_deps)."""
    p = beats.tools_path(slug)
    try:
        return (json.loads(p.read_text()).get("narration") or {}).get("config", {}) if p.exists() else {}
    except ValueError:
        return {}


def uses_take(slug: str, beat_id: str) -> bool:
    """A take wins when it exists — unless the series narrates every line with its binding
    (`narration.use_takes: false`, 2026-10-08: the clone reads all of M1). The take stays on disk either way."""
    return take_path(slug, beat_id).exists() and narration_config(slug).get("use_takes", True) is not False


def bridge(command: str, request: dict, timeout: int = 1800) -> dict:
    """Run one command in the voice env. Never imports torch in this process."""
    from . import bridge as bridge_mod
    if not config.voice_ready():
        return {"ok": False, "error": f"voice env missing at {config.VOICE_ROOT}"}
    return bridge_mod.call(config.voice_python(), config.VOICE_BRIDGE,
                           command, request, timeout)


def conform(src, dest, pad: float = 0.0) -> bool:
    """Put a beat's audio in the project's format. No loudness correction.

    Normalising a single 3-second beat is wrong twice over. EBU R128 gates in
    400 ms blocks with a relative threshold, so an integrated measurement of a
    fragment that short is not meaningful — measured on this demo it landed
    5 LU under target. And per-beat correction equalises beats against each
    other, which destroys the intended dynamics between a quiet line and an
    emphatic one.

    Loudness is a property of the finished narration, so it is measured once,
    over the whole assembled track. See normalize_track().
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # `pad` appends silence so the wav can cover a settled picture: when the
    # slug asks the picture to hold after the line, the narration clip on the
    # timeline occupies that same span, and it needs real (silent) samples to
    # occupy it with. Half a second of slack on top keeps the clamp in
    # timeline.build from ever being the thing that decides a duration.
    filters = ["-af", f"apad=pad_dur={pad + 0.5:.3f}"] if pad > 0 else []
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-i", str(src), *filters,
         "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  conform FAILED\n{r.stderr[-500:]}")
        return False
    return True


def normalize_track(src, dest) -> bool:
    """Two-pass EBU R128 over a FULL narration track, as ffmpeg-normalize does.

    Single-pass `loudnorm` measures and corrects in one go and silently falls
    back to DYNAMIC processing — which compresses a voice track — whenever it
    cannot hit the target inside the true-peak ceiling. Measuring first and
    passing the measured values back lets us both avoid that and notice when it
    happens anyway.
    """
    common = (f"I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}")
    probe = subprocess.run(
        [config.ffmpeg(), "-hide_banner", "-nostats", "-i", str(src),
         "-af", f"loudnorm={common}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    measured = {}
    blob = probe.stderr[probe.stderr.rfind("{"):probe.stderr.rfind("}") + 1]
    try:
        measured = json.loads(blob)
    except json.JSONDecodeError:
        pass

    af = f"loudnorm={common}:linear=true"
    if measured:
        af += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
               f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
               f":offset={measured.get('target_offset', 0)}")
        if measured.get("normalization_type", "").lower() == "dynamic":
            print(f"  [note] loudnorm chose dynamic (measured I={measured['input_i']} "
                  f"LRA={measured['input_lra']} TP={measured['input_tp']}) — the track "
                  f"is compressed rather than gain-shifted. Worth a listen.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-i", str(src),
         "-af", af, "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  loudness normalisation FAILED\n{r.stderr[-500:]}")
        return False
    return True


def _denoise(src, dest) -> bool:
    """Spectral denoise for real takes only.

    `afftdn` first, deliberately. ML denoisers (DeepFilterNet) are the second
    reach, not the first — they pump on breaths and sibilants, which is exactly
    what a narration track is made of.
    """
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-i", str(src),
         "-af", "highpass=f=80,afftdn=nf=-25", "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def render(slug: str, beat_id: str) -> bool:
    frag = json.loads(beats.fragment_path(slug, beat_id, "vo").read_text())
    text = (frag.get("vo_text") or "").strip()
    dest = wav_path(slug, beat_id)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not text:
        print(f"[{slug}/{beat_id}] no vo_text — nothing to voice")
        return False

    take, work = take_path(slug, beat_id), dest.with_suffix(".raw.wav")
    if uses_take(slug, beat_id):
        source_type, tool, model, backend = "human", "recording", "", "take"
        # A series can take its recordings untouched (narration.take_denoise: false
        # in the registry, resolved into _tools.json, which this task file_deps).
        ncfg = narration_config(slug)
        if ncfg.get("take_denoise", True) is False:
            r = subprocess.run([config.ffmpeg(), "-y", "-v", "error", "-i", str(take), "-c:a", "pcm_s16le", str(work)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[{slug}/{beat_id}] take copy FAILED")
                return False
        elif not _denoise(take, work):
            print(f"[{slug}/{beat_id}] denoise FAILED")
            return False
    else:
        # Which engine synthesises is read from the RESOLVED FRAGMENT, not
        # from the registry live — _tools.json is what this task file_deps, so
        # the bytes read here and the bytes doit invalidates on are the same
        # bytes. A take still beats every provider: a performance is not a
        # binding.
        from . import bridge as bridge_mod
        binding = {"provider": "local", "config": {}}
        tools_frag = beats.tools_path(slug)
        if tools_frag.exists():
            try:
                binding = (json.loads(tools_frag.read_text()) or {}).get(
                    "narration") or binding
            except ValueError:
                pass

        req = {"text": text, "out": str(work), **binding["config"]}
        if binding["provider"] == "elevenlabs":
            # Neighbouring narration as prosody context: synthesized cold,
            # every beat opens with fresh announcer energy — the most
            # AI-sounding artifact of per-beat synthesis. DELIBERATELY not in
            # this beat's dependency bytes: it is a rendering hint, not
            # content, and folding it in would make every rewording
            # invalidate three beats instead of one.
            try:
                order = json.loads(beats.order_path(slug).read_text())
                i = order.index(beat_id)

                def _neighbour(idx):
                    if 0 <= idx < len(order):
                        p = beats.fragment_path(slug, order[idx], "vo")
                        if p.exists():
                            return json.loads(p.read_text()).get("vo_text", "")
                    return ""
                req.setdefault("previous_text", _neighbour(i - 1))
                req.setdefault("next_text", _neighbour(i + 1))
            except (OSError, ValueError):
                pass
            result = bridge_mod.call(
                config.REPO / ".venv" / "bin" / "python",
                config.REPO / "bridges" / "elevenlabs_render.py",
                "synth", req, timeout=300)
        else:
            # Every other name is the local bridge; the kokoro/chatterbox
            # switch stays inside it, keyed on the reference file.
            ref = reference_path(slug)
            if ref.exists():
                req["reference"] = str(ref)
            result = bridge("synth", req)
        if not result.get("ok"):
            print(f"[{slug}/{beat_id}] synth FAILED: {result.get('error')}")
            return False
        source_type, tool = "model", result["backend"]
        model, backend = result["model"], result["backend"]

    ok = conform(work, dest, pad=beats.timing(slug)["settle"])
    work.unlink(missing_ok=True)
    if not ok:
        return False

    library.write(
        dest,
        library="voice",
        rights_block=library.rights(
            "CC0-1.0" if backend == "take" else "LicenseRef-model-output",
            creator="", attribution_required=False),
        provenance_block=library.provenance(
            tool, source_type=source_type, model=model, stem="narration",
            inputs={"vo_text": text, "beat": beat_id,
                    **({"voice_id": result.get("voice_id"),
                        "voice_settings": binding["config"].get("voice_settings") or {}}
                       if backend == "elevenlabs" else {})}),
        selected=True,
        # Scratch is disposable and must be visibly so: a synthetic track that
        # quietly ships is the failure mode this flag exists to prevent.
        scratch=(backend != "take"),
    )
    print(f"[{slug}/{beat_id}] voice ({backend}) -> {dest.relative_to(config.REPO)}")
    return True
