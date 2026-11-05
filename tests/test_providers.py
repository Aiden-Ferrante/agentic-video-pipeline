"""The resolver: which adapter renders each stem, per project.

Every path here must ANSWER — no registry, no owner, junk entries — because
resolution runs during task work and an authoring typo must fail its own beat
with a message, never take the build system down.
"""
import json

import pytest

from pipeline import projects, providers


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """An isolated registry claiming slug `fx` for project `p`."""
    reg = tmp_path / "projects"
    reg.mkdir()
    # Not tmp_path/"videos" — the conftest `videos` fixture owns that name,
    # and a test using both fixtures would collide on the mkdir.
    vids = tmp_path / "registry-videos"
    (vids / "fx").mkdir(parents=True)
    (vids / "fx" / "shotlist.json").write_text("{}")
    monkeypatch.setattr(projects, "PROJECTS", reg)
    monkeypatch.setattr(projects, "VIDEOS", vids)
    # config.VIDEOS too: write_fragment resolves beats.tools_path through it,
    # and leaving it at the real repo is exactly how a test once wrote a
    # videos/fx into the working tree.
    from pipeline import config
    monkeypatch.setattr(config, "VIDEOS", vids)

    def author(tools):
        (reg / "p.json").write_text(json.dumps(
            {"id": "p", "title": "P", "modules": [{"slug": "fx"}],
             "tools": tools}))
    return author


class TestDefaults:
    def test_no_registry_resolves_to_the_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr(projects, "PROJECTS", tmp_path / "nowhere")
        monkeypatch.setattr(projects, "VIDEOS", tmp_path / "novideos")
        assert providers.resolve("fx", "narration") == \
            {"provider": "local", "config": {}}
        assert providers.resolve("fx", "visuals", "animation")["provider"] == "manim"

    def test_an_unowned_slug_resolves_to_the_defaults(self, registry):
        registry({})
        assert providers.resolve("elsewhere", "music")["provider"] == "library"


class TestProjectOverride:
    def test_string_shorthand(self, registry):
        registry({"narration": "kokoro"})
        assert providers.resolve("fx", "narration") == \
            {"provider": "kokoro", "config": {}}

    def test_dict_form_carries_config(self, registry):
        registry({"narration": {"provider": "elevenlabs", "voice_id": "v9"}})
        assert providers.resolve("fx", "narration") == \
            {"provider": "elevenlabs", "config": {"voice_id": "v9"}}

    def test_nested_visuals_sub_keys(self, registry):
        registry({"visuals": {"animation": "manim", "imagery": "midjourney"}})
        assert providers.resolve("fx", "visuals", "imagery")["provider"] == "midjourney"
        assert providers.resolve("fx", "visuals", "animation")["provider"] == "manim"


class TestLegacyKeys:
    def test_flat_voice_key_means_narration(self, registry):
        registry({"voice": "kokoro"})
        assert providers.resolve("fx", "narration")["provider"] == "kokoro"

    def test_flat_imagery_key_means_visuals_imagery(self, registry):
        registry({"imagery": "diffusers/qwen-image"})
        assert providers.resolve("fx", "visuals", "imagery")["provider"] == \
            "diffusers/qwen-image"

    def test_flat_sound_key_means_music(self, registry):
        registry({"sound": "library"})
        assert providers.resolve("fx", "music")["provider"] == "library"


class TestBadAuthoring:
    def test_a_non_string_non_dict_entry_warns_and_defaults(self, registry, capsys):
        registry({"narration": 42})
        out = providers.resolve("fx", "narration")
        assert out["provider"] == "local"
        assert "WARNING" in capsys.readouterr().out

    def test_a_pasted_key_is_refused_from_the_registry(self, registry, capsys):
        registry({"narration": "sk-abcdefghijklmnopqrstuvwxyz123456"})
        out = providers.resolve("fx", "narration")
        assert out["provider"] == "local"
        assert "credential" in capsys.readouterr().out

    def test_a_key_shaped_config_value_warns_but_resolves(self, registry, capsys):
        registry({"narration": {"provider": "elevenlabs",
                                "api_key": "sk-abcdefghijklmnop"}})
        out = providers.resolve("fx", "narration")
        assert out["provider"] == "elevenlabs"
        assert "credential" in capsys.readouterr().out


class TestElevenLabsBridge:
    """Request shaping and refusals — no network, ever. The bridge is driven
    the way voice.render drives it: a subprocess with JSON on stdin."""

    def _run(self, req, env=None, tmp_path=None):
        import json as j
        import os
        import subprocess
        import sys
        from pathlib import Path
        script = Path(__file__).parent.parent / "bridges" / "elevenlabs_render.py"
        full = {**os.environ, **(env or {})}
        full.pop("ELEVENLABS_API_KEY", None)
        full.update(env or {})
        r = subprocess.run([sys.executable, str(script), "synth"],
                           input=j.dumps(req), capture_output=True, text=True,
                           env=full)
        return j.loads(r.stdout.strip().splitlines()[-1])

    def test_missing_key_is_a_clean_refusal(self, tmp_path):
        out = self._run({"text": "hi", "out": str(tmp_path / "o.wav")},
                        env={"ELEVENLABS_RUN_COUNTER": str(tmp_path / "c"),
                             "ELEVENLABS_ENV_FILE": str(tmp_path / "no.env")})   # not the repo's real .env
        assert out["ok"] is False
        assert "ELEVENLABS_API_KEY" in out["error"]

    def test_spend_cap_refuses_with_the_count(self, tmp_path):
        counter = tmp_path / "c"
        counter.write_text("2")
        out = self._run({"text": "hi", "out": str(tmp_path / "o.wav")},
                        env={"ELEVENLABS_API_KEY": "fake-key-for-the-cap-check",
                             "ELEVENLABS_MAX_BEATS": "2",
                             "ELEVENLABS_RUN_COUNTER": str(counter)})
        assert out["ok"] is False
        assert "spend cap" in out["error"] and "2" in out["error"]

    def test_unknown_command_is_a_verdict(self, tmp_path):
        import json as j
        import subprocess
        import sys
        from pathlib import Path
        script = Path(__file__).parent.parent / "bridges" / "elevenlabs_render.py"
        r = subprocess.run([sys.executable, str(script), "conjure"],
                           input="{}", capture_output=True, text=True)
        out = j.loads(r.stdout.strip())
        assert out["ok"] is False and "unknown command" in out["error"]


class TestProviderInBytes:
    """The invalidation contract: switching a project's narration provider
    changes _tools.json and only the narration chain notices."""

    def test_switching_the_provider_changes_the_fragment(self, registry):
        from pipeline import beats, providers as prov
        registry({"narration": "local"})
        prov.write_fragment("fx")
        before = beats.tools_path("fx").read_text()
        registry({"narration": {"provider": "elevenlabs", "voice_id": "v9"}})
        prov.write_fragment("fx")
        after = beats.tools_path("fx").read_text()
        assert before != after
        assert "elevenlabs" in after

    def test_the_animation_fragment_never_carries_the_narration_provider(
            self, registry, videos):
        """The cross-stem firewall: a narration provider switch must not put a
        byte anywhere the animation task reads. Uses the `videos` fixture so
        config.VIDEOS is the isolated root — writing through the registry
        fixture's path alone once leaked a videos/fx into the real repo."""
        from conftest import animation_beat, shotlist
        from pipeline import beats
        videos(shotlist(animation_beat("b01")))
        beats.split("fx")
        frag = beats.fragment_path("fx", "b01", "animation").read_text()
        assert "elevenlabs" not in frag and "provider" not in frag
