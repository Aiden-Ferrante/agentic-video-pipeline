# Backlog

The queue Development Protocol 1.0 assumes and this repo has never had. Three
things land here and nothing else:

- **Pruned children** — scope cut out of a cycle, written down at the moment it
  was cut. A pruned list assembled afterwards is a description of what you
  happened not to do.
- **ADR seeds** — a decision that wanted an ADR and didn't get one yet.
- **Retro items** — "what carries forward" from a closed cycle.

An item here is not scheduled. The next light plan pulls from this file; it does
not add to it mid-build.

## Pruned

- **Stems (2026-08-26), cut from the arc on purpose:**
  - shorts.py rebased onto the timeline — shorts re-render at 9:16 and the
    timeline references 16:9 fitted media, so a rebase needs a parallel 9:16
    render set (the same blocker derive_short records). The vocabulary fork is
    already closed (shorts reads beats.visual_spec); the rebase is its own cycle.
  - per-beat music cues — reserved in the schema, warned on, no task.
  - narration-over-black beats — visuals stays the carrier stem for now.
  - directory/library renames (voice/ -> narration/ etc.) — mapped, not
    renamed; renaming churns 160+ sidecars for zero behavioural gain.
  - imagery consuming _tools.json — waits for a second resolvable imagery
    provider; a dep change would re-run unlocked i2v beats at 25-45 min each.
  - estimate-mode captions — captions stay honestly voiced-only.

## ADR seeds

- **A malformed registry file is a silent provider downgrade.** `projects.load()`
  skips an unparseable project file so one typo cannot blank the canvas — and in
  doing so every slug it owned resolves to the built-in providers, `_tools.json`
  changes bytes, and the next `doit` re-synthesises every voiced beat (2026-09-01:
  445 tasks stale from a stray `\n`). Wants a decision: `task_tools` refuses
  (not warns) when a slug's owning file exists but cannot be parsed, or the
  ElevenLabs spend cap counts a provider *change* as intent. The cap already
  bounds the bill; it does not bound the invalidation.
- **`fit` reads pace and settle live.** `beats.timing()` is not in any fragment
  fit depends on, so changing a style's (or a shot list's) `pace` invalidates
  imagery beats (their fragment carries `seconds`) but not fit. Adding a
  `_timing.json` dep to fit would re-fit ~1000 beats once. Own cycle, after a
  master needs re-rendering anyway.
- **`_note` keys ride into fragments.** A prose edit to a `grade._note` rebuilds
  the master. Either strip `_`-prefixed keys at projection time (one invalidation
  to adopt) or accept it as the cost of notes living beside numbers.

- **Who may write shotlist.json (The Studio D1, applied 2026-09-01).** The
  shot list stays the only hand-authored file, and tools edit it in place by
  beat id under a merge rule: `pool.commit` owns a beat's visual recipe,
  `screenplay.compile` owns narration, order and `brief`. Neither is a doit
  task — a task whose target is the authored file would `clean` it. Wants an
  ADR so the next tool that edits the shot list joins the rule rather than
  inventing one.
- **`task_timeline` and `task_thumbnail` file_dep the whole shotlist.json.**
  Adding `brief` fields to 58 beats invalidated both (cheap, and the master
  stayed fresh because the timeline bytes were identical) — but the same
  coarse dep means a thumbnail rebuild on every reword. The fix is the
  `_grade.json` pattern: project what each actually reads (`_order` + timing
  for the timeline; the `thumbnail` block for the thumbnail).
- **Silent VIDEO-only blocks always get a fresh id on adopt.** They cannot be
  matched by text, so a cold-open shot described in a screenplay becomes a
  new `brief` beat beside the existing `cNN` shot. Re-keying by hand is the
  current answer; a `--key r59=c03` option on adopt is the obvious next one.

- **A conditional file_dep means "correct only on the second run."** task_master
  deps `bed_path` only when it already exists, so the 2026-08-27 series render
  produced five masters WITHOUT their music, reported success, and only the
  canvas's stale badge caught it. Shipping a silent master while reporting
  success is the exact failure the assemble node guards against elsewhere. The
  same shape applies to `edit.otio`. Options: make the dep unconditional (needs
  a null-fragment for the bed, the _grade.json pattern), or order the tasks so
  music always precedes master. Wants a decision, not a patch.

## Carries forward

- ~~doit 408/408 stale after the merge~~ — CLOSED 2026-08-26: rebuilt once in place
  (v0.1.2). The rebuild also surfaced the VOICE_ROOT/VISUAL_ROOT path bug, which a
  reconcile-without-rebuilding would have left latent until the first voiced slug.

## Retro · v0.14.0 (2026-09-22)

- The master's chapter tag overlaps the Triptych's maths panel (top right) — hide the tag on `fusion:*` beats or
  move the panel.
- `doit master:four-slots` re-rendered 59 Manim beats whose inputs had not changed; sidecar diffs were timestamps
  only. Find why before the next full build (dep checker? scene-source closure?).
- loudnorm chose "dynamic" on the new master (I = −23.33, LRA 3.0) — the narration is compressed; listen.
- `fleet/check_fleet.py` red on two items outside this repo's cycles: agent docs stale in all eight repos
  (fleet a997267 not re-rendered), and `four-slots` chapter_tag `M1` is not an AI 101 section key.
- CHANGELOG: v0.12.0 and v0.13.0 are tagged, but v0.12.0's entry still reads "Unreleased" and v0.13.0 has none.
- Two stray 0-byte files at the repo root (`Advanced.`, `General`, 2026-09-05) — the human's to delete or keep.
- `video-pipeline/PROJECT.yaml` is uncharted in shipyard (a hook asks for a problem with a waypoint).
- **ADR seed:** the `memoryline` node depends on its beat's timing authority (alignment / vo fragment), breaking
  "a node reads only its own fragment" on purpose — shots cue events to words. Written in the module; wants an ADR.

## Retro · v0.15.0 (2026-10-08)

- The 82-beat drawn/typeset/layered table was never written (Acts II–X moved under the rules directly). Write it
  if a later review wants the reasons per beat.
- Side-by-side v0.15.0 vs v0.14.0 not made.
- CHANGELOG v0.12.0 heading fixed; v0.13.0 still has no entry.
- Stray `Advanced.` / `General` (0 bytes) removed by the agent while closing the cycle under the human's goal.
- **ADR seed:** the finished film (score, SFX, acts, voice mix, join) is built outside doit by hand-run scripts in
  gitignored `out/ml/` + untracked `stable-audio-3/outputs/`. → STEPWISE REFINEMENT — The Line.
- A light cycle stayed open 16 days and absorbed five script drafts. Cut sooner.

## Retro · v0.24.0 (2026-10-11)

- **Pruned at close:** upload-on-approve and the 48 h post-mortem, both waiting on the human's YouTube OAuth client
  (README "One-time: YouTube sign-in"). Re-verify both once it exists.
- Same-day pair of timer-run Shorts (06:30 + 14:30) not yet observed. Check `audience/data/runs/`.
- Producer rule: every Short's drawings are new (the human, 2026-10-11: "the visuals need to be 100% orignal not
  reused"). Add new motifs per Short; don't reuse library ones.
- ADR seed: register the review page (:8977) in mission-control, or fold it into content-manager.
