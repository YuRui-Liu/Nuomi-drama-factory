from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import narrative_groups
from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    StyleProjections,
    StyleSnapshot,
    ValidationReport,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.media_capabilities.video.h3_timeline import transition_for
from novelvideo.task_backend.runners.narrative_group_video_compose import (
    LocalCompositionPlan,
    build_ffmpeg_filter_complex,
)


def test_compose_filter_applies_directional_j_and_l_cuts() -> None:
    graph = build_ffmpeg_filter_complex(
        LocalCompositionPlan(
            paths=("one.mp4", "two.mp4", "three.mp4"),
            transitions=(
                transition_for("time_jump", has_leading_dialogue=True),
                transition_for("causal", has_trailing_ambience=True),
            ),
        ),
        durations=(4.0, 5.0, 6.0),
    )

    assert "xfade=transition=fade:duration=0.333333:offset=3.666667" in graph
    # J-cut: input 1 audio starts 300ms before its 3.666667s visual boundary.
    assert "adelay=3367|3367[j1]" in graph
    # L-cut: the final 500ms of input 1 is replayed from the next 8.666667s boundary.
    assert "atrim=start=4.500000" in graph
    assert "adelay=8667|8667[l2]" in graph
    assert "acrossfade" not in graph
    assert "[1:a]asplit=2[a1-main][a1-tail]" in graph
    assert "[a1-main]adelay=3367|3367[j1]" in graph
    assert "[a1-tail]atrim=start=4.500000" in graph
    assert graph.count("[1:a]") == 1


@pytest.mark.asyncio
async def test_director_input_uses_redirect_style_payload(monkeypatch) -> None:
    from novelvideo.task_backend.runners import director_plan

    source = SimpleNamespace(
        episode_number=1, source_revision=7, content_hash="source", content="Hero leaves."
    )
    monkeypatch.setattr(
        director_plan,
        "_build_episode_source_store",
        lambda _ctx: _async(SimpleNamespace(list_sources=lambda: _async([source]))),
    )
    selected = StyleSnapshot(
        snapshot_id="selected-snapshot", style_id="selected-style", style_version="2",
        catalog_hash="catalog", style_hash="selected-hash",
        projections=StyleProjections(
            director="selected-director", image="image", video="video", panel_tag="tag"
        ),
    )
    seen = []
    monkeypatch.setattr(
        "novelvideo.project_config.load_project_config_file_from_state_dir",
        lambda _state_dir: {"visual_style": "project-default"},
    )
    monkeypatch.setattr(
        "novelvideo.services.style_service.StyleService.resolve_style_snapshot",
        lambda project_style, override=None, **_kwargs: seen.append(
            (project_style, override)
        ) or selected,
    )

    value = await director_plan._build_director_plan_input(
        {
            "project_id": "project-1", "episode": 1, "source_revision": 7,
            "style_id": "selected-style", "style_snapshot_id": "selected-snapshot",
        },
        SimpleNamespace(
            project_id="project-1", state_dir="state", owner_username="owner",
            project_name="project", output_dir="output",
        ),
    )

    assert seen == [("project-default", "selected-style")]
    assert value.project_style_snapshot_id == "selected-snapshot"
    assert value.style_director["projection"] == "selected-director"


def _active_plan(project_dir) -> None:
    snapshot = StyleSnapshot(
        snapshot_id="old-snapshot", style_id="anime", style_version="1",
        catalog_hash="catalog", style_hash="old-hash",
        projections=StyleProjections(
            director="director", image="image", video="video", panel_tag="anime"
        ),
    )
    revision = DirectorPlanRevision.new(
        episode=1, source_script_hash="source", director_model="deepseek",
        prompt_version="v2", project_style_snapshot_id=snapshot.snapshot_id,
        project_style_snapshot=snapshot,
        groups=(NarrativeGroupPlan(
            id="ng-01", ordinal=1, source_span_ids=("s1",),
            scene_anchor="room", time_anchor="day", objective="leave",
            visible_turn="door opens", relation_to_previous="single",
            style_snapshot_id=snapshot.snapshot_id,
            shots=(ShotPlan(
                id="shot-1", source_span_ids=("s1",), subject="hero",
                action="leaves", visible_start_state="inside",
                visible_end_state="outside", duration_seconds=3,
            ),),
        ),),
    ).model_copy(update={
        "status": "review_required", "validation_report": ValidationReport(passed=True)
    })
    store = DirectorPlanStore(project_dir)
    store.save(revision)
    store.activate(1, revision.revision_id)


def test_style_null_restores_default_and_redirect_enqueues_director_task(
    monkeypatch, tmp_path,
) -> None:
    _active_plan(tmp_path)
    ctx = SimpleNamespace(
        project_id="project-1", project_name="demo", owner_username="tester",
        output_dir=str(tmp_path), state_dir=str(tmp_path),
    )
    resolved = SimpleNamespace(ctx=ctx, project_dir=tmp_path, output_dir=str(tmp_path))

    async def resolve(*_args, **_kwargs):
        return resolved

    class Store:
        async def get_beats_as_dicts(self, _episode):
            return []

    async def store(*_args, **_kwargs):
        return Store()

    calls = []

    async def enqueue(_ctx, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task-redirect"),
            backend="inline", queue=kwargs["queue_kind"],
        )

    default_snapshot = StyleSnapshot(
        snapshot_id="default-snapshot", style_id="project-default", style_version="2",
        catalog_hash="catalog-2", style_hash="default-hash",
        projections=StyleProjections(
            director="d", image="i", video="v", panel_tag="default"
        ),
    )
    monkeypatch.setattr(narrative_groups, "resolve_project_scope", resolve)
    monkeypatch.setattr(narrative_groups, "make_sqlite_store_for_context", store)
    monkeypatch.setattr(narrative_groups, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))
    monkeypatch.setattr(
        "novelvideo.episode_source_store.EpisodeSourceStore",
        lambda _store: SimpleNamespace(list_sources=lambda: _async([
            SimpleNamespace(episode_number=1, source_revision=7)
        ])),
    )
    monkeypatch.setattr(
        "novelvideo.services.style_service.StyleService.resolve_style_snapshot",
        lambda project_style, override=None, **_kwargs: default_snapshot,
    )
    monkeypatch.setattr(
        "novelvideo.project_config.load_project_config_from_state_dir",
        lambda *_args, **_kwargs: {"visual_style": "project-default"},
    )
    app = FastAPI()
    app.include_router(narrative_groups.router, prefix="/api/v1")
    app.dependency_overrides[narrative_groups.get_api_user] = lambda: {
        "id": "user-1", "username": "tester", "scopes": ["tasks:submit"],
    }

    response = TestClient(app).put(
        "/api/v1/projects/project-1/episodes/1/narrative-groups/ng-01/style",
        json={"style_id": None, "action": "redirect"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["task_id"] == "task-redirect"
    assert data["style_snapshot"]["style_id"] == "project-default"
    assert calls[0]["task_type"] == "director_plan"


async def _async(value):
    return value
