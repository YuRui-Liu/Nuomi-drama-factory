from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.freezone.video_director.models import (
    CanvasBaseWire, DirectorDraft, DirectorImage, DirectorSegment, OptimizedDirector, OptimizedSegment,
)
from novelvideo.freezone.video_director.service import DirectorService
from novelvideo.media_capabilities.video.h3_wire import compile_h3_wire
from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubError


def png(color):
    out = BytesIO()
    Image.new("RGB", (2, 2), color).save(out, format="PNG")
    return out.getvalue()


def draft(url, *, sha256="wrong"):
    return DirectorDraft(revision=4, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="one", prompt="Walk", duration_seconds=5,
                        first_frame=DirectorImage(image_id="image", url=url, sha256=sha256)),
    ))


async def fake_optimize(runtime, source, *, frozen_images, reference_limit):
    runtime.append(frozen_images["image"].data)
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(source).timeline[0]
    wire = CanvasBaseWire(mode="i2va", duration_seconds=aligned.duration_seconds,
                          integrated_multimodal_description="[Shot 1] Walk",
                          overall_soundscape="Footsteps", non_diegetic_music="N/A")
    return OptimizedDirector(revision=source.revision, route="h3", profile_id="minimax-h3-director",
                             profile_version=15, optimized_at=datetime.now(timezone.utc),
                             segments=(OptimizedSegment(segment_id="one", mode="i2va",
                                 requested_duration_seconds=5, duration_seconds=aligned.duration_seconds,
                                 frames=aligned.frames, wire=wire, prompt=compile_h3_wire(wire)),))


class FakeProvider:
    def __init__(self):
        self.submit_calls = 0
        self.query_calls = 0
        self.download_calls = 0
        self.query_status = "queued"
        self.fail_submit = None
        self.fail_query = False

    async def prepare(self, source, optimized, paths, reference_limit):
        return {"workflow_id": "123", "profile_id": "h3", "node_info": [], "uploaded": list(paths)}

    async def submit(self, prepared):
        self.submit_calls += 1
        if self.fail_submit:
            raise self.fail_submit
        return "paid-task"

    async def query(self, task_id):
        self.query_calls += 1
        if self.fail_query:
            raise TimeoutError("query outage")
        return SimpleNamespace(status=self.query_status, results=(SimpleNamespace(url="https://output.test/video.mp4", node_id="7"),))

    async def download(self, url):
        self.download_calls += 1
        return b"video"


@pytest.fixture
def setup(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    ctx = SimpleNamespace(project_id="project-1", owner_username="alice", project_name="show",
                          output_dir=output, runtime_dir=runtime)
    provider = FakeProvider()
    images = []
    service = DirectorService(ctx, provider=provider, runtime=images, optimizer=fake_optimize)
    return ctx, service, provider, images


@pytest.mark.asyncio
async def test_freezes_real_bytes_and_duplicate_resume_never_resubmits(setup):
    ctx, service, provider, images = setup
    source = ctx.output_dir / "freezone" / "frame.png"
    source.parent.mkdir()
    source.write_bytes(png("red"))
    first, created = service.create("canvas", "node", "click", draft("freezone/frame.png"))
    assert created and first["snapshot"]["segments"][0]["first_frame"]["sha256"] != "wrong"
    source.write_bytes(png("blue"))
    assert service.create("canvas", "node", "click", draft("freezone/frame.png"))[0]["id"] == first["id"]
    source.unlink()
    assert service.create("canvas", "node", "click", draft("freezone/frame.png"))[0]["id"] == first["id"]
    await service.resume(first["id"])
    await service.resume(first["id"])
    assert images == [png("red")]
    assert provider.submit_calls == 1 and provider.query_calls == 2


@pytest.mark.asyncio
async def test_ambiguous_submit_is_unknown_after_restart(setup):
    ctx, service, provider, _ = setup
    source = ctx.output_dir / "frame.png"
    source.write_bytes(png("red"))
    attempt, _ = service.create("c", "n", "r", draft("frame.png"))
    provider.fail_submit = TimeoutError("response lost")
    result = await service.resume(attempt["id"])
    assert result["stage"] == "submission_unknown"
    assert (await DirectorService(ctx, provider=provider, runtime=[], optimizer=fake_optimize).resume(attempt["id"]))["stage"] == "submission_unknown"
    assert provider.submit_calls == 1
    with pytest.raises(ValueError, match="unknown"):
        service.retry(attempt["id"])


@pytest.mark.parametrize("url", [
    "https://evil.test/frame.png", "/static/projects/other/frame.png",
    "/api/v1/projects/other/media/frame.png", "/static/bob/show/frame.png",
])
def test_rejects_foreign_image_sources(setup, url):
    _, service, _, _ = setup
    with pytest.raises(ValueError):
        service.create("c", "n", "r", draft(url))


def test_rejects_same_image_id_with_conflicting_identity(setup):
    ctx, service, _, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    source = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p",
                           segments=(DirectorSegment(id="s1", prompt="Walk", duration_seconds=5,
                               first_frame=DirectorImage(image_id="same", url="frame.png")),
                               DirectorSegment(id="s2", prompt="Run", duration_seconds=5,
                               first_frame=DirectorImage(image_id="same", url="different.png"))))
    with pytest.raises(ValueError, match="conflicting image identity"):
        service.create("c", "n", "r", source)


@pytest.mark.asyncio
async def test_concurrent_resume_submits_once(setup):
    import asyncio
    ctx, service, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))
    await asyncio.gather(service.resume(item["id"]), service.resume(item["id"]))
    assert provider.submit_calls == 1


@pytest.mark.asyncio
async def test_optimizer_diagnostics_keep_only_chained_validation_locations_and_types(setup):
    import json

    from pydantic import BaseModel, ValidationError, field_validator
    from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError

    class Output(BaseModel):
        value: str

        @field_validator("value")
        @classmethod
        def reject(cls, value):
            raise ValueError("secret-validation-context")

    ctx, service, _, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))

    async def fail(*args, **kwargs):
        try:
            Output.model_validate({"value": "secret-validation-input"})
        except ValidationError as exc:
            raise KnowledgeRuntimeError("secret-runtime-message", code="CODEX_STRUCTURED_OUTPUT_INVALID") from exc

    service.optimizer = fail
    await service.resume(item["id"])
    stored = service.store.get(item["id"])
    assert stored["optimization_validation_errors"] == [{"loc": ["<redacted>"], "type": "value_error"}]
    assert "secret-" not in json.dumps(stored)


@pytest.mark.asyncio
async def test_optimizer_diagnostics_keep_locations_without_exception_content(setup):
    import json

    from novelvideo.api.routes.freezone_video_director import _public
    from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError

    ctx, service, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))

    async def fail_with_private_content(*args, **kwargs):
        private_prompt = "private-prompt-sentinel"
        raise KnowledgeRuntimeError(private_prompt, code="private-code-sentinel")

    service.optimizer = fail_with_private_content
    await service.resume(item["id"])
    stored = service.store.get(item["id"])
    assert stored["optimization_error_type"] == "KnowledgeRuntimeError"
    assert stored["optimization_error_code"] is None
    last = stored["optimization_error_trace"][-1]
    assert last["filename"] == "test_service.py"
    assert last["function"] == "fail_with_private_content"
    assert isinstance(last["line"], int) and last["line"] > 0
    assert "private-prompt-sentinel" not in json.dumps(stored)
    assert "private-code-sentinel" not in json.dumps(stored)
    assert not any(key.startswith("optimization_") for key in _public(stored))
    assert provider.submit_calls == 0


def test_validation_diagnostics_redact_input_keys_but_keep_schema_locations():
    from pydantic import ValidationError
    from novelvideo.freezone.video_director.models import DirectorDraft
    from novelvideo.freezone.video_director.service import _validation_locations

    try:
        DirectorDraft.model_validate({
            "revision": 1, "aspect_ratio": "16:9", "resolution": "720p",
            "secret-input-key": "private-value", "segments": [{"duration_seconds": "bad"}],
        })
    except ValidationError as exc:
        errors = _validation_locations(exc)
    assert {"loc": ["<redacted>"], "type": "extra_forbidden"} in errors
    assert {"loc": ["segments", 0, "duration_seconds"], "type": "float_parsing"} in errors
    assert "secret-input-key" not in str(errors)


@pytest.mark.asyncio
async def test_optimizer_diagnostic_trace_is_bounded(setup):
    ctx, service, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))

    def nested(depth):
        if depth:
            return nested(depth - 1)
        raise RuntimeError("private-trace-message")

    async def fail(*args, **kwargs):
        nested(50)

    service.optimizer = fail
    failed = await service.resume(item["id"])
    assert len(failed["optimization_error_trace"]) == 32
    assert provider.submit_calls == 0


@pytest.mark.asyncio
async def test_deepseek_image_rejection_explains_runtime_change_without_submitting(setup):
    from novelvideo.text_task_runtime.deepseek_harness import DeepSeekHarnessStructuredRuntime
    from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot

    ctx, _, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    runtime = DeepSeekHarnessStructuredRuntime(AgentTaskRouteSnapshot(
        runtime="deepseek_harness", task_role="director_plan", source="global",
    ))
    service = DirectorService(ctx, provider=provider, runtime=runtime)
    item, _ = service.create("c", "n", "r", draft("frame.png"))

    failed = await service.resume(item["id"])

    assert failed["stage"] == "failed"
    assert failed["failed_stage"] == "optimizing"
    assert provider.submit_calls == 0
    assert failed["optimization_error_code"] == "DSH_IMAGES_UNSUPPORTED"
    assert failed["error"] == (
        "当前导演提示词运行时 DeepSeek Harness 不支持图片输入；"
        "请将导演规划运行时切换为 Codex 或支持图片的模型 API 后重试。"
    )


@pytest.mark.asyncio
async def test_optimizer_failure_never_submits_and_linked_retry_can_succeed(setup):
    ctx, service, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))

    async def fail(*args, **kwargs):
        raise RuntimeError("optimizer down")

    service.optimizer = fail
    failed = await service.resume(item["id"])
    assert failed["failed_stage"] == "optimizing" and provider.submit_calls == 0
    assert failed["error"] == "Director optimization failed"
    retry, _ = service.retry(item["id"])
    service.optimizer = fake_optimize
    assert (await service.resume(retry["id"]))["provider_task_id"] == "paid-task"
    assert retry["parent_attempt_id"] == item["id"]


@pytest.mark.asyncio
async def test_known_submit_rejection_reuses_optimization_on_retry(setup):
    ctx, service, provider, images = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))
    provider.fail_submit = RunningHubError("rejected", code="BAD_INPUT", http_status=200)
    failed = await service.resume(item["id"])
    assert failed["stage"] == "failed" and failed["failed_stage"] == "submitting"
    provider.fail_submit = None
    child, _ = service.retry(item["id"])
    await service.resume(child["id"])
    assert provider.submit_calls == 2 and len(images) == 1


@pytest.mark.asyncio
async def test_download_retry_keeps_provider_task_and_remote_url(setup):
    ctx, service, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))
    provider.query_status = "succeeded"
    failed = await service.resume(item["id"])
    assert failed["failed_stage"] == "downloading" and failed["remote_url"]
    service.probe = lambda path: __import__("asyncio").sleep(0, result=SimpleNamespace(
        duration=5, width=1280, height=736))
    retry, _ = service.retry(item["id"])
    completed = await service.resume(retry["id"])
    assert completed["stage"] == "completed" and completed["result_url"]
    assert (await service.resume(retry["id"]))["stage"] == "completed"
    assert provider.submit_calls == 1 and provider.query_calls == 1 and provider.download_calls == 2


@pytest.mark.asyncio
async def test_provider_failure_creates_linked_attempt_with_frozen_images(setup):
    ctx, service, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))
    provider.query_status = "failed"
    failed = await service.resume(item["id"])
    assert failed["failed_stage"] == "generating"
    (ctx.output_dir / "frame.png").unlink()
    child, _ = service.retry(item["id"])
    provider.query_status = "queued"
    resumed = await service.resume(child["id"])
    assert resumed["provider_task_id"] == "paid-task"
    assert child["parent_attempt_id"] == item["id"] and provider.submit_calls == 2


@pytest.mark.asyncio
async def test_restart_with_provider_id_queries_without_submitting(setup):
    ctx, service, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))
    service.store.update(item["id"], stage="queued", provider_task_id="existing-paid-task")
    restarted = DirectorService(ctx, provider=provider, runtime=[], optimizer=fake_optimize)
    result = await restarted.resume(item["id"])
    assert result["provider_task_id"] == "existing-paid-task"
    assert provider.query_calls == 1 and provider.submit_calls == 0


@pytest.mark.asyncio
async def test_successful_poll_clears_transient_query_error(setup):
    ctx, service, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))
    service.store.update(item["id"], stage="queued", provider_task_id="paid-id")
    provider.fail_query = True
    assert (await service.resume(item["id"]))["error"] == "Provider status is temporarily unavailable"
    provider.fail_query = False
    assert (await service.resume(item["id"]))["error"] is None


@pytest.mark.asyncio
async def test_crash_at_submit_claim_never_submits_again(setup):
    ctx, service, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))
    service.store.update(item["id"], stage="submitting")
    restarted = DirectorService(ctx, provider=provider, runtime=[], optimizer=fake_optimize)
    result = await restarted.resume(item["id"])
    assert result["stage"] == "submission_unknown" and provider.submit_calls == 0


@pytest.mark.asyncio
async def test_configured_limit_is_snapshotted_for_runner_restart(setup):
    ctx, _, provider, _ = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    source = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p",
                           references=tuple(DirectorImage(image_id=f"r{i}", url="frame.png")
                                            for i in range(6)),
                           segments=(DirectorSegment(id="s", prompt="Walk", duration_seconds=5),))
    item, _ = DirectorService(ctx, provider=provider, runtime=[], reference_limit=8).create(
        "c", "n", "r", source)
    limits = []

    async def record(runtime, source, *, frozen_images, reference_limit):
        limits.append(reference_limit)
        raise RuntimeError("stop after limit check")

    await DirectorService(ctx, provider=provider, runtime=[], optimizer=record).resume(item["id"])
    assert limits == [8]


@pytest.mark.asyncio
async def test_changed_writing_rules_hash_invalidates_cached_optimization(setup):
    ctx, service, provider, images = setup
    (ctx.output_dir / "frame.png").write_bytes(png("red"))
    item, _ = service.create("c", "n", "r", draft("frame.png"))
    provider.fail_submit = RunningHubError("rejected", code="BAD_INPUT", http_status=200)
    await service.resume(item["id"])
    service.store.update(item["id"], rules_hash="old-writing-rules")
    child, _ = service.retry(item["id"])
    provider.fail_submit = None
    await service.resume(child["id"])
    assert len(images) == 2 and provider.submit_calls == 2
