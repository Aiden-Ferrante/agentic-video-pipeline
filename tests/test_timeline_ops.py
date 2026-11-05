"""Timeline reads and mutations — the seam the agent and Kdenlive share.

These build OTIO documents directly rather than rendering anything, so they run
in the thin venv and in milliseconds. The fixtures build STEMS-FORM documents
(stem-named tracks, layout metadata, beat ids on Gaps) — the only form the
mutations accept; the one legacy-form test pins the refusal.
"""
import opentimelineio as otio
import pytest
from conftest import animation_beat, shotlist

from pipeline import mcp_server as mcp
from pipeline import timeline


def _span(seconds, fps=30):
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, fps),
        duration=otio.opentime.RationalTime(round(seconds * fps), fps))


def _clip(name, seconds, beat, extra_meta=None):
    return otio.schema.Clip(
        name=name,
        media_reference=otio.schema.ExternalReference(
            target_url=f"file:///nonexistent/{name}",
            available_range=_span(seconds)),
        source_range=_span(seconds),
        metadata={"pipeline": {"beat": beat, **(extra_meta or {})}})


def _gap(seconds, beat):
    return otio.schema.Gap(source_range=_span(seconds),
                           metadata={"pipeline": {"beat": beat}})


def _stem_track(name, stem, layout, kind):
    return otio.schema.Track(
        name=name, kind=kind,
        metadata={"pipeline": {"stem": stem, "layout": layout}})


def _write(tl):
    otio.adapters.write_to_file(tl, str(timeline.edit_path("fx")))


@pytest.fixture
def promoted(videos):
    """A two-beat promoted timeline: picture clips, one narration clip and one
    narration Gap — the shape a stems build actually emits."""
    d = videos(shotlist(animation_beat("b01"), animation_beat("b02")))
    (d / "out").mkdir(parents=True, exist_ok=True)

    tl = otio.schema.Timeline(name="fx")
    video = _stem_track("visuals", "visuals", "beats", otio.schema.TrackKind.Video)
    voice = _stem_track("narration", "narration", "beats", otio.schema.TrackKind.Audio)
    video.append(_clip("b01", 3.0, "b01"))
    video.append(_clip("b02", 4.0, "b02"))
    voice.append(_clip("b01.narration", 3.0, "b01"))
    # b02 has no narration: a Gap of the picture's length, carrying the beat.
    voice.append(_gap(4.0, "b02"))
    tl.tracks.append(video)
    tl.tracks.append(voice)
    _write(tl)
    return d


@pytest.fixture
def promoted_with_music(promoted):
    """The same document plus a span-layout music track under the programme."""
    tl = otio.adapters.read_from_file(str(timeline.edit_path("fx")))
    music = _stem_track("music", "music", "span", otio.schema.TrackKind.Audio)
    music.append(_clip("music", 7.0, None, {"stem": "music", "role": "bed"}))
    tl.tracks.append(music)
    _write(tl)
    return promoted


def test_iterating_a_temporary_timeline_yields_nothing(promoted):
    """The OTIO footgun that made drift detection silently check zero clips.

    A Stack does not keep its parent Timeline alive, so the temporary is freed
    and the loop finds no items — it does not raise. Bind the timeline first.
    """
    path = timeline.edit_path("fx")
    temporary = sum(1 for t in otio.adapters.read_from_file(str(path)).tracks for _ in t)
    held = otio.adapters.read_from_file(str(path))
    assert temporary == 0
    assert sum(1 for t in held.tracks for _ in t) == 4


def test_authoritative_path_prefers_the_promoted_edit(promoted):
    assert timeline.authoritative_path("fx") == timeline.edit_path("fx")


def test_authoritative_path_falls_back_to_the_generated_timeline(videos):
    videos(shotlist(animation_beat("b01")))
    assert timeline.authoritative_path("fx") == timeline.otio_path("fx")


class TestRetime:
    def test_moves_picture_and_narration_together(self, promoted):
        """Retiming one track alone desyncs them, and the render is cut to the
        shorter — so shortening the picture silently truncates the voice."""
        mcp.retime("fx", "b01", 2.0)
        tracks = mcp.get_timeline("fx")["tracks"]
        assert tracks["visuals"][0]["seconds"] == 2.0
        assert tracks["narration"][0]["seconds"] == 2.0

    def test_resizes_a_narration_gap_by_beat(self, promoted):
        """The beat with no narration: its silence is a Gap carrying the beat
        id, and it must move with the picture — matched, never skipped."""
        mcp.retime("fx", "b02", 2.5)
        held = otio.adapters.read_from_file(str(timeline.edit_path("fx")))
        narration = next(t for t in held.tracks if t.name == "narration")
        assert narration[1].source_range.duration.to_seconds() == 2.5

    def test_a_span_track_is_untouched_by_per_beat_surgery(self, promoted_with_music):
        mcp.retime("fx", "b01", 2.0)
        held = otio.adapters.read_from_file(str(timeline.edit_path("fx")))
        music = next(t for t in held.tracks if t.name == "music")
        assert music[0].source_range.duration.to_seconds() == 7.0

    def test_warns_when_it_would_clip_the_narration(self, promoted):
        assert "WARNING" in mcp.retime("fx", "b01", 2.0)

    def test_is_quiet_when_shortening_only_a_gap(self, promoted):
        """Shortening silence is not clipping a performance."""
        assert "WARNING" not in mcp.retime("fx", "b02", 2.0)

    def test_refuses_to_exceed_the_media(self, promoted):
        """Holding a frame the edit never saw is a re-render, not a retime."""
        assert "re-render" in mcp.retime("fx", "b02", 99.0)
        assert mcp.get_timeline("fx")["tracks"]["visuals"][1]["seconds"] == 4.0

    def test_refuses_a_nonpositive_duration(self, promoted):
        assert "longer than zero" in mcp.retime("fx", "b01", 0)

    def test_refuses_an_unknown_beat(self, promoted):
        assert "No clip named" in mcp.retime("fx", "nope", 1.0)


class TestReorder:
    def test_reorders_clips_and_gaps_in_step(self, promoted):
        """The regression the redesign exists for: the old Clips-only loop
        skipped a Gap-carrying track entirely, so the picture reordered and the
        silence stayed put — desyncing every beat after the first move."""
        mcp.reorder("fx", ["b02", "b01"])
        held = otio.adapters.read_from_file(str(timeline.edit_path("fx")))
        visuals = next(t for t in held.tracks if t.name == "visuals")
        narration = next(t for t in held.tracks if t.name == "narration")
        assert [c.name for c in visuals] == ["b02", "b01"]
        # the Gap (b02's silence) moved to the front with its beat
        assert isinstance(narration[0], otio.schema.Gap)
        assert narration[0].metadata["pipeline"]["beat"] == "b02"

    def test_a_span_track_keeps_its_own_order(self, promoted_with_music):
        mcp.reorder("fx", ["b02", "b01"])
        held = otio.adapters.read_from_file(str(timeline.edit_path("fx")))
        music = next(t for t in held.tracks if t.name == "music")
        assert [c.name for c in music] == ["music"]

    def test_refuses_a_partial_order(self, promoted):
        assert "every beat" in mcp.reorder("fx", ["b01"])

    def test_refuses_an_unknown_beat(self, promoted):
        assert "no items for" in mcp.reorder("fx", ["b01", "ghost"])


class TestLegacyDocuments:
    def test_mutations_refuse_a_pre_stems_promoted_edit(self, videos):
        """Zero such documents exist on disk; one restored from a backup gets a
        refusal naming the fix, never a positional guess."""
        d = videos(shotlist(animation_beat("b01")))
        (d / "out").mkdir(parents=True, exist_ok=True)
        tl = otio.schema.Timeline(name="fx")
        video = otio.schema.Track(name="video", kind=otio.schema.TrackKind.Video)
        video.append(_clip("b01", 3.0, "b01"))
        tl.tracks.append(video)
        _write(tl)
        with pytest.raises(ValueError, match="re-promote"):
            mcp.retime("fx", "b01", 1.0)
        with pytest.raises(ValueError, match="re-promote"):
            mcp.reorder("fx", ["b01"])

    def test_reads_still_resolve_a_legacy_named_picture_track(self, videos):
        d = videos(shotlist(animation_beat("b01")))
        (d / "out").mkdir(parents=True, exist_ok=True)
        tl = otio.schema.Timeline(name="fx")
        video = otio.schema.Track(name="video", kind=otio.schema.TrackKind.Video)
        video.append(_clip("b01", 3.0, "b01"))
        tl.tracks.append(video)
        _write(tl)
        t = mcp.get_timeline("fx")
        assert [c["beat"] for c in t["clips"]] == ["b01"]


class TestPromotionGate:
    def test_mutations_refuse_an_unpromoted_timeline(self, videos):
        """Editing the generated timeline is pointless — the next build
        overwrites it — so the tools must say so rather than appear to work."""
        videos(shotlist(animation_beat("b01")))
        with pytest.raises(ValueError, match="no promoted timeline"):
            mcp.retime("fx", "b01", 1.0)
        with pytest.raises(ValueError, match="no promoted timeline"):
            mcp.reorder("fx", ["b01"])

    def test_promote_refuses_to_clobber_an_existing_edit(self, promoted):
        assert "already promoted" in mcp.promote_timeline("fx")


def test_get_timeline_does_not_count_narration_as_picture(promoted):
    """`clips` is the cut. Counting the voice track too once made a two-beat
    video report four clips."""
    t = mcp.get_timeline("fx")
    assert [c["beat"] for c in t["clips"]] == ["b01", "b02"]
    assert set(t["tracks"]) == {"visuals", "narration"}


def test_the_music_track_shows_up_named(promoted_with_music):
    t = mcp.get_timeline("fx")
    assert set(t["tracks"]) == {"visuals", "narration", "music"}
    # and the picture is still only the visuals
    assert [c["beat"] for c in t["clips"]] == ["b01", "b02"]
