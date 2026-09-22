import asyncio
from types import SimpleNamespace

import pytest

from novelvideo.narrative_groups.service import (
    advance_revision, group_beats, record_stage_result, save_groups, load_materialized_groups,
)
from novelvideo.task_backend import cancel
from novelvideo.task_backend.runners import narrative_group_video as runner


@pytest.fixture
def queued_video(tmp_path):
    save_groups(tmp_path, 1, group_beats([
        {"id": "beat-1", "beat_number": 1, "video_prompt": "He waits."},
    ]))
    advance_revision(tmp_path, 1, "ng-01", "video")
    return {
        "project_id": "cancel-test", "task_type": "narrative_group_video",
        "episode": 1, "__run_task_id": "cancel-test-task",
        "payload": {"group_id": "ng-01", "revision": 1},
    }, SimpleNamespace(output_dir=tmp_path)


def test_already_cancelled_video_never_starts_execution(queued_video, monkeypatch):
    envelope, ctx = queued_video
    started = []

    async def execute(*args):
        started.append(True)
        return {"status": "completed"}

    async def cancelled(**kwargs):
        return True

    monkeypatch.setattr(runner, "_execute_inner", execute)
    monkeypatch.setattr(cancel, "is_cancel_requested", cancelled)
    with pytest.raises(cancel.TaskCancelled):
        runner.run_narrative_group_video(envelope, ctx)
    assert started == []
    state = load_materialized_groups(ctx.output_dir, 1)[0].stages["video"]
    assert state.status == "failed"
    assert "cancel" in state.error.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("new_revision", [False, True])
async def test_cancellation_interrupts_work_and_preserves_new_revision(
    queued_video, monkeypatch, new_revision,
):
    envelope, ctx = queued_video
    started = asyncio.Event()
    interrupted = asyncio.Event()

    async def execute(*args):
        record_stage_result(ctx.output_dir, 1, "ng-01", "video",
                            expected_revision=1, status="running")
        if new_revision:
            advance_revision(ctx.output_dir, 1, "ng-01", "video", regenerate=True)
        started.set()
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            interrupted.set()
            raise
        return {"status": "completed"}

    async def cancelled(**kwargs):
        return started.is_set()

    monkeypatch.setattr(runner, "_execute_inner", execute)
    monkeypatch.setattr(cancel, "is_cancel_requested", cancelled)
    with pytest.raises(cancel.TaskCancelled):
        await runner._execute(envelope, ctx)
    assert interrupted.is_set()
    state = load_materialized_groups(ctx.output_dir, 1)[0].stages["video"]
    assert state.status == ("queued" if new_revision else "failed")
    assert state.revision == (2 if new_revision else 1)


@pytest.mark.asyncio
async def test_video_cancellation_reaches_codex_process_cleanup(queued_video, monkeypatch):
    from novelvideo.knowledge_runtime.codex_process import supervise_codex_process

    envelope, ctx = queued_video
    started = asyncio.Event()
    stopped = asyncio.Event()

    class Process:
        returncode = None

        async def communicate(self, prompt):
            started.set()
            await stopped.wait()
            return b"", b""

    process = Process()

    async def terminate(child):
        child.returncode = -15
        stopped.set()

    async def execute(*args):
        await supervise_codex_process(
            process, "test", ctx.output_dir / "unused-output.json", 3,
            terminate=terminate,
        )
        return {"status": "completed"}

    async def cancelled(**kwargs):
        return started.is_set()

    monkeypatch.setattr(runner, "_execute_inner", execute)
    monkeypatch.setattr(cancel, "is_cancel_requested", cancelled)
    with pytest.raises(cancel.TaskCancelled):
        await runner._execute(envelope, ctx)
    assert stopped.is_set()
    assert process.returncode == -15
