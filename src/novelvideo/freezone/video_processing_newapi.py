"""NewAPI processing transport, matching DramaClaw PRs #662 and #716.

Only explicitly configured processing models use this adapter. Credentials,
media relay, HTTP headers, downloads and credit accounting reuse NewAPI runtime.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from functools import partial
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:/-]+$")
    enabled: bool = False
    resolutions: list[Literal["1080p", "2k", "4k"]] = []
    frame_rates: list[int] = Field(default_factory=list, max_length=10)
    output_kind: Literal["full", "continuation"] = "full"
    duration_mode: Literal["additional", "total"] = "additional"
    max_input_seconds: float = Field(default=120, gt=0, le=3600, allow_inf_nan=False)


class ProcessingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: dict[Literal["extend", "upscale", "interpolate", "retake"], ModelConfig] = Field(default_factory=dict)


def config_path(project_dir: Path) -> Path:
    return project_dir / "freezone" / "video-processing.json"


def read_config(project_dir: Path) -> ProcessingConfig:
    path = config_path(project_dir)
    return ProcessingConfig.model_validate_json(path.read_text()) if path.exists() else ProcessingConfig()


def configured_adapters(project_dir: Path, snapshot: dict | None = None) -> dict:
    from novelvideo.config import get_newapi_runtime_credentials
    from novelvideo.freezone.video_processing import ProcessingAdapter
    key, endpoint = get_newapi_runtime_credentials()
    if not key or not endpoint:
        return {}
    adapters = {}
    saved = ProcessingConfig.model_validate(snapshot) if snapshot is not None else read_config(project_dir)
    for mode, config in saved.models.items():
        if not config.enabled:
            continue
        adapters[mode] = ProcessingAdapter(
            execute=partial(execute_newapi, config=config.model_dump()), provider="newapi", model=config.model,
            resolutions=tuple(config.resolutions), frame_rates=tuple(config.frame_rates),
            output_kind=config.output_kind, max_input_seconds=config.max_input_seconds)
    return adapters


def build_request(model: str, source_url: str, request: dict, config: dict) -> dict:
    mode = request["operation"]
    duration = float(request["source_duration"])
    metadata = {"reference_videos": [source_url], "ratio": "auto"}
    if mode == "extend":
        metadata["omni_reference_task_type"] = "extend"
        duration = float(request["additional_seconds"]) + (duration if config.get("duration_mode") == "total" else 0)
    elif mode == "retake":
        metadata["omni_reference_task_type"] = "edit"
        duration = float(request["end"]) - float(request["start"])
    elif mode == "upscale":
        metadata.update(resolution=request["resolution"], resolution_tier={"1080p": "fhd"}.get(request["resolution"], request["resolution"]), scene="realistic", face_enhance=False)
    elif mode == "interpolate":
        metadata.update(target_fps=request["fps"], smart_interpolation=True)
        if request.get("resolution"):
            metadata["resolution_tier"] = {"1080p": "fhd"}.get(request["resolution"], request["resolution"])
    return {"model": model, "prompt": request.get("prompt", ""), "duration": duration, "metadata": metadata}


def _save(path: Path, data: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(data))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


async def execute_newapi(*, source: Path, output: Path, operation_id: str, request: dict, config: dict) -> Path:
    from novelvideo.generators.video_generator import (
        NewApiVideoGenerator, NewApiVideoError, _reserve_video_model_call, _confirm_video_model_call, _refund_video_model_call,
    )
    from novelvideo.freezone.jobs import _run_cmd
    from novelvideo.task_backend.cancel import TaskCancelled, TaskTimedOut
    from novelvideo.utils.error_redaction import redact_secrets
    generator = NewApiVideoGenerator(model=config["model"], resolution=request.get("resolution") or "720p")
    if not generator.api_key or not generator.base_url:
        raise ValueError("请先在模型设置配置 NewAPI 网关和凭据")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = output.with_suffix(".provider.json")
    state = json.loads(receipt.read_text()) if receipt.exists() else {}
    if state.get("failed"):
        # Refund operations are idempotent by reservation ID. Retry even after
        # a crash (or a swallowed billing transport error) in the previous run.
        await _refund_video_model_call(state.get("reservation_id", ""), source="video_processing",
                                      error=state["failed"], provider_task_id=state.get("task_id", ""))
        raise RuntimeError(state["failed"])
    if state.get("submitting") and not state.get("task_id"):
        raise RuntimeError(f"供应商提交状态未知，禁止自动重复提交；请按操作标识 {operation_id} 核查")
    if not state.get("task_id"):
        input_video = source
        if request["operation"] == "retake":
            input_video = output.with_suffix(".selection.mp4")
            await _run_cmd(["ffmpeg", "-y", "-ss", str(request["start"]), "-i", str(source),
                            "-t", str(request["end"] - request["start"]), "-c:v", "libx264", "-c:a", "aac", str(input_video)])
        relay = await generator._relay_media_input(str(input_video), default_ext="mp4", allowed_roots=[source.parent, output.parent])
        body = build_request(config["model"], relay, request, config)
        body["idempotency_key"] = operation_id
        if not state.get("reservation_id"):
            state["reservation_id"] = await _reserve_video_model_call(config["model"], source="video_processing", resolution=request.get("resolution"), duration_seconds=body["duration"])
            _save(receipt, state)
        # A gateway is not assumed to honor idempotency. An ambiguous acceptance
        # blocks re-submission even though we also send its idempotency header.
        state["submitting"] = True
        _save(receipt, state)
        try:
            submitted = await generator._post_json(f"{generator.base_url}/video/generations", body)
        except NewApiVideoError as exc:
            # Like the existing generation transport, explicit 4xx rejection
            # (except timeout) is refundable. 5xx/408/network loss remain unknown.
            if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code != 408:
                message = redact_secrets(str(exc))
                state.update(failed=message, submitting=False)
                _save(receipt, state)
                await _refund_video_model_call(state.get("reservation_id", ""), source="video_processing",
                                              error=message, provider_request_id=exc.request_id)
            raise
        state["task_id"] = str(submitted.get("id") or submitted.get("task_id") or "")
        state["submitting"] = not bool(state["task_id"])
        _save(receipt, state)
        if not state["task_id"]:
            raise RuntimeError("供应商未返回任务 ID；请按幂等标识核查任务")
    task_id = state["task_id"]
    try:
        for _ in range(360):
            task = await generator._get_json(f"{generator.base_url}/videos/{task_id}")
            status = str(task.get("status", "")).lower()
            if status in {"completed", "succeeded", "success", "done"}:
                url = generator._extract_video_url(task)
                if not url:
                    raise RuntimeError("供应商完成任务但未提供视频")
                await generator._download_video(url, str(output))
                if not output.is_file() or not output.stat().st_size:
                    raise RuntimeError("供应商结果下载失败")
                if not state.get("confirmed"):
                    await _confirm_video_model_call(model=config["model"], reservation_id=state.get("reservation_id", ""), provider_task_id=task_id)
                    state["confirmed"] = True
                    _save(receipt, state)
                return output
            if status in {"failed", "error", "cancelled", "canceled"}:
                message = redact_secrets(str(task.get("error") or "视频处理失败"))
                state["failed"] = message
                _save(receipt, state)
                await _refund_video_model_call(state.get("reservation_id", ""), source="video_processing", error=message, provider_task_id=task_id)
                raise RuntimeError(message)
            await asyncio.sleep(5)
        raise TimeoutError("视频处理仍未完成；重试会继续查询已提交任务")
    except (asyncio.CancelledError, TaskCancelled, TaskTimedOut):
        if await generator._cancel_provider_task(task_id):
            state["failed"] = "供应商任务已取消"
            _save(receipt, state)
            await _refund_video_model_call(state.get("reservation_id", ""), source="video_processing", error="cancelled", provider_task_id=task_id)
        raise
