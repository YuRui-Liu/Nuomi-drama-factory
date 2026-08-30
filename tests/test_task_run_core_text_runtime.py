from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

from novelvideo.task_backend.registry import (
    get_project_task_runner,
    get_project_task_runner_registration,
    register_project_task_runner,
)
from novelvideo.text_task_runtime.models import (
    AgentTaskRouteOverride,
    AgentTaskRoutingConfig,
)
from novelvideo.text_task_runtime.runtime import current_text_task_runtime


def test_runner_registration_keeps_callable_api_and_exposes_text_role():
    def runner(envelope, ctx):
        return {"ok": True}

    register_project_task_runner(
        "text_runtime_registration_test",
        runner,
        text_task_role="director_plan",
    )

    assert get_project_task_runner("text_runtime_registration_test") is runner
    registration = get_project_task_runner_registration(
        "text_runtime_registration_test"
    )
    assert registration is not None
    assert registration.runner is runner
    assert registration.text_task_role == "director_plan"


async def test_enqueue_freezes_task_override_into_envelope(monkeypatch):
    from novelvideo.ports.local import tasks

    def runner(envelope, ctx):
        return {"ok": True}

    register_project_task_runner(
        "text_runtime_enqueue_test", runner, text_task_role="director_plan"
    )
    monkeypatch.setattr(tasks, "_ensure_builtin_runners_registered", lambda: None)
    monkeypatch.setattr(
        "novelvideo.text_task_runtime.settings.load_global_routes",
        lambda: AgentTaskRoutingConfig(
            routes={
                "director_plan": AgentTaskRouteOverride(
                    runtime="model_api", model="deepseek-v4-flash"
                )
            }
        ),
    )
    monkeypatch.setattr(
        "novelvideo.text_task_runtime.settings.load_project_routes",
        lambda ctx: AgentTaskRoutingConfig(),
    )

    state = SimpleNamespace(task_id="task-1", status="queued", metadata={})

    class Manager:
        def reserve_task_for_project(self, *args, **kwargs):
            return state, True

        def update_progress_for_project(self, *args, **kwargs):
            return None

    monkeypatch.setattr(tasks, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(tasks, "require_project_home_node", lambda ctx, **kwargs: ctx)
    backend = tasks.InlineTaskBackend()
    captured = []
    monkeypatch.setattr(backend, "_submit_lane_job", captured.append)
    ctx = SimpleNamespace(
        project_id="project-1", requester_user_id="user-1", state_dir="state"
    )

    await backend.enqueue_project_task(
        ctx,
        task_type="text_runtime_enqueue_test",
        payload={
            "agent_route_override": {
                "runtime": "codex",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
            }
        },
    )

    snapshot = captured[0].envelope["agent_route_snapshot"]
    assert snapshot["runtime"] == "codex"
    assert snapshot["source"] == "task"
    assert "api_key" not in snapshot


async def test_run_core_isolates_frozen_routes_and_records_metadata(monkeypatch):
    from novelvideo.task_backend import run_core

    async def not_cancelled(**kwargs):
        return False

    async def no_async_work(*args, **kwargs):
        return None

    monkeypatch.setattr(run_core, "is_cancel_requested", not_cancelled)
    monkeypatch.setattr(run_core, "_emit_project_task_metrics", no_async_work)
    monkeypatch.setattr(run_core, "_confirm_feature_credit_reservation", no_async_work)
    monkeypatch.setattr(run_core, "_refund_feature_credit_reservation", no_async_work)
    monkeypatch.setattr(run_core, "_clear_project_task_metrics_context", lambda: None)
    monkeypatch.setattr(run_core, "_set_project_task_metrics_context", lambda *a, **k: None)
    monkeypatch.setattr(run_core, "_project_task_timeout_seconds", lambda: 0)
    monkeypatch.setattr(run_core, "project_task_run_context", lambda task_id: nullcontext())
    monkeypatch.setattr(
        run_core, "project_task_subprocess_context", lambda **kwargs: nullcontext()
    )
    monkeypatch.setattr(run_core, "_ensure_builtin_runners_registered", lambda: None)

    completions = []

    class Manager:
        def update_progress_for_project(self, *args, **kwargs):
            return None

        def fail_task_for_project(self, *args, **kwargs):
            raise AssertionError(kwargs)

        def complete_task_for_project(self, *args, **kwargs):
            completions.append(kwargs)

    def routed_runner(envelope, ctx):
        runtime = current_text_task_runtime()
        assert runtime is not None
        return {"runtime": runtime.snapshot.runtime}

    register_project_task_runner(
        "text_runtime_core_codex", routed_runner, text_task_role="director_plan"
    )
    register_project_task_runner(
        "text_runtime_core_api", routed_runner, text_task_role="director_plan"
    )

    def envelope(task_type, runtime):
        return {
            "project_id": "project-1",
            "task_type": task_type,
            "episode": 0,
            "agent_route_snapshot": {
                "task_role": "director_plan",
                "source": "task",
                "runtime": runtime,
                "model": "model",
                "fallback": "stop",
            },
        }

    results = await asyncio.gather(
        asyncio.to_thread(
            run_core.run_project_task_core_sync,
            envelope("text_runtime_core_codex", "codex"),
            SimpleNamespace(),
            Manager(),
            run_task_id="task-codex",
        ),
        asyncio.to_thread(
            run_core.run_project_task_core_sync,
            envelope("text_runtime_core_api", "model_api"),
            SimpleNamespace(),
            Manager(),
            run_task_id="task-api",
        ),
    )

    assert {item["runtime"] for item in results} == {"codex", "model_api"}
    assert current_text_task_runtime() is None
    assert {item["metadata"]["agent_route_snapshot"]["runtime"] for item in completions} == {
        "codex",
        "model_api",
    }
