"""The stem mix graph and the program's per-stem composition.

_mix_graph is pure — no subprocess, no media — precisely so its shape can be
pinned here: the graph IS the mixing policy (speech at unity, beds ducked
under speech, everything trimmed to the picture) and a policy you cannot test
is a policy that drifts.
"""
import opentimelineio as otio

from pipeline import assemble, program, stems


class TestMixGraph:
    def test_narration_only_is_the_historical_path(self):
        g = assemble._mix_graph(2, 0, [(0.0, 0.0, 3.0), (0.0, 3.0, 4.0)], 7.0)
        assert "amix=inputs=2" in g
        assert "sidechaincompress" not in g
        assert "atrim=duration=7.000[out]" in g

    def test_speech_plus_bed_ducks_the_bed_under_the_speech(self):
        g = assemble._mix_graph(1, 1, [(0.0, 0.0, 3.0), (0.0, 0.0, 7.0)], 7.0)
        assert "sidechaincompress" in g
        # the speech sum is the sidechain key, the bed the target
        assert "[vox]asplit=2[voxout][trigger]" in g
        assert "[bedmix][trigger]sidechaincompress" in g

    def test_bed_alone_plays_unducked(self):
        """A sidechain needs a key; music with no narration just plays."""
        g = assemble._mix_graph(0, 1, [(0.0, 0.0, 7.0)], 7.0)
        assert "sidechaincompress" not in g
        assert "amix=inputs=1" in g

    def test_audio_never_outlives_the_picture(self):
        g = assemble._mix_graph(1, 1, [(0.0, 0.0, 3.0), (0.0, 0.0, 99.0)], 7.0)
        assert "apad,atrim=duration=7.000[out]" in g

    def test_empty_timeline_mixes_to_none_not_failure(self, videos):
        """None is 'legitimately silent'; False is 'the mix broke'. Collapsing
        them would let a broken mix ship a silent master as success."""
        tl = otio.schema.Timeline(name="fx")
        tl.tracks.append(otio.schema.Track(
            name="visuals", kind=otio.schema.TrackKind.Video,
            metadata={"pipeline": {"stem": "visuals", "layout": "beats"}}))
        assert assemble._mix_audio("fx", tl, None) is None


def _module_tl(name, beats_secs, with_music=False, fps=30):
    def span(sec):
        return otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, fps),
            duration=otio.opentime.RationalTime(round(sec * fps), fps))

    tl = otio.schema.Timeline(name=name)
    video = otio.schema.Track(
        name="visuals", kind=otio.schema.TrackKind.Video,
        metadata={"pipeline": {"stem": "visuals", "layout": "beats"}})
    audio = otio.schema.Track(
        name="narration", kind=otio.schema.TrackKind.Audio,
        metadata={"pipeline": {"stem": "narration", "layout": "beats"}})
    for bid, sec in beats_secs:
        video.append(otio.schema.Clip(
            name=bid,
            media_reference=otio.schema.ExternalReference(
                target_url=f"file:///nonexistent/{bid}", available_range=span(sec)),
            source_range=span(sec),
            metadata={"pipeline": {"beat": bid}}))
        audio.append(otio.schema.Gap(source_range=span(sec),
                                     metadata={"pipeline": {"beat": bid}}))
    tl.tracks.append(video)
    tl.tracks.append(audio)
    if with_music:
        total = sum(s for _b, s in beats_secs)
        music = otio.schema.Track(
            name="music", kind=otio.schema.TrackKind.Audio,
            metadata={"pipeline": {"stem": "music", "layout": "span"}})
        music.append(otio.schema.Clip(
            name="music",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///nonexistent/bed", available_range=span(total)),
            source_range=span(total),
            metadata={"pipeline": {"stem": "music", "role": "bed"}}))
        tl.tracks.append(music)
    return tl


class TestProgramStems:
    def _compose(self, videos, monkeypatch, modules):
        """Run program.build over hand-written module timelines."""
        import json
        from conftest import animation_beat, shotlist
        from pipeline import timeline as tl_mod
        d = None
        for slug, tl in modules.items():
            d = videos(shotlist(animation_beat("b01")), slug=slug)
            (d / "out").mkdir(parents=True, exist_ok=True)
            otio.adapters.write_to_file(tl, str(tl_mod.otio_path(slug)))
        registry = d.parent / "projects"
        registry.mkdir(exist_ok=True)
        (registry / "p.json").write_text(json.dumps(
            {"id": "p", "title": "P",
             "modules": [{"slug": s} for s in modules]}))
        monkeypatch.setattr(program.projects, "PROJECTS", registry)
        # projects._on_disk reads its own VIDEOS constant, not config.VIDEOS —
        # point it at the fixture root or every test slug reads as missing.
        monkeypatch.setattr(program.projects, "VIDEOS", d.parent)
        monkeypatch.setattr(program, "out_dir", lambda: d.parent / "outp")
        # the build's summary line prints the output relative to REPO; the
        # fixture tree lives in tmp, so REPO must move with it.
        monkeypatch.setattr(program.config, "REPO", d.parent.parent)
        assert program.build("p")
        held = otio.adapters.read_from_file(str(program.otio_path("p")))
        return {stems.stem_of(t): t for t in held.tracks}

    def test_a_module_without_music_contributes_a_span_gap(self, videos, monkeypatch):
        by_stem = self._compose(videos, monkeypatch, {
            "one": _module_tl("one", [("b01", 3.0)], with_music=True),
            "two": _module_tl("two", [("b01", 4.0)], with_music=False),
        })
        music = by_stem["music"]
        # module one contributed its bed; module two a gap of its length
        assert isinstance(music[0], otio.schema.Clip)
        assert music[0].name == "one/music"
        assert isinstance(music[1], otio.schema.Gap)
        assert music.duration().to_seconds() == by_stem["visuals"].duration().to_seconds()

    def test_every_stem_matches_the_picture_length(self, videos, monkeypatch):
        by_stem = self._compose(videos, monkeypatch, {
            "one": _module_tl("one", [("b01", 3.0), ("b02", 2.0)]),
            "two": _module_tl("two", [("b01", 4.0)]),
        })
        pic = by_stem["visuals"].duration().to_seconds()
        for name, track in by_stem.items():
            assert track.duration().to_seconds() == pic, name

    def test_narration_clip_names_carry_the_stem_suffix(self, videos, monkeypatch):
        tl = _module_tl("one", [("b01", 3.0)])
        # replace the gap with a real narration clip to check its qualified name
        narr = next(t for t in tl.tracks if t.name == "narration")
        span = narr[0].source_range
        del narr[:]
        narr.append(otio.schema.Clip(
            name="b01.narration",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///nonexistent/w", available_range=span),
            source_range=span, metadata={"pipeline": {"beat": "b01"}}))
        by_stem = self._compose(videos, monkeypatch, {"one": tl})
        assert by_stem["narration"][0].name == "one/b01.narration"


class TestDispatch:
    def test_bare_beat(self):
        assert program.dispatch("axioms/b03") == ("axioms", "b03")

    def test_stem_suffix(self):
        assert program.dispatch("axioms/b03.narration") == ("axioms", "b03")

    def test_legacy_vo_suffix(self):
        assert program.dispatch("axioms/b03.vo") == ("axioms", "b03")

    def test_module_level_music(self):
        assert program.dispatch("axioms/music") == ("axioms", "music")

    def test_no_module_is_none(self):
        assert program.dispatch("b03") is None


class TestAudioStock:
    """The house sound: room tone under everything, saturation on the speech."""

    def test_room_tone_spans_the_programme_in_every_shape(self):
        for n_speech, n_bed, pl in ((1, 0, [(0, 0, 3.0)]),
                                    (1, 1, [(0, 0, 3.0), (0, 0, 7.0)]),
                                    (0, 1, [(0, 0, 7.0)])):
            g = assemble._mix_graph(n_speech, n_bed, pl, 7.0)
            assert "anoisesrc=color=pink" in g
            assert "duration=7.000[room]" in g

    def test_the_exciter_touches_speech_and_never_the_bed(self):
        g = assemble._mix_graph(1, 1, [(0, 0, 3.0), (0, 0, 7.0)], 7.0)
        assert g.count("aexciter") == 1
        assert "aexciter" in g.split("[vox]")[0]      # on the speech branch
        g = assemble._mix_graph(0, 1, [(0, 0, 7.0)], 7.0)
        assert "aexciter" not in g                    # bed alone stays clean
