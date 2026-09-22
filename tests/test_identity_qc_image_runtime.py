from pathlib import Path
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from novelvideo.knowledge_runtime import codex
from novelvideo.character_visual import identity_sheet_qc as qc
from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
from novelvideo.text_task_runtime.runtime import CodexStructuredRuntime
from novelvideo.text_task_runtime.runtime import ModelApiStructuredRuntime
from novelvideo.text_task_runtime.settings import default_agent_task_route


class Answer(BaseModel):
    ok: bool


def test_identity_qc_default_route():
    route = default_agent_task_route("identity_sheet_qc")
    assert (route.runtime, route.model, route.reasoning_effort, route.fallback) == (
        "codex", "gpt-5.6-sol", "low", "stop"
    )


async def test_image_attachment_exists_during_execution_and_is_removed(monkeypatch):
    seen = []

    async def create(argv, **kwargs):
        path = Path(argv[argv.index("--image") + 1])
        assert path.read_bytes() == b"image"
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in argv and "--ignore-user-config" in argv
        seen.append(path)
        return object()

    async def supervise(*args):
        return SimpleNamespace(completed_from_final_message=True, output='{"ok":true}')

    monkeypatch.setattr(codex, "_create_codex_process", create)
    monkeypatch.setattr(codex, "supervise_codex_process", supervise)
    backend = codex.CodexCliStructuredBackend(codex_bin="codex")
    result = await backend.acreate_structured_output(
        "inspect", "", Answer, images=[codex.StructuredImage(b"image", "image/png")]
    )
    assert result.ok
    assert seen and not seen[0].exists()


@pytest.mark.parametrize("data,mime", [(b"", "image/png"), (b"a", "text/plain"), (bytearray(b"a"), "image/png")])
def test_invalid_image_rejected(data, mime):
    with pytest.raises(ValueError):
        codex.StructuredImage(data, mime)


async def test_explicit_qc_runtime_receives_image_and_keeps_advisory_policy(monkeypatch):
    gateway = AsyncMock(side_effect=AssertionError("gateway must not run"))
    monkeypatch.setattr(qc, "call_freezone_vision_model", gateway)
    payload = dict.fromkeys(qc._ISSUE_CODES, False)
    payload["dead_eyes"] = True
    runtime = SimpleNamespace(run_structured=AsyncMock(return_value=payload))
    report = await qc.assess_identity_sheet_quality(image_data=b"original", style="2d", runtime=runtime)
    assert report.passed and report.warnings == ["dead_eyes"]
    assert runtime.run_structured.call_args.kwargs["images"][0].data == b"original"
    gateway.assert_not_called()


async def test_runtime_invalid_output_fails_closed():
    runtime = SimpleNamespace(run_structured=AsyncMock(return_value={"body_cropped": False}))
    report = await qc.assess_identity_sheet_quality(image_data=b"original", style="2d", runtime=runtime)
    assert not report.passed and report.blocking_issues == ["qc_unavailable"]


async def test_codex_runtime_forwards_images():
    backend = SimpleNamespace(acreate_structured_output=AsyncMock(return_value=Answer(ok=True)))
    runtime = CodexStructuredRuntime(AgentTaskRouteSnapshot(task_role="identity_sheet_qc", source="task", runtime="codex", model="gpt-5.6-sol"), backend=backend)
    images = [codex.StructuredImage(b"image", "image/png")]
    await runtime.run_structured(prompt="inspect", output_type=Answer, images=images)
    assert backend.acreate_structured_output.call_args.kwargs["images"] == images


def test_invalid_attachment_object_is_rejected_cleanly():
    with pytest.raises(ValueError):
        codex.validate_structured_images([object()])


def test_oversized_image_is_rejected(monkeypatch):
    monkeypatch.setattr(codex, "MAX_STRUCTURED_IMAGE_BYTES", 3)
    with pytest.raises(ValueError):
        codex.StructuredImage(b"four", "image/png")


async def test_image_temp_files_removed_on_cancellation(monkeypatch):
    paths = []

    async def create(argv, **kwargs):
        paths.append(Path(argv[argv.index("--image") + 1]))
        return object()

    async def cancel(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(codex, "_create_codex_process", create)
    monkeypatch.setattr(codex, "supervise_codex_process", cancel)
    with pytest.raises(asyncio.CancelledError):
        await codex.CodexCliStructuredBackend().acreate_structured_output(
            "inspect", "", Answer, images=[codex.StructuredImage(b"image", "image/png")]
        )
    assert paths and not paths[0].parent.exists()


async def test_model_api_transports_binary_image(monkeypatch):
    from pydantic_ai import BinaryContent

    monkeypatch.setattr("novelvideo.config.get_newapi_text_pydantic_model", lambda *a, **k: "mock")
    agent = SimpleNamespace(run=AsyncMock(return_value=Answer(ok=True)))
    runtime = ModelApiStructuredRuntime(
        AgentTaskRouteSnapshot(task_role="identity_sheet_qc", source="task", runtime="model_api", model="vision"),
        agent_factory=lambda **kwargs: agent,
    )
    await runtime.run_structured(prompt="inspect", output_type=Answer, images=[codex.StructuredImage(b"original", "image/webp")])
    prompt = agent.run.call_args.args[0]
    assert prompt[0] == "inspect"
    assert isinstance(prompt[1], BinaryContent)
    assert prompt[1].data == b"original" and prompt[1].media_type == "image/webp"


def test_fingerprint_tracks_route_and_policy(monkeypatch):
    route = AgentTaskRouteSnapshot(task_role="identity_sheet_qc", source="task", runtime="codex", model="gpt-5.6-sol")
    runtime = SimpleNamespace(snapshot=route)
    original = qc.identity_sheet_qc_policy_fingerprint(runtime)
    assert original == qc.identity_sheet_qc_policy_fingerprint(runtime)
    assert original != qc.identity_sheet_qc_policy_fingerprint(SimpleNamespace(snapshot=route.model_copy(update={"model": "other"})))
    monkeypatch.setattr(qc, "_ADVISORY_CODES", frozenset())
    assert original != qc.identity_sheet_qc_policy_fingerprint(runtime)
