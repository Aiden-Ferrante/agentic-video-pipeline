"""Fit node: a beat's clip becomes exactly as long as its narration.

The contract, decided 2026-08-19: VO duration wins, picture yields. A beat is
as long as it takes to say, and the animation is stretched or cut to match.

Why this is a real artifact rather than a number on the timeline. If the clip
stayed at its rendered length and the timeline merely asked for a different
duration, then source_range would exceed available_range — a timeline that
claims more media than exists. Kdenlive clamps it, the round trip loses the
intent, and the master and the editor disagree about how long a beat is.
Producing media that is genuinely the right length keeps
source_range == available_range, so every consumer sees the same thing.

Short animation: hold the last frame. Long animation: cut it. Neither is
retiming — the motion plays at its authored speed, which is what makes an
easing curve look right.
"""
import json
import subprocess

from . import align, config

# Under this, the render already matches the narration and re-encoding it would
# only cost quality. One frame at 30fps is 0.033s.
TOLERANCE = 0.04


def fit_path(slug: str, beat_id: str):
    return config.video_dir(slug) / "assets" / f"{beat_id}.fit.mp4"


def target_seconds(slug: str, beat_id: str) -> float:
    """How long the beat should be, in the slug's declared timing authority.

    The contract is unchanged — narration wins, picture yields — but the
    narration's length can now come from the script rather than from audio, so
    a film can be cut before a single word is recorded. Resolution order:

        alignment   a real take, measured           (auto / voice)
        estimate    the read time of the text       (auto / estimate)
        the render  when the beat has no words at all

    This is the ONE place the authority is decided; every consumer — fit, and
    the resync polish in the MCP server — must route through here or they will
    disagree about how long a beat is.
    """
    from . import assemble, beats, reading

    mode = beats.timing(slug)
    # `settle` rides on top of the NARRATED length only: the picture holds for
    # a breath after the line lands, so an idea has silence to sink in before
    # the next one starts. A silent beat (the fallback below) keeps exactly its
    # rendered length — a card already holds for as long as it animates.
    if mode["mode"] != "estimate":
        spoken = align.spoken_seconds(slug, beat_id)
        if spoken > 0:
            return spoken + mode["settle"] + beats.beat_settle(slug, beat_id)

    if mode["mode"] != "voice":
        frag_path = beats.fragment_path(slug, beat_id, "vo")
        if frag_path.exists():
            estimated = reading.estimate(
                json.loads(frag_path.read_text()).get("vo_text", ""), mode["pace"])
            if estimated > 0:
                return estimated + mode["settle"] + beats.beat_settle(slug, beat_id)

    return assemble.duration(config.asset_path(slug, beat_id))


def build(slug: str, beat_id: str) -> bool:
    from . import assemble
    src = config.asset_path(slug, beat_id)
    dest = fit_path(slug, beat_id)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        print(f"[{slug}/{beat_id}] no clip to fit")
        return False

    have, want = assemble.duration(src), target_seconds(slug, beat_id)
    if want <= 0:
        print(f"[{slug}/{beat_id}] no target duration")
        return False

    if abs(have - want) <= TOLERANCE:
        # Already the right length: copy the bytes rather than re-encode them.
        r = subprocess.run(
            [config.ffmpeg(), "-y", "-v", "error", "-i", str(src),
             "-c", "copy", str(dest)], capture_output=True, text=True)
        verb = "matched"
    else:
        # tpad clones the final frame when we need more, trim cuts when we need
        # less; -t bounds both. Encoded on the GPU — NVENC is present on the GB10.
        r = subprocess.run(
            [config.ffmpeg(), "-y", "-v", "error", "-i", str(src),
             "-vf", f"tpad=stop_mode=clone:stop_duration={want:.3f}",
             "-t", f"{want:.3f}", "-an",
             *config.video_encoder(19), "-pix_fmt", "yuv420p",
             str(dest)], capture_output=True, text=True)
        verb = "held" if want > have else "cut"

    if r.returncode != 0 or not dest.exists():
        print(f"[{slug}/{beat_id}] fit FAILED\n{r.stderr[-500:]}")
        return False

    got = assemble.duration(dest)
    print(f"[{slug}/{beat_id}] {verb} {have:.2f}s -> {got:.2f}s (narration {want:.2f}s)")
    return True
