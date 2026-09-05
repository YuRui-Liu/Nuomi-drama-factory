from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest


def test_runner_registration_failure_marks_started_task_failed(monkeypatch):
    from novelvideo.task_backend import run_core

    failures: list[dict] = []

    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            return None

        def fail_task_for_project(self, *_args, **kwargs):
            failures.append(kwargs)

    async def not_cancelled(**_kwargs):
        return False

    async def no_refund(*_args, **_kwargs):
        return None

    monkeypatch.setattr(run_core, "is_cancel_requested", not_cancelled)
    monkeypatch.setattr(run_core, "_refund_feature_credit_reservation", no_refund)
    monkeypatch.setattr(run_core, "_clear_project_task_metrics_context", lambda: None)
    monkeypatch.setattr(run_core, "_set_project_task_metrics_context", lambda *_a, **_k: None)
    monkeypatch.setattr(run_core, "project_task_run_context", lambda _task_id: nullcontext())
    monkeypatch.setattr(
        run_core, "project_task_subprocess_context", lambda **_kwargs: nullcontext()
    )
    monkeypatch.setattr(
        run_core,
        "_ensure_builtin_runners_registered",
        lambda: (_ for _ in ()).throw(ImportError("broken optional runner")),
    )

    with pytest.raises(ImportError, match="broken optional runner"):
        run_core.run_project_task_core_sync(
            {"task_type": "episode_import", "project_id": "project-1", "episode": 0},
            SimpleNamespace(),
            Manager(),
            run_task_id="task-1",
        )

    assert len(failures) == 1
    assert "broken optional runner" in failures[0]["error"]
    assert failures[0]["expected_task_id"] == "task-1"
