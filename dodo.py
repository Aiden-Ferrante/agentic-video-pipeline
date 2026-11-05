"""The spine. `doit list --status` to see state, `doit` to build everything.

Task enumeration reads the authored shotlist.json (always present), while
dependency tracking hangs off the derived per-node fragments. That split avoids
the chicken-and-egg where beat tasks cannot exist until a task has run.

Each task depends on the narrowest slice it actually reads:

    animation:<slug>/<beat>   beats/<beat>.animation.json + the scene .py
    master:<slug>             beats/_order.json + the clips

so retouching narration rebuilds nothing visual, and editing a Manim scene
rebuilds exactly the beats that use it.

    doit                      build every video
    doit list --all --status  per-beat state: R = run needed, U = up to date
    doit animation:demo/b02   rebuild one beat
    doit clean --clean-all    remove derived artifacts (note: not --all)
"""
import json
from pipeline import (align, animation, assemble, beats, brief, captions, config, finish,
                      fit, imagery, library, memoryline, program, projects, providers, publish,
                      pace, shorts, sound, styles, thumbnail, timeline, voice)

DOIT_CONFIG = {"default_tasks": ["cues", "beats", "tools", "animation", "memoryline", "imagery", "brief", "narration", "align",
                                 "fit", "timeline", "music", "captions", "master",
                                 "short", "thumbnail", "publish", "program"],
               "verbosity": 2}


def task_beats():
    """Split each authored shotlist into per-beat, per-node fragments."""
    for slug in config.slugs():
        targets = [beats.order_path(slug), beats.grade_path(slug)]
        for bid in beats.beat_ids(slug):
            targets += [beats.fragment_path(slug, bid, n) for n in beats.PROJECTIONS]
        # The style file is a CONDITIONAL dep, the task_tools precedent: the
        # fragments carry the style's grade/letterbox/pace, so the bytes that
        # decide them must be able to invalidate this one cheap task.
        style = styles.style_file(slug)
        yield {
            "name": slug,
            "file_dep": [config.video_dir(slug) / "shotlist.json",
                         *([style] if style else [])],
            "targets": targets,
            "actions": [(beats.split, [slug])],
            "clean": True,
        }


def task_animation():
    """Render one Manim clip per animation beat, keyed by beat id."""
    for slug in config.slugs():
        for beat in beats.of_kind(slug, "animation"):
            bid = beat["id"]
            yield {
                "name": f"{slug}/{bid}",
                # The projection carries what to draw; the .py carries how it
                # looks. Either changing must re-render this beat and only this one.
                "file_dep": [beats.fragment_path(slug, bid, "animation"),
                             *animation.scene_sources(beat)],
                # The sidecar is a real product of this task, not a stray file:
                # declaring it means `doit clean` cannot leave provenance
                # describing a clip that no longer exists.
                "targets": [config.asset_path(slug, bid),
                            library.sidecar_path(config.asset_path(slug, bid))],
                "task_dep": [f"beats:{slug}"],
                "actions": [(animation.render, [slug, bid])],
                "clean": True,
            }


def task_memoryline():
    """Render one memory-line clip per memoryline beat, timed to its narration.

    Unlike animation, the timing authority is a dep: the shot syncs to the words.
    It is chosen exactly as task_fit chooses it (alignment when voiced, else the vo
    fragment), and is UNCONDITIONAL for fit's reason — a dep set that changes
    between runs never lets the pipeline come to rest.
    """
    for slug in config.slugs():
        voiced = beats.needs_voice(slug)
        narrated = set(beats.narrated_ids(slug))
        for beat in beats.of_kind(slug, "memoryline"):
            bid = beat["id"]
            timing_dep = (align.align_path(slug, bid) if voiced and bid in narrated
                          else beats.fragment_path(slug, bid, "vo"))
            yield {
                "name": f"{slug}/{bid}",
                "file_dep": [beats.fragment_path(slug, bid, "memoryline"), timing_dep,
                             *memoryline.kit_sources_for(beats.visual_spec(beat)),
                             *memoryline.chain_deps(slug, beats.visual_spec(beat))],
                "targets": [config.asset_path(slug, bid),
                            library.sidecar_path(config.asset_path(slug, bid))],
                "task_dep": [f"beats:{slug}"],
                "actions": [(memoryline.render, [slug, bid])],
                "clean": True,
            }


def task_imagery():
    """Fetch or generate a still for each imagery beat, held as a clip."""
    for slug in config.slugs():
        for beat in beats.of_kind(slug, "imagery"):
            bid = beat["id"]
            yield {
                "name": f"{slug}/{bid}",
                "file_dep": [beats.fragment_path(slug, bid, "imagery")],
                "targets": [config.asset_path(slug, bid),
                            library.sidecar_path(config.asset_path(slug, bid)),
                            imagery.still_path(slug, bid)],
                "task_dep": [f"beats:{slug}"],
                "actions": [(imagery.render, [slug, bid])],
                "clean": True,
            }


def task_brief():
    """Typeset a placeholder card for each beat whose picture is still prose.

    The script compiler emits `visual.kind: "brief"` for a new beat; this task
    is why that kind may sit in CLIP_KINDS — the animatic builds with the hole
    labelled rather than not building at all.
    """
    for slug in config.slugs():
        for beat in beats.of_kind(slug, "brief"):
            bid = beat["id"]
            yield {
                "name": f"{slug}/{bid}",
                "file_dep": [beats.fragment_path(slug, bid, "brief")],
                "targets": [config.asset_path(slug, bid),
                            library.sidecar_path(config.asset_path(slug, bid)),
                            brief.png_path(slug, bid)],
                "task_dep": [f"beats:{slug}"],
                "actions": [(brief.render, [slug, bid])],
                "clean": True,
            }


def task_tools():
    """Resolve each slug's per-stem provider bindings into beats/_tools.json.

    The registry file is a CONDITIONAL dep — the accepted pattern from
    task_master's edit.otio: a slug gaining or losing an owning project changes
    this one cheap task's dep set and nothing else. The fragment itself is
    written unconditionally (defaults when unowned), so consumers can dep it
    unconditionally and the dep-set-stability rule holds where it matters.
    """
    for slug in config.slugs():
        registry = providers.registry_file(slug)
        style = styles.style_file(slug)   # delivery defaults ride in _tools.json
        yield {
            "name": slug,
            "file_dep": [*([registry] if registry else []), *([style] if style else [])],
            "targets": [beats.tools_path(slug)],
            "task_dep": [f"beats:{slug}"],
            "actions": [(providers.write_fragment, [slug])],
            "clean": True,
        }


def task_narration():
    """Narrate one beat: a recorded take if there is one, else synthesis.

    Skipped entirely for a slug timed from the script (timing.mode ==
    "estimate") — there is no narration to make.
    """
    for slug in config.slugs():
        if not beats.needs_voice(slug):
            continue
        for bid in beats.narrated_ids(slug):
            # A take (or a cloning reference) is optional, so it can only be a
            # dependency once it exists. doit re-runs the task when the dep SET
            # changes, so dropping a recording in still invalidates the scratch.
            optional = [p for p in (voice.take_path(slug, bid),
                                    voice.reference_path(slug)) if p.exists()]
            yield {
                "name": f"{slug}/{bid}",
                # _tools.json unconditionally: which provider synthesises this
                # beat is part of the bytes that decide whether to re-run it.
                "file_dep": [beats.fragment_path(slug, bid, "vo"),
                             beats.tools_path(slug), *optional],
                "targets": [voice.wav_path(slug, bid),
                            library.sidecar_path(voice.wav_path(slug, bid))],
                "task_dep": [f"beats:{slug}", f"tools:{slug}"],
                "actions": [(voice.render, [slug, bid])],
                "clean": True,
            }


def task_align():
    """Word-level timings for a beat, from its own audio and its own text."""
    for slug in config.slugs():
        if not beats.needs_voice(slug):
            continue
        for bid in beats.narrated_ids(slug):
            yield {
                "name": f"{slug}/{bid}",
                "file_dep": [voice.wav_path(slug, bid),
                             beats.fragment_path(slug, bid, "vo")],
                "targets": [pace.raw_align_path(slug, bid)],
                "actions": [(align.build, [slug, bid])],
                "clean": True,
            }


def task_pacing():
    """Resolve each voiced slug's pause rules (style `timing.pauses` + beats' `pause`/`hold`) into _pacing.json."""
    for slug in config.slugs():
        if not beats.needs_voice(slug):
            continue
        style = styles.style_file(slug)
        yield {
            "name": slug,
            "file_dep": [config.video_dir(slug) / "shotlist.json", *([style] if style else [])],
            "targets": [pace.pacing_path(slug)],
            "task_dep": [f"beats:{slug}"],
            "actions": [(pace.write_fragment, [slug])],
            "clean": True,
        }


def task_pace():
    """Pauses put into each beat's read — never re-voiced. Writes the paced wav and the alignment downstream reads."""
    for slug in config.slugs():
        if not beats.needs_voice(slug):
            continue
        for bid in beats.narrated_ids(slug):
            yield {
                "name": f"{slug}/{bid}",
                "file_dep": [voice.wav_path(slug, bid), pace.raw_align_path(slug, bid),
                             beats.fragment_path(slug, bid, "vo"), pace.pacing_path(slug), pace.__file__],
                "targets": [voice.spoken_path(slug, bid), align.align_path(slug, bid)],
                "actions": [(pace.build, [slug, bid])],
                "clean": True,
            }


def task_fit():
    """Stretch or cut each clip to its narration. VO duration is the contract."""
    for slug in config.slugs():
        voiced = beats.needs_voice(slug)
        narrated = set(beats.narrated_ids(slug))
        for bid in beats.clip_ids(slug):
            # What the length is read FROM is what this task depends on. With a
            # voice track that is the alignment; timed from the script it is the
            # narration text itself, so that rewording a line retimes its
            # picture. Depending on the alignment in estimate mode would demand
            # a target no task produces and fail the build.
            # A silent beat (a TOC card) has no alignment in ANY mode — its
            # timing authority is its vo fragment (empty text -> the render's
            # own length), exactly like estimate mode.
            timing_dep = (align.align_path(slug, bid) if voiced and bid in narrated
                          else beats.fragment_path(slug, bid, "vo"))
            yield {
                "name": f"{slug}/{bid}",
                # Both deps are UNCONDITIONAL even though the alignment does not
                # exist on a first run. `align` declares it as a target, so doit
                # orders this after it. Adding the dep only once the file exists
                # would change the dependency SET between runs, which doit reads
                # as a change — so the task would be stale immediately after a
                # successful build and the pipeline would never come to rest.
                "file_dep": [config.asset_path(slug, bid), timing_dep],
                "targets": [fit.fit_path(slug, bid)],
                "actions": [(fit.build, [slug, bid])],
                "clean": True,
            }


def task_timeline():
    """Emit out/<slug>.otio — the document the master is rendered from."""
    for slug in config.slugs():
        yield {
            "name": slug,
            # Unconditional for the same reason as fit: `voice` targets every
            # beat's wav, so a conditional dep would only make this task stale
            # forever after the first build.
            # _music.json: the timeline now places a music-stem clip when a bed
            # is authored, so the authored block is part of this task's bytes.
            "file_dep": [config.video_dir(slug) / "shotlist.json",
                         beats.music_path(slug),
                         *[fit.fit_path(slug, b) for b in beats.clip_ids(slug)],
                         *([voice.spoken_path(slug, b) for b in beats.narrated_ids(slug)]
                           if beats.needs_voice(slug) else [])],
            "targets": [timeline.otio_path(slug)],
            "task_dep": [f"beats:{slug}"],
            "actions": [(timeline.build, [slug])],
            "clean": True,
        }


def task_music():
    """Lay the requested music bed under the whole programme."""
    for slug in config.slugs():
        block = sound.spec(slug)
        if not block:
            continue
        src = sound.source_for(block["bed"])
        if src is None:
            continue
        yield {
            "name": slug,
            # The bed's own sidecar is a dependency: correcting a licence must
            # rewrite the derived copy's rights, which are what credits() reads.
            # The music fragment is the authored gain/fade/bed choice — without
            # it, editing gain_db rebuilt nothing (found in the stems audit).
            "file_dep": [src, library.sidecar_path(src), timeline.otio_path(slug),
                         beats.music_path(slug)],
            "targets": [sound.bed_path(slug),
                        library.sidecar_path(sound.bed_path(slug))],
            "actions": [(sound.build, [slug])],
            "clean": True,
        }


def task_captions():
    """Subtitles from the alignments, positioned on the finished timeline.

    Word-level timings are the only honest source for a subtitle, and they come
    from a real alignment. A slug timed from the script has none, so it gets no
    captions rather than captions invented from an estimate.
    """
    for slug in config.slugs():
        if not beats.needs_voice(slug):
            continue
        yield {
            "name": slug,
            "file_dep": [timeline.otio_path(slug),
                         *[align.align_path(slug, b) for b in beats.narrated_ids(slug)]],
            "targets": [captions.ass_path(slug), captions.srt_path(slug)],
            "actions": [(captions.build, [slug])],
            "clean": True,
        }


def task_short():
    """One vertical cut per authored Short. Re-rendered at 9:16, never cropped."""
    for slug in config.slugs():
        # A Short is cut from alignments and voice takes; neither exists for a
        # slug timed from the script.
        if not beats.needs_voice(slug):
            continue
        for spec in shorts.specs(slug):
            name = spec["name"]
            yield {
                "name": f"{slug}/{name}",
                # The shot list carries the beat selection; the fitted clips and
                # alignments carry the timing. All three must invalidate it.
                "file_dep": [config.video_dir(slug) / "shotlist.json",
                             *[align.align_path(slug, b) for b in spec["beats"]
                               if b in beats.beat_ids(slug)],
                             *[voice.spoken_path(slug, b) for b in spec["beats"]
                               if b in beats.beat_ids(slug)]],
                "targets": [shorts.short_path(slug, name),
                            library.sidecar_path(shorts.short_path(slug, name))],
                "actions": [(shorts.build, [slug, name])],
                # The per-Short scratch dir holds the reframed beats; it is
                # rebuildable and must not outlive a clean.
                "clean": [f"rm -rf {shorts.out_dir(slug) / ('_' + name)}",
                          f"rm -f {shorts.short_path(slug, name)} "
                          f"{library.sidecar_path(shorts.short_path(slug, name))}"],
            }


def task_thumbnail():
    """Typography over an optional backplate, rasterised to out/thumbnail.png."""
    for slug in config.slugs():
        yield {
            "name": slug,
            "file_dep": [config.video_dir(slug) / "shotlist.json"],
            "targets": [thumbnail.thumbnail_path(slug), thumbnail.svg_path(slug),
                        library.sidecar_path(thumbnail.thumbnail_path(slug))],
            "actions": [(thumbnail.build, [slug])],
            # backplate.png is a frame pulled from a clip, not a declared
            # target, so it needs removing by hand.
            "clean": [f"rm -f {thumbnail.thumbnail_path(slug)} "
                      f"{thumbnail.svg_path(slug)} "
                      f"{library.sidecar_path(thumbnail.thumbnail_path(slug))} "
                      f"{config.video_dir(slug) / 'out' / 'backplate.png'}"],
        }


def task_publish():
    """Everything the upload form needs. The upload itself stays manual."""
    for slug in config.slugs():
        yield {
            "name": slug,
            "file_dep": [assemble.master_path(slug), timeline.otio_path(slug)],
            "targets": [publish.publish_path(slug)],
            "actions": [(publish.build, [slug])],
            "clean": True,
        }


def task_master():
    """Render the authoritative timeline into out/master.mp4.

    Rendered FROM the timeline, so an agent retime or a Kdenlive polish reaches
    the file you actually watch. When a promoted edit copy exists it governs, so
    it is a dependency too.
    """
    for slug in config.slugs():
        # Three kinds of input, and all three must invalidate:
        #
        #   the timeline   the EDIT — order, trims, durations
        #   the grade      touches nothing else the master reads
        #   the clips      the PICTURE itself
        #
        # The clips were once left out on the reasoning that the timeline
        # governs. It governs the edit, but the master is rendered from the
        # media the timeline points at — so a clip whose CONTENT changed while
        # its DURATION did not leaves the .otio byte-identical and the master
        # silently stale. That is not hypothetical: swapping thirteen stills
        # from depth rack focus to generated motion produced exactly the same
        # durations, doit correctly saw no dependency change, and the master on
        # disk stayed an hour older than the footage in it.
        deps = [timeline.otio_path(slug), beats.grade_path(slug),
                *[fit.fit_path(slug, b) for b in beats.clip_ids(slug)]]
        if timeline.edit_path(slug).exists():
            deps.append(timeline.edit_path(slug))
        if sound.bed_path(slug).exists():
            deps.append(sound.bed_path(slug))
        out = assemble.master_path(slug).parent
        yield {
            "name": slug,
            "file_dep": deps,
            "targets": [assemble.master_path(slug),
                        library.sidecar_path(assemble.master_path(slug))],
            "actions": [(assemble.build, [slug])],
            # The render leaves working files beside the master. They are worth
            # keeping for a post-mortem but must not survive a clean.
            "clean": [f"rm -rf {out / 'segments'}",
                      f"rm -f {out / 'picture.mp4'} {out / 'voice.wav'} "
                      f"{out / 'concat.txt'} {assemble.master_path(slug)} "
                      f"{library.sidecar_path(assemble.master_path(slug))}"],
        }


def task_program():
    """Compose each project's modules into one editable timeline.

    Last in the default order because it reads what every module produced. It
    depends on each module's AUTHORITATIVE timeline, so promoting a module to a
    hand-owned edit re-composes the project rather than being ignored — the
    failure mode worth avoiding is a project view that quietly shows the
    machine's cut of a module you have already edited by hand.
    """
    for proj in projects.load():
        deps = [timeline.authoritative_path(m["slug"])
                for m in proj["modules"] if not m.get("missing")]
        if not deps:
            continue
        yield {
            "name": proj["id"],
            # The project file is a dependency in its own right: reordering
            # modules changes the composition without any module changing. It
            # lives OUTSIDE this repo (content-manager owns the registry), which
            # doit handles fine — a file_dep is a path, not a repo member.
            "file_dep": [projects.PROJECTS / f"{proj['id']}.json", *deps],
            "targets": [program.otio_path(proj["id"])],
            "task_dep": [f"timeline:{m['slug']}"
                         for m in proj["modules"] if not m.get("missing")],
            "actions": [(program.build, [proj["id"]])],
            "clean": True,
        }


# ---------------------------------------------------------------- finish stage
# The film the human watches: acts (picture + captions), a generated score per act, word-cued SFX, the final
# voice over the score, the acts joined with dips to black. Scripts in finish/, questions in pipeline/finish.py.
# Opt in per video: "finish": {"enabled": true} in the shot list.  `doit film:<slug>` builds everything upstream.

def task_cues():
    """Refuse a sound-effect cue whose word is no longer said — FIRST, before anything renders."""
    for slug in config.slugs():
        if not finish.enabled(slug):
            continue

        def check(slug=slug):
            bad = finish.cue_problems(slug)
            for line in bad:
                print(f"[{slug}] cue word missing: {line}")
            return not bad
        yield {
            "name": slug,
            "file_dep": [config.video_dir(slug) / "shotlist.json"],
            "actions": [check],
        }


def task_card():
    """Each act's title card and held last frame — clips in the act like beats (finish.acts). Only music crosses them."""
    from doit.tools import config_changed
    for slug in config.slugs():
        if not finish.enabled(slug) or not finish.card_settings(slug):
            continue
        for a in finish.acts(slug):
            n = a["n"]
            if finish.card_id(n) in a["ids"]:          # act one has none under `skip_first`
                yield {"name": f"{slug}/{finish.card_id(n)}",
                       "targets": [fit.fit_path(slug, finish.card_id(n))],
                       "uptodate": [config_changed(json.dumps(finish.card_spec(slug, a), sort_keys=True))],
                       "actions": [(finish.render_card, [slug, n])], "clean": True}
            yield {"name": f"{slug}/{finish.tail_id(n)}",
                   "file_dep": [fit.fit_path(slug, a["ids"][-2])],
                   "targets": [fit.fit_path(slug, finish.tail_id(n))],
                   "uptodate": [config_changed(str(finish.card_spec(slug, a)["tail"]))],
                   "actions": [(finish.render_tail, [slug, n])], "clean": True}


def task_acts():
    """Split a finishing video into acts; resolve the template and the video's score, cues and beds per act."""
    for slug in config.slugs():
        if not finish.enabled(slug):
            continue
        acts = finish.acts(slug)
        style = styles.style_file(slug)
        yield {
            "name": slug,
            "task_dep": [f"cues:{slug}"],
            "file_dep": [*finish.fit_deps(slug, [b for a in acts for b in a["ids"]]), finish.SCRIPTS / "acts_plan.py",
                         config.video_dir(slug) / "shotlist.json", *([style] if style else [])],
            "targets": [finish.acts_json(slug), finish.settings_path(slug),
                        *[finish.timing_path(slug, a["slug"]) for a in acts],
                        *[finish.cues_path(slug, a["slug"]) for a in acts]],
            "actions": [(finish.run, [slug, "acts_plan.py"])],
            "clean": True,
        }


def task_score():
    """Each act's score (Stable Audio). Depends on the act's timing file — its length, beats and score spec —
    never its picture. A heard score is kept, cut or continued, never regenerated (finish/score.py)."""
    for slug in config.slugs():
        if not finish.enabled(slug):
            continue
        for a in finish.acts(slug):
            yield {
                "name": f"{slug}/{a['slug']}",
                "file_dep": [finish.timing_path(slug, a["slug"]), finish.SCRIPTS / "score.py", finish.BASE_TAKE],
                "targets": [finish.score_path(slug, a["slug"])],
                "actions": [(finish.run, [slug, "score.py", a["slug"]], {"python": finish.stable_audio_python()})],
                "clean": True,
            }


def task_actpic():
    """An act's picture with its captions burned in (the input the mix scores)."""
    for slug in config.slugs():
        if not finish.enabled(slug):
            continue
        for a in finish.acts(slug):
            yield {
                "name": f"{slug}/{a['slug']}",
                "file_dep": [*finish.fit_deps(slug, a["ids"]), *finish.voice_deps(slug, a["ids"]),
                             *[beats.fragment_path(slug, b, "vo") for b in a["ids"] if not finish.is_card(b)], finish.acts_json(slug),
                             finish.settings_path(slug),
                             *[finish.SCRIPTS / f for f in ("act_picture.py", "actcut.py", "captions.py")]],
                "targets": [finish.narrated_path(slug, a["slug"])],
                "actions": [(finish.run, [slug, "act_picture.py", a["n"]])],
                "clean": True,
            }


def task_actmix():
    """Score + word-cued SFX under the act's picture, then the final voice over it, the score ducking."""
    for slug in config.slugs():
        if not finish.enabled(slug):
            continue
        kit = sorted(finish.KIT.glob("*.flac"))
        layout = config.MEMORYLINE_KIT / "shots" / "four_slots_act1_layout.py"   # sfx_mix's curve blips read it
        for a in finish.acts(slug):
            yield {
                "name": f"{slug}/{a['slug']}",
                "file_dep": [finish.narrated_path(slug, a["slug"]), finish.score_path(slug, a["slug"]),
                             *finish.voice_deps(slug, a["ids"]), *kit, finish.acts_json(slug),
                             finish.settings_path(slug), finish.cues_path(slug, a["slug"]),
                             *[finish.SCRIPTS / f for f in ("sfx_mix.py", "act_voice.py")],
                             *([layout] if layout.exists() else [])],
                "targets": [finish.act_path(slug, a["slug"])],
                "actions": [(finish.run, [slug, "sfx_mix.py", a["n"]], {"python": finish.stable_audio_python()}),
                            (finish.run, [slug, "act_voice.py", a["n"]])],
                "clean": True,
            }


def task_film():
    """The whole film: every act, joined with 2.5 s dips through black, the scores crossfading under them."""
    for slug in config.slugs():
        if not finish.enabled(slug):
            continue
        yield {
            "name": slug,
            "task_dep": [f"cues:{slug}"],          # first: a broken cue fails before anything renders
            "file_dep": [*[finish.act_path(slug, a["slug"]) for a in finish.acts(slug)], finish.SCRIPTS / "film.py",
                         finish.settings_path(slug)],
            "targets": [finish.film_path(slug)],
            "actions": [(finish.run, [slug, "film.py"])],
            "clean": True,
        }
