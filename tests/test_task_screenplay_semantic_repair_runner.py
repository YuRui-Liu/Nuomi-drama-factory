from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.task_backend.registry import get_project_task_runner_registration
from novelvideo.task_backend.runners import screenplay_semantic_repair as runner


def _report(*, passed: bool, issues: list[SimpleNamespace]):
    return SimpleNamespace(
        passed=passed,
        issues=tuple(issues),
        model_dump=lambda **kwargs: {
            "passed": passed,
            "issues": [vars(issue) for issue in issues],
        },
    )


def _revision(revision_id: str, *, source_revision: int = 7, issues=()):
    return SimpleNamespace(
        revision_id=revision_id,
        episode=1,
        source_revision=source_revision,
        status="review_required",
        validation_report=_report(passed=not issues, issues=list(issues)),
    )


def test_runner_registers_dedicated_text_task_role():
    registration = get_project_task_runner_registration("screenplay_semantic_repair")

    assert registration is not None
    assert registration.text_task_role == "screenplay_semantic_repair"
    assert registration.runner is runner.run_screenplay_semantic_repair


@pytest.mark.asyncio
async def test_runner_repairs_frozen_base_and_returns_progress_summary(monkeypatch):
    source = SimpleNamespace(episode_number=1, source_revision=7)
    repository = SimpleNamespace()
    calls = 0

    async def list_sources():
        nonlocal calls
        calls += 1
        return [source]

    repository.list_sources = list_sources
    initial_issues = [
        SimpleNamespace(
            severity="error", scene_id="scene-1", code="missing_result"
        ),
        SimpleNamespace(
            severity="error", scene_id="scene-2", code="missing_goal"
        ),
    ]
    base = _revision("sem-base", issues=initial_issues)
    remaining = [
        SimpleNamespace(
            severity="error", scene_id="scene-2", code="repair_contract_violation"
        )
    ]
    child = _revision("sem-child", issues=remaining)

    class Store:
        def load(self, episode, revision_id):
            assert episode == 1
            assert revision_id == "sem-base"
            return base

    progress_callbacks = []

    class Service:
        async def repair(
            self,
            value,
            *,
            max_rounds,
            concurrency,
            before_commit,
            on_progress,
        ):
            assert value is base
            assert max_rounds == 2
            assert concurrency == 3
            await before_commit()
            progress = SimpleNamespace(
                repair_round=2,
                max_rounds=2,
                pending_scene_ids=("scene-2",),
                completed_scene_ids=("scene-1", "scene-2"),
            )
            progress_callbacks.append(progress)
            on_progress(progress)
            return child

    updates = []
    manager = SimpleNamespace(
        update_progress_for_project=lambda *args, **kwargs: updates.append(kwargs)
    )

    async def build_repository(ctx):
        return repository

    monkeypatch.setattr(runner, "_build_episode_source_store", build_repository)
    monkeypatch.setattr(runner, "_build_store", lambda ctx: Store())
    monkeypatch.setattr(runner, "_build_service", lambda store: Service())
    monkeypatch.setattr(runner, "get_task_manager", lambda: manager)

    result = await runner._run_screenplay_semantic_repair(
        {
            "__run_task_id": "task-1",
            "payload": {
                "project_id": "project-1",
                "episode": 1,
                "source_revision": 7,
                "semantic_revision_id": "sem-base",
                "max_rounds": 2,
                "concurrency": 3,
            },
        },
        SimpleNamespace(project_id="project-1"),
    )

    assert calls == 2
    assert progress_callbacks
    assert result["base_semantic_revision_id"] == "sem-base"
    assert result["semantic_revision_id"] == "sem-child"
    assert result["rounds"] == 2
    assert result["targeted_scenes"] == 2
    assert result["repaired_scenes"] == 1
    assert result["failed_scenes"] == 1
    assert result["remaining_issues"] == 1
    assert result["contract_violations"] == 1
    assert updates[-1]["expected_task_id"] == "task-1"


@pytest.mark.asyncio
async def test_runner_rejects_source_change_before_repair(monkeypatch):
    repository = SimpleNamespace()

    async def list_sources():
        return [SimpleNamespace(episode_number=1, source_revision=8)]

    repository.list_sources = list_sources

    async def build_repository(ctx):
        return repository

    monkeypatch.setattr(runner, "_build_episode_source_store", build_repository)

    with pytest.raises(runner.ScreenplaySemanticRepairTaskError, match="SOURCE_REVISION_CONFLICT"):
        await runner._run_screenplay_semantic_repair(
            {
                "payload": {
                    "project_id": "project-1",
                    "episode": 1,
                    "source_revision": 7,
                    "semantic_revision_id": "sem-base",
                }
            },
            SimpleNamespace(project_id="project-1"),
        )


@pytest.mark.asyncio
async def test_runner_rejects_semantic_base_change_before_commit(monkeypatch):
    repository = SimpleNamespace()

    async def list_sources():
        return [SimpleNamespace(episode_number=1, source_revision=7)]

    repository.list_sources = list_sources
    base = _revision("sem-base")
    loads = iter((base, None))

    class Store:
        def load(self, episode, revision_id):
            return next(loads)

    class Service:
        async def repair(self, value, **kwargs):
            await kwargs["before_commit"]()
            raise AssertionError("commit guard should have raised")

    async def build_repository(ctx):
        return repository

    monkeypatch.setattr(runner, "_build_episode_source_store", build_repository)
    monkeypatch.setattr(runner, "_build_store", lambda ctx: Store())
    monkeypatch.setattr(runner, "_build_service", lambda store: Service())
    monkeypatch.setattr(
        runner,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *a, **k: None),
    )

    with pytest.raises(
        runner.ScreenplaySemanticRepairTaskError,
        match="SEMANTIC_REVISION_CONFLICT",
    ):
        await runner._run_screenplay_semantic_repair(
            {
                "payload": {
                    "project_id": "project-1",
                    "episode": 1,
                    "source_revision": 7,
                    "semantic_revision_id": "sem-base",
                    "max_rounds": 2,
                    "concurrency": 3,
                }
            },
            SimpleNamespace(project_id="project-1"),
        )
