"""Residue cleanup for narrative-group stages whose task already ended.

Regression origin: 任务中心显示“空闲”，但叙事组的 video stage 仍停在
``running``，于是「生成组合视频」按钮被永久禁用。
"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api.routes import narrative_groups
from novelvideo.narrative_groups.service import (
    group_beats,
    load_groups,
    save_groups,
)


class FakeTask:
    def __init__(self, task_type: str, scope: str, status: str):
        self.task_type = task_type
        self.scope = scope
        self.status = status


class FakeManager:
    def __init__(self, tasks):
        self._tasks = list(tasks)

    def list_tasks_for_project(self, ctx):
        return list(self._tasks)


def _resolved(tmp_path: Path):
    return SimpleNamespace(project_dir=tmp_path, ctx=SimpleNamespace(project_id="project-1"))


def _saved_group(tmp_path: Path, *, stage: str = "video", status: str = "running", revision: int = 3):
    group = group_beats([{"id": "shot-07-01"}])[0]
    stages = dict(group.stages)
    stages[stage] = replace(stages[stage], status=status, revision=revision)
    save_groups(tmp_path, 1, [replace(group, stages=stages)])
    return load_groups(tmp_path, 1)[0]


def _install(monkeypatch, tasks):
    monkeypatch.setattr(narrative_groups, "get_task_manager", lambda: FakeManager(tasks))


def test_running_stage_without_any_task_is_reset_to_failed(monkeypatch, tmp_path):
    group = _saved_group(tmp_path)
    _install(monkeypatch, [])

    result = narrative_groups._reconcile_orphan_stages(_resolved(tmp_path), 1, [group])

    assert result[0].stages["video"].status == "failed"
    assert "任务中心已空闲" in result[0].stages["video"].error
    # The fix must be durable, not just a prettier response body.
    assert load_groups(tmp_path, 1)[0].stages["video"].status == "failed"


def test_running_stage_with_live_task_is_left_alone(monkeypatch, tmp_path):
    group = _saved_group(tmp_path, revision=3)
    scope = narrative_groups._stage_task_scope(group.id, "video", 3)
    _install(monkeypatch, [FakeTask("narrative_group_video", scope, "running")])

    result = narrative_groups._reconcile_orphan_stages(_resolved(tmp_path), 1, [group])

    assert result[0].stages["video"].status == "running"
    assert load_groups(tmp_path, 1)[0].stages["video"].status == "running"


def test_running_stage_with_terminal_task_is_reset(monkeypatch, tmp_path):
    group = _saved_group(tmp_path, revision=3)
    scope = narrative_groups._stage_task_scope(group.id, "video", 3)
    _install(monkeypatch, [FakeTask("narrative_group_video", scope, "completed")])

    result = narrative_groups._reconcile_orphan_stages(_resolved(tmp_path), 1, [group])

    assert result[0].stages["video"].status == "failed"


def test_running_stage_ignores_a_task_for_a_different_revision(monkeypatch, tmp_path):
    group = _saved_group(tmp_path, revision=3)
    stale_scope = narrative_groups._stage_task_scope(group.id, "video", 2)
    _install(monkeypatch, [FakeTask("narrative_group_video", stale_scope, "running")])

    result = narrative_groups._reconcile_orphan_stages(_resolved(tmp_path), 1, [group])

    assert result[0].stages["video"].status == "failed"


def test_queued_stage_without_task_record_stays_queued(monkeypatch, tmp_path):
    # Enqueue→task-record is not atomic, so an absent record must not flap the
    # stage to failed the moment the user presses the button.
    group = _saved_group(tmp_path, status="queued")
    _install(monkeypatch, [])

    result = narrative_groups._reconcile_orphan_stages(_resolved(tmp_path), 1, [group])

    assert result[0].stages["video"].status == "queued"


def test_queued_stage_with_terminal_task_record_is_reset(monkeypatch, tmp_path):
    group = _saved_group(tmp_path, status="queued", revision=1)
    scope = narrative_groups._stage_task_scope(group.id, "video", 1)
    _install(monkeypatch, [FakeTask("narrative_group_video", scope, "failed")])

    result = narrative_groups._reconcile_orphan_stages(_resolved(tmp_path), 1, [group])

    assert result[0].stages["video"].status == "failed"


@pytest.mark.parametrize(
    "stage,task_type",
    [("sketch", "narrative_group_grid"), ("render", "narrative_group_split"), ("render", "narrative_group_grid")],
)
def test_each_stage_accepts_its_own_task_types(monkeypatch, tmp_path, stage, task_type):
    group = _saved_group(tmp_path, stage=stage, revision=2)
    scope = narrative_groups._stage_task_scope(group.id, stage, 2)
    _install(monkeypatch, [FakeTask(task_type, scope, "running")])

    result = narrative_groups._reconcile_orphan_stages(_resolved(tmp_path), 1, [group])

    assert result[0].stages[stage].status == "running"


def test_unreadable_task_state_leaves_durable_state_untouched(monkeypatch, tmp_path):
    group = _saved_group(tmp_path)

    class Broken:
        def list_tasks_for_project(self, ctx):
            raise RuntimeError("task store unavailable")

    monkeypatch.setattr(narrative_groups, "get_task_manager", lambda: Broken())

    result = narrative_groups._reconcile_orphan_stages(_resolved(tmp_path), 1, [group])

    assert result[0].stages["video"].status == "running"
    assert load_groups(tmp_path, 1)[0].stages["video"].status == "running"


def test_no_live_stage_short_circuits_without_touching_the_task_store(monkeypatch, tmp_path):
    group = _saved_group(tmp_path, status="completed")
    calls = []

    class Counting:
        def list_tasks_for_project(self, ctx):
            calls.append(ctx)
            return []

    monkeypatch.setattr(narrative_groups, "get_task_manager", lambda: Counting())

    result = narrative_groups._reconcile_orphan_stages(_resolved(tmp_path), 1, [group])

    assert result[0].stages["video"].status == "completed"
    assert calls == []
