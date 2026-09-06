from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace


def test_run_core_fails_and_refunds_partial_failure_result(monkeypatch):
    from novelvideo.task_backend import run_core
    from novelvideo.task_backend.registry import register_project_task_runner

    partial_result = {
        "status": "partial_failure",
        "generated": 1,
        "errors": [{"beat": 2, "message": "provider failed"}],
    }
    refunds: list[tuple[str, dict]] = []
    confirmations: list[str] = []
    metric_outcomes: list[str] = []

    async def not_cancelled(**_kwargs):
        return False

    async def refund(reservation_id, *, metadata=None):
        refunds.append((reservation_id, metadata or {}))

    async def confirm(reservation_id, *, metadata=None):
        confirmations.append(reservation_id)

    async def emit_metrics(*_args, outcome="success", **_kwargs):
        metric_outcomes.append(outcome)

    class Manager:
        def __init__(self):
            self.failures: list[dict] = []
            self.completions: list[dict] = []

        def update_progress_for_project(self, *_args, **_kwargs):
            return None

        def fail_task_for_project(self, *_args, **kwargs):
            self.failures.append(kwargs)

        def complete_task_for_project(self, *_args, **kwargs):
            self.completions.append(kwargs)

    register_project_task_runner(
        "partial_failure_core_test", lambda _envelope, _ctx: partial_result
    )
    monkeypatch.setattr(run_core, "is_cancel_requested", not_cancelled)
    monkeypatch.setattr(run_core, "_refund_feature_credit_reservation", refund)
    monkeypatch.setattr(run_core, "_confirm_feature_credit_reservation", confirm)
    monkeypatch.setattr(run_core, "_emit_project_task_metrics", emit_metrics)
    monkeypatch.setattr(run_core, "_clear_project_task_metrics_context", lambda: None)
    monkeypatch.setattr(run_core, "_set_project_task_metrics_context", lambda *_a, **_k: None)
    monkeypatch.setattr(run_core, "_project_task_timeout_seconds", lambda: 0)
    monkeypatch.setattr(run_core, "project_task_run_context", lambda _task_id: nullcontext())
    monkeypatch.setattr(
        run_core, "project_task_subprocess_context", lambda **_kwargs: nullcontext()
    )
    monkeypatch.setattr(run_core, "_ensure_builtin_runners_registered", lambda: None)
    manager = Manager()

    result = run_core.run_project_task_core_sync(
        {
            "project_id": "project-1",
            "task_type": "partial_failure_core_test",
            "episode": 1,
        },
        SimpleNamespace(),
        manager,
        run_task_id="partial-task-1",
        metadata={"feature_credit_reservation_id": "reservation-1"},
    )

    assert result == {"failed": True, "result": partial_result}
    assert confirmations == []
    assert refunds == [
        (
            "reservation-1",
            {"source": "task_partial_failure", "result": partial_result},
        )
    ]
    assert metric_outcomes == ["failed"]
    assert manager.completions == []
    assert len(manager.failures) == 1
    assert manager.failures[0]["expected_task_id"] == "partial-task-1"
    assert manager.failures[0]["metadata"]["partial_failure_result"] == partial_result
