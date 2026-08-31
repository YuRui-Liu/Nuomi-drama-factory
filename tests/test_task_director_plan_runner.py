from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.director_plan.models import ValidationReport
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.narrative_groups.models import (
    CellMapping,
    GridLayout,
    GroupStageState,
    NarrativeGroup,
)
from novelvideo.narrative_groups.service import save_groups

from director_plan.test_migration import _plan, _shot


@pytest.mark.asyncio
async def test_director_plan_runner_reports_real_stages_and_returns_revision(monkeypatch):
    from novelvideo.task_backend.runners import director_plan
    from novelvideo.task_backend.registry import get_project_task_runner

    assert get_project_task_runner("director_plan") is director_plan.run_director_plan

    progress_events: list[tuple[float, str]] = []
    input_value = object()
    old_plan = object()
    assets = (object(),)
    revision = SimpleNamespace(
        revision_id="revision-1",
        status="review_required",
        validation_report=SimpleNamespace(
            passed=True,
            model_dump=lambda **_kwargs: {"passed": True, "issues": []},
        ),
    )

    class Service:
        async def create_draft(self, value, *, on_stage, old_plan, assets):
            assert value is input_value
            assert old_plan is old_plan_value
            assert assets is assets_value
            assert progress_events == [(0.05, "M1 source_locked")]
            on_stage("episode_planned")
            assert progress_events[-1] == (0.55, "M1 episode_planned")
            on_stage("validated")
            assert progress_events[-1] == (0.8, "M1 validated")
            return revision

    monkeypatch.setattr(
        director_plan,
        "_build_director_plan_input",
        lambda *_args, **_kwargs: _async(input_value),
    )
    monkeypatch.setattr(
        director_plan,
        "_build_director_plan_service",
        lambda _ctx: Service(),
    )
    old_plan_value, assets_value = old_plan, assets
    monkeypatch.setattr(
        director_plan,
        "_load_asset_migration_context",
        lambda _ctx, _episode: (old_plan_value, assets_value),
    )
    monkeypatch.setattr(
        director_plan,
        "get_task_manager",
        lambda: SimpleNamespace(
            update_progress_for_project=lambda *_args, **kwargs: progress_events.append(
                (kwargs["progress"], kwargs["current_task"])
            )
        ),
    )

    result = await director_plan._run_director_plan(
        {
            "__run_task_id": "task-1",
            "scope": "revision:7",
            "payload": {"project_id": "project-1", "episode": 3, "source_revision": 7},
        },
        SimpleNamespace(project_id="project-1"),
    )

    assert progress_events == [
        (0.05, "M1 source_locked"),
        (0.55, "M1 episode_planned"),
        (0.8, "M1 validated"),
        (0.85, "M2 assets_matched"),
        (1.0, "M1 review_ready"),
    ]
    assert result == {
        "revision_id": "revision-1",
        "status": "review_required",
        "validation_report": {"passed": True, "issues": []},
    }


@pytest.mark.asyncio
async def test_director_plan_runner_enforces_180_second_timeout(monkeypatch):
    from novelvideo.task_backend.runners import director_plan

    seen: dict[str, float] = {}

    class Service:
        async def create_draft(self, _value, *, on_stage, old_plan, assets):
            on_stage("episode_planned")
            on_stage("validated")
            return SimpleNamespace(
                revision_id="r",
                status="review_required",
                validation_report=SimpleNamespace(
                    passed=True, model_dump=lambda **_kwargs: {"passed": True}
                ),
            )

    original_wait_for = asyncio.wait_for

    async def recording_wait_for(awaitable, timeout):
        seen["timeout"] = timeout
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(director_plan.asyncio, "wait_for", recording_wait_for)
    monkeypatch.setattr(
        director_plan, "_build_director_plan_input", lambda *_args: _async(object())
    )
    monkeypatch.setattr(
        director_plan, "_build_director_plan_service", lambda _ctx: Service()
    )
    monkeypatch.setattr(
        director_plan,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_a, **_kw: None),
    )

    await director_plan._run_director_plan(
        {"payload": {"project_id": "p", "episode": 1, "source_revision": 1}},
        SimpleNamespace(project_id="p"),
    )

    assert seen == {"timeout": 180}


@pytest.mark.asyncio
async def test_director_plan_runner_exposes_structured_failure_without_secrets(monkeypatch):
    from novelvideo.task_backend.runners import director_plan

    class Service:
        async def create_draft(self, _value, *, on_stage, old_plan, assets):
            error = RuntimeError("provider rejected sk-live-secret")
            error.code = "director_plan_provider_error"
            raise error

    monkeypatch.setattr(
        director_plan, "_build_director_plan_input", lambda *_args: _async(object())
    )
    monkeypatch.setattr(
        director_plan, "_build_director_plan_service", lambda _ctx: Service()
    )
    monkeypatch.setattr(
        director_plan,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_a, **_kw: None),
    )

    with pytest.raises(director_plan.DirectorPlanTaskError) as captured:
        await director_plan._run_director_plan(
            {"payload": {"project_id": "p", "episode": 1, "source_revision": 1}},
            SimpleNamespace(project_id="p"),
        )

    assert captured.value.error_code == "director_plan_provider_error"
    assert captured.value.validation_report == {"passed": False, "issues": []}
    assert "sk-live-secret" not in str(captured.value)


def test_asset_migration_context_collects_real_project_assets_safely(tmp_path: Path):
    from novelvideo.task_backend.runners import director_plan

    old_plan = _plan("old-revision", (_shot("old-shot"),)).model_copy(
        update={"validation_report": ValidationReport(passed=True)}
    )
    store = DirectorPlanStore(tmp_path)
    store.save(old_plan)
    store.activate(1, old_plan.revision_id)

    safe_asset = tmp_path / "assets" / "old-shot.png"
    safe_asset.parent.mkdir()
    safe_asset.write_bytes(b"image")
    unsafe_asset = tmp_path.parent / "outside.png"
    unsafe_asset.write_bytes(b"outside")
    save_groups(
        tmp_path,
        1,
        [
            NarrativeGroup(
                id="ng-1",
                ordinal=1,
                beat_ids=("old-shot",),
                layout=GridLayout(rows=1, columns=1, capacity=1),
                cell_to_beat=(CellMapping(cell=0, beat_id="old-shot"),),
                shot_ids=("old-shot",),
                director_revision_id=old_plan.revision_id,
                stages={
                    "render": GroupStageState(
                        status="completed",
                        cell_assets=(
                            {
                                "asset_id": "asset-safe",
                                "beat_id": "old-shot",
                                "path": str(safe_asset),
                                "style_hash": "style-a",
                            },
                            {
                                "asset_id": "asset-unsafe",
                                "beat_id": "old-shot",
                                "path": str(unsafe_asset),
                                "style_hash": "style-a",
                            },
                        ),
                    )
                },
            )
        ],
    )

    loaded_plan, assets = director_plan._load_asset_migration_context(
        SimpleNamespace(output_dir=tmp_path), 1
    )

    assert loaded_plan is not None
    assert loaded_plan.revision_id == old_plan.revision_id
    assert len(assets) == 1
    assert assets[0].asset_id == "asset-safe"
    assert assets[0].old_shot_id == "old-shot"
    assert assets[0].asset_path == str(safe_asset.resolve())
    assert assets[0].style_hash == "style-a"


@pytest.mark.asyncio
async def test_input_assembler_uses_active_semantic_revision(monkeypatch):
    from novelvideo.task_backend.runners import director_plan

    source = SimpleNamespace(
        episode_number=3,
        source_revision=7,
        content_hash="sha256:episode-3",
        content="""3-1 废弃仓库 夜 内
林默：别出声。
△脚步声逼近。
3-2 天台 日 外
苏青：他们来了。""",
    )

    class Repository:
        async def list_sources(self):
            return [source]

    monkeypatch.setattr(
        director_plan,
        "_build_episode_source_store",
        lambda _ctx: _async(Repository()),
    )
    from novelvideo.screenplay_semantics import parse_screenplay_document
    parsed = parse_screenplay_document(source.content)
    semantic = SimpleNamespace(
        revision_id="semantic-7", source_revision=7,
        scenes=parsed.scenes, beats=(),
    )
    monkeypatch.setattr(
        director_plan, "_load_active_semantic_revision", lambda *_args: semantic
    )

    value = await director_plan._build_director_plan_input(
        {"project_id": "project-1", "episode": 3, "source_revision": 7},
        SimpleNamespace(project_id="project-1"),
    )

    assert [span.id for span in value.source_spans] == [
        "line-2",
        "line-3",
        "line-5",
    ]
    assert len(value.source_spans) > 1
    assert value.source_spans[0].scene == "废弃仓库"
    assert value.source_spans[0].time == "夜"
    assert value.source_spans[0].dialogue_text == "林默：别出声。"
    assert value.source_spans[1].scene == "废弃仓库"
    assert value.source_spans[2].scene == "天台"
    assert value.source_spans[2].time == "日"
    assert value.source_spans[2].dialogue_text == "苏青：他们来了。"
    assert value.source_script_hash == "sha256:episode-3"
    assert value.semantic_revision_id == "semantic-7"


@pytest.mark.asyncio
async def test_input_assembler_rejects_source_revision_conflict(monkeypatch):
    from novelvideo.task_backend.runners import director_plan

    class Repository:
        async def list_sources(self):
            return [
                SimpleNamespace(
                    episode_number=3,
                    source_revision=8,
                    content_hash="hash",
                    content="正文",
                )
            ]

    monkeypatch.setattr(
        director_plan,
        "_build_episode_source_store",
        lambda _ctx: _async(Repository()),
    )

    with pytest.raises(
        director_plan.DirectorPlanTaskError, match="SOURCE_REVISION_CONFLICT"
    ):
        await director_plan._build_director_plan_input(
            {"project_id": "project-1", "episode": 3, "source_revision": 7},
            SimpleNamespace(project_id="project-1"),
        )


async def _async(value):
    return value
