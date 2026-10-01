from types import SimpleNamespace

import pytest

from novelvideo.api.routes import narrative_groups as routes


@pytest.mark.parametrize("body", [
    routes.NarrativeGroupGenerationRequest(),
    routes.NarrativeGroupVideoRequest(revision=0, plan_revision=1),
])
def test_omitted_group_aspect_follows_landscape_project(monkeypatch, tmp_path, body):
    monkeypatch.setattr("novelvideo.project_config.load_project_config_from_state_dir",
                        lambda *args, **kwargs: {"aspect_ratio": "16:9"})
    resolved = SimpleNamespace(ctx=SimpleNamespace(state_dir=tmp_path), project_dir=tmp_path)
    assert routes._generation_aspect_ratio(resolved, body.aspect_ratio) == "16:9"


@pytest.mark.parametrize("project_aspect,expected", [("2:3", "9:16"), ("9:16", "9:16"), ("16:9", "16:9")])
def test_project_orientation_and_explicit_override(monkeypatch, tmp_path, project_aspect, expected):
    monkeypatch.setattr("novelvideo.project_config.load_project_config_from_state_dir",
                        lambda *args, **kwargs: {"aspect_ratio": project_aspect})
    resolved = SimpleNamespace(ctx=SimpleNamespace(state_dir=tmp_path), project_dir=tmp_path)
    assert routes._generation_aspect_ratio(resolved, None) == expected
    assert routes._generation_aspect_ratio(resolved, "16:9") == "16:9"
