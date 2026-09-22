from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_recheck_endpoint_reuses_pass_without_enqueue(monkeypatch):
    from novelvideo.api.routes import identity_qc as api
    ctx = SimpleNamespace(state_dir="unused")
    monkeypatch.setattr(api, "resolve_project_scope", AsyncMock(return_value=SimpleNamespace(ctx=ctx)))
    monkeypatch.setattr(api, "prepare_recheck", lambda *a, **k: {"image_sha256": "abc"})
    monkeypatch.setattr(api, "_route_and_style", lambda c: (SimpleNamespace(model_dump=lambda **k: {}), "anime", "fp", "2d"))
    monkeypatch.setattr(api, "recheck_cache", lambda *a: {"qc_passed": True, "version_id": "v"})
    monkeypatch.setattr(api, "get_task_backend", lambda: pytest.fail("must not enqueue"))
    result = await api.recheck_identity("p", "n", "i", "v", api.IdentityRecheckRequest(), {})
    assert result["reused"] is True


@pytest.mark.asyncio
async def test_failed_recheck_requires_explicit_retry(monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import identity_qc as api
    monkeypatch.setattr(api, "resolve_project_scope", AsyncMock(return_value=SimpleNamespace(ctx=object())))
    monkeypatch.setattr(api, "prepare_recheck", lambda *a, **k: {"image_sha256": "abc"})
    monkeypatch.setattr(api, "_route_and_style", lambda c: (object(), "anime", "fp", "2d"))
    monkeypatch.setattr(api, "recheck_cache", lambda *a: {"qc_passed": False})
    monkeypatch.setattr(api, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *a, **k: None))
    with pytest.raises(HTTPException) as error:
        await api.recheck_identity("p", "n", "i", "v", api.IdentityRecheckRequest(), {})
    assert error.value.status_code == 409


def test_cancelled_qc_does_not_publish(monkeypatch):
    from novelvideo.task_backend.runners import character_qc
    async def assess(*a):
        return object()
    monkeypatch.setattr(character_qc, "_assess", assess)
    monkeypatch.setattr(character_qc, "raise_if_envelope_cancel_requested", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cancelled")))
    monkeypatch.setattr(character_qc, "publish_recheck", lambda *a, **k: pytest.fail("cancelled publication"))
    with pytest.raises(RuntimeError, match="cancelled"):
        character_qc.run_character_qc({"payload": {}}, object())


def test_new_identity_generation_has_frozen_qc_route():
    from novelvideo.task_backend.registry import get_project_task_runner_registration
    import novelvideo.task_backend.runners  # noqa: F401
    assert get_project_task_runner_registration("identity_image").text_task_role == "identity_sheet_qc"


def test_qc_role_is_configurable_in_settings():
    from novelvideo.api.routes.model_gateway import TEXT_TASK_ROLE_LABELS
    assert "identity_sheet_qc" in TEXT_TASK_ROLE_LABELS


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [None, {"qc_passed": False}])
async def test_running_recheck_reuses_task(monkeypatch, cached):
    from novelvideo.api.routes import identity_qc as api
    monkeypatch.setattr(api, "resolve_project_scope", AsyncMock(return_value=SimpleNamespace(ctx=object())))
    monkeypatch.setattr(api, "prepare_recheck", lambda *a, **k: {"image_sha256": "abc"})
    monkeypatch.setattr(api, "_route_and_style", lambda c: (object(), "anime", "fp", "2d"))
    monkeypatch.setattr(api, "recheck_cache", lambda *a: cached)
    monkeypatch.setattr(api, "get_task_manager", lambda: SimpleNamespace(
        get_task_for_project=lambda *a, **k: SimpleNamespace(status="running", task_id="existing")))
    monkeypatch.setattr(api, "get_task_backend", lambda: pytest.fail("must not enqueue twice"))
    result = await api.recheck_identity("p", "n", "i", "v", api.IdentityRecheckRequest(), {})
    assert result["task_id"] == "existing"


@pytest.mark.asyncio
async def test_recheck_enqueues_frozen_route(monkeypatch):
    from novelvideo.api.routes import identity_qc as api
    from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
    route = AgentTaskRouteSnapshot(task_role="identity_sheet_qc", source="global", runtime="codex", model="gpt-5.6-sol")
    monkeypatch.setattr(api, "resolve_project_scope", AsyncMock(return_value=SimpleNamespace(ctx=object())))
    monkeypatch.setattr(api, "prepare_recheck", lambda *a, **k: {"image_sha256": "abc"})
    monkeypatch.setattr(api, "_route_and_style", lambda c: (route, "anime", "fp", "2d"))
    monkeypatch.setattr(api, "recheck_cache", lambda *a: None)
    monkeypatch.setattr(api, "get_task_manager", lambda: SimpleNamespace(get_task_for_project=lambda *a, **k: None))
    enqueue = AsyncMock(return_value=SimpleNamespace(task_state=SimpleNamespace(task_id="new"), backend="inline"))
    monkeypatch.setattr(api, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))
    result = await api.recheck_identity("p", "n", "i", "v", api.IdentityRecheckRequest(), {})
    assert result["task_id"] == "new"
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["target"]["image_sha256"] == "abc"
    assert payload["agent_route_override"]["model"] == route.model
    assert payload["qc_route"]["source"] == "global"
    assert payload["style_family"] == "2d"


@pytest.mark.asyncio
async def test_runner_uses_frozen_style_family(monkeypatch):
    from novelvideo.task_backend.runners import character_qc as runner
    from novelvideo.character_visual.identity_sheet import IdentitySheetStyleFamily
    monkeypatch.setattr(runner, "current_text_task_runtime", lambda: SimpleNamespace(
        snapshot=SimpleNamespace(task_role="identity_sheet_qc")))
    monkeypatch.setattr(runner, "read_recheck_image", lambda *a: b"image")
    assessment = AsyncMock(return_value="report")
    monkeypatch.setattr(runner, "assess_identity_sheet_quality", assessment)
    async def watch(awaitable, *args, **kwargs):
        return await awaitable
    monkeypatch.setattr(runner, "await_envelope_with_cancel_watch", watch)
    result = await runner._assess({"payload": {"target": {}, "style": "custom", "style_family": "2d"}},
                                 SimpleNamespace(output_dir="unused"))
    assert result == "report"
    assert assessment.call_args.kwargs["style_family"] is IdentitySheetStyleFamily.TWO_D
