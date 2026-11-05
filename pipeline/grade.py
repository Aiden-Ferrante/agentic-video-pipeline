"""Grade node: one film stock over the whole programme.

This is the step that makes a cut assembled from a JWST plate, a Commons
microscope slide and a Manim render read as ONE film rather than as three
sources in a row. It is deliberately applied to the CONCATENATED picture, not
per clip, for two reasons:

  * grain must be continuous. Per-clip grain restarts its noise field at every
    cut, and the eye reads that reset as a flicker at each edit.
  * a grade is a property of the programme. Applying it per beat invites beats
    to drift apart, which is the exact problem it exists to solve.

WHY GRAIN IS THE LOAD-BEARING PART. A held still is pixel-identical frame to
frame, and that stillness — not the image quality — is what reads as "dead".
Grain makes every frame differ even when nothing moves. It costs one filter and
it applies to the Manim beats too, which is what pulls the two production
styles into one look.

THE COST, STATED PLAINLY: grain is high-entropy and compresses badly. This pass
re-encodes the picture (the concat is otherwise a stream copy) at a higher
quality than the rest of the pipeline, because grain quantised at the usual cq
turns into visible blocking in the shadows. Keep `grain` modest and expect the
master to grow.

Nothing here is on by default. A slug with no `grade` block in its shot list is
encoded exactly as before.
"""
import json
import subprocess

from . import config

# Sensible film-ish defaults. Every one can be overridden per slug, and any set
# to 0 is omitted from the chain entirely rather than applied as a no-op.
DEFAULTS = {
    "grain": 6.0,          # noise strength; ~4 is subtle, ~12 is a 16mm look
    "lift": 0.020,         # raise pure black — #000 is the most digital thing on screen
    "shadow_tint": 0.004,  # a touch more lift in blue than red: cool shadows
    "contrast": 1.05,
    "saturation": 1.03,
    "vignette": 0.30,      # 0..1, scaled onto a sane angle range
    "halation": 0.20,      # highlight bloom; the big one for nebulae and fire
    "halation_radius": 16.0,
    "aberration": 0.0,     # lateral RGB shift in pixels; 1.0 is already visible
    "aspect": 0.0,         # 0 = keep the project frame; 2.39 = anamorphic crop
}


def spec(slug: str) -> dict | None:
    """The slug's authored grade, or None to leave the picture untouched.

    Read from the derived projection rather than the shot list, so that what
    `master` depends on and what it consumes are the same bytes.
    """
    from . import beats
    path = beats.grade_path(slug)
    block = json.loads(path.read_text()) if path.exists() else None
    if not block or block.get("enabled") is False:
        return None
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in block.items() if k in DEFAULTS})
    return merged


def filter_chain(s: dict) -> str:
    """Build the ffmpeg filtergraph. Order is a real decision, not a detail.

    bloom -> colour -> vignette -> aberration -> grain

    Halation happens first because it models light scattering in the emulsion,
    before any grading of the result. Grain goes LAST because it is the film
    stock the finished image is recorded onto — grading grain after adding it
    would push the noise around with the picture and stop it looking like
    grain.
    """
    stages = []

    if s["aspect"] > 0:
        # FIRST, so everything downstream sees the delivery frame — a vignette
        # computed on 16:9 and then cropped would have its corners cut off and
        # read as an uneven edge darkening rather than a lens.
        #
        # Worth being honest about what this does NOT fix: a portrait-ish plate
        # is pillarboxed by the imagery node, and cropping the TOP and BOTTOM
        # leaves those side bars exactly where they were. It removes letterbox
        # bars on wide plates and it buys the anamorphic look; it is not a
        # remedy for tall source images.
        stages.append(f"crop=iw:2*floor(iw/{s['aspect']:.4f}/2)")

    if s["halation"] > 0:
        # Isolate highlights, blur them wide, screen them back over the source.
        # The lutrgb expression is a soft knee at ~63% so only genuine highlights
        # bloom; a linear multiply would haze the whole frame.
        stages.append(
            f"split[hbase][hglow];"
            f"[hglow]lutrgb="
            f"r='max(0\\,val-160)*2.6':"
            f"g='max(0\\,val-160)*2.6':"
            f"b='max(0\\,val-160)*2.6',"
            f"gblur=sigma={s['halation_radius']:.1f}[hblur];"
            f"[hbase][hblur]blend=all_mode=screen:all_opacity={s['halation']:.3f}"
        )

    if s["lift"] > 0 or s["shadow_tint"] > 0:
        lift, tint = s["lift"], s["shadow_tint"]
        stages.append(
            f"colorlevels=romin={lift:.4f}:gomin={lift + tint * 0.5:.4f}:"
            f"bomin={lift + tint:.4f}"
        )

    if s["contrast"] != 1.0 or s["saturation"] != 1.0:
        stages.append(f"eq=contrast={s['contrast']:.3f}:saturation={s['saturation']:.3f}")

    if s["vignette"] > 0:
        # vignette's angle IS the strength: bigger angle, darker corners.
        angle = 0.10 + 0.42 * min(1.0, s["vignette"])
        stages.append(f"vignette=angle={angle:.3f}:mode=forward")

    if s["aberration"] > 0:
        px = int(round(s["aberration"]))
        if px > 0:
            stages.append(f"rgbashift=rh={px}:bh={-px}")

    if s["grain"] > 0:
        # allf=t is the whole point: TEMPORAL noise, a fresh field every frame.
        # Without t the grain is a fixed pattern welded to the image, which
        # looks like sensor dirt rather than film and does nothing for a still.
        stages.append(f"noise=alls={int(round(s['grain']))}:allf=t+u")

    if not stages:
        return ""

    # EVERY stage runs in planar RGB, and this is not a formality.
    #
    # The picture arrives as yuv420p. Left to itself ffmpeg will happily run
    # `blend=screen` on that directly — and screen blending is 1-(1-a)(1-b),
    # which assumes each channel is linear light in [0,1]. U and V are not:
    # they are colour DIFFERENCE planes centred on 128, where 128 means "no
    # colour". Screening them drives both toward 255 and the picture comes out
    # violently magenta with the green channel crushed to near zero.
    #
    # It is worth knowing this bug reproduces ONLY on video. Testing the same
    # chain against a PNG passes cleanly, because a PNG decodes to RGB and
    # ffmpeg never inserts the yuv path at all.
    return "format=gbrp," + ",".join(stages) + ",format=yuv420p"


def apply(src, dest, s: dict, fps) -> bool:
    """Grade `src` into `dest`. Returns False and leaves dest alone on failure."""
    chain = filter_chain(s)
    if not chain:
        return False
    # cq 16 rather than the pipeline's usual 19: grain is exactly the signal a
    # quantiser throws away first, and losing it here would mean paying the
    # encode cost for nothing.
    r = subprocess.run(
        [config.ffmpeg(), "-y", "-v", "error", "-i", str(src),
         "-filter_complex", chain, "-r", str(fps),
         *config.video_encoder(16),
         "-pix_fmt", "yuv420p", "-an", str(dest)],
        capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        print(f"  grade FAILED\n{r.stderr[-600:]}")
        return False
    return True
