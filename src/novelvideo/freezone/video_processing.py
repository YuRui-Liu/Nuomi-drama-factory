"""Capability-gated video operations. Generation adapters must opt in explicitly.

Ordinary video generation models are deliberately not treated as editing models.
Stage operation IDs are stable across task retries and distinct between models.
"""
from __future__ import annotations

import hashlib
import asyncio
import json
import math
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from fractions import Fraction
from typing import Awaitable, Callable

from novelvideo.freezone.paths import outputs_dir


@dataclass(frozen=True)
class ProcessingAdapter:
    """Register only an implemented provider operation with verified semantics.

    execute must reuse the provider's existing billing/concurrency transport and
    accept operation_id as its idempotency key; no generic HTTP URL is accepted.
    """
    execute: Callable[..., Awaitable[Path]]
    provider: str
    model: str
    resolutions: tuple[str, ...] = ()
    frame_rates: tuple[int, ...] = ()
    output_kind: str = "full"
    max_input_seconds: float = 0


ADAPTERS: dict[str, ProcessingAdapter] = {}
MODEL_OPERATIONS = ("extend", "upscale", "interpolate", "retake")


def _adapters(project_dir: Path | None = None, snapshot: dict | None = None) -> dict:
    if project_dir is None:
        return ADAPTERS
    from novelvideo.freezone.video_processing_newapi import configured_adapters
    return {**configured_adapters(project_dir, snapshot), **ADAPTERS}


def capabilities(project_dir: Path | None = None) -> dict:
    result = {}
    adapters = _adapters(project_dir)
    for mode in MODEL_OPERATIONS:
        adapter = adapters.get(mode)
        result[mode] = {
            "available": adapter is not None,
            "reason": "" if adapter else "未配置并验证此操作的供应商适配器；普通视频生成模型不代表支持该能力",
            "provider": adapter.provider if adapter else None,
            "model": adapter.model if adapter else None,
            "resolutions": list(adapter.resolutions) if adapter else [],
            "frame_rates": list(adapter.frame_rates) if adapter else [],
            "output_kind": adapter.output_kind if adapter else None,
            "max_input_seconds": adapter.max_input_seconds if adapter else None,
        }
    result["adopt"] = {"available": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
                       "reason": "需要 FFmpeg 和 ffprobe", "audio_policy": "preserve_source"}
    return result


def require_capability(mode: str, project_dir: Path | None = None, snapshot: dict | None = None) -> ProcessingAdapter:
    adapters = _adapters(project_dir, snapshot)
    if mode not in MODEL_OPERATIONS or mode not in adapters:
        raise ValueError(f"{mode}: 未配置并验证此操作的供应商适配器")
    return adapters[mode]


def validate_range(start: float, end: float, duration: float) -> float:
    if not all(math.isfinite(n) for n in (start, end, duration)) or not 0 <= start < end <= duration:
        raise ValueError("重拍范围必须满足 0 ≤ start < end ≤ 原视频时长（左闭右开）")
    return end - start


def stage_id(job_id: str, stage: str) -> str:
    return hashlib.sha256(f"video-processing:{job_id}:{stage}".encode()).hexdigest()[:32]


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save_stage_receipt(path: Path, data: dict) -> None:
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(data))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def audio_result_path(project_dir: Path, job_id: str) -> Path:
    root = outputs_dir(project_dir, "freezone_audio_separate")
    mp3 = root / f"{job_id}.mp3"
    old = root / f"{job_id}.m4a"
    return mp3 if mp3.exists() or not old.exists() else old


async def probe_stream(path: Path) -> dict:
    proc = await asyncio.to_thread(subprocess.run, ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,avg_frame_rate", "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise ValueError("无法读取视频流参数")
    stream = json.loads(proc.stdout)["streams"][0]
    stream["fps"] = float(Fraction(stream["avg_frame_rate"]))
    return stream


async def join_continuation(source: Path, continuation: Path, output: Path, duration: float, extra: float) -> None:
    from novelvideo.freezone.jobs import _render_video_clip, _concat_media_segments
    info = await probe_stream(source)
    parts = [output.with_suffix(".source.mp4"), output.with_suffix(".tail.mp4")]
    for path, target, seconds in zip([source, continuation], parts, [duration, extra]):
        await _render_video_clip(source_path=str(path), output_path=target, source_start=0, duration=seconds,
                                 width=info["width"], height=info["height"], fps=round(info["fps"]),
                                 background_color="#000000", keep_original_audio=True, volume=1, muted=False)
    await _concat_media_segments(parts, output)
    for path in parts:
        path.unlink(missing_ok=True)


async def adopt_segment(source: Path, candidate: Path, output: Path, start: float, end: float) -> None:
    """Re-encode cuts with a single timebase; retain original audio in full."""
    from novelvideo.freezone.jobs import _probe_video_duration, _probe_video_size, _run_cmd
    duration = await _probe_video_duration(str(source))
    selected = validate_range(start, end, duration)
    replacement_duration = await _probe_video_duration(str(candidate))
    if abs(replacement_duration - selected) > 0.08:
        raise ValueError("候选片段时长与选区不符；不会自动拉伸")
    width, height = await _probe_video_size(str(source))
    # Normalize VFR and all cuts to one timebase before concat. Do not stretch time.
    normalize = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,settb=AVTB"
    filters, labels = [], []
    if start > 0:
        filters.append(f"[0:v]trim=end={start},setpts=PTS-STARTPTS,{normalize}[before]")
        labels.append("[before]")
    filters.append(f"[1:v]trim=duration={selected},setpts=PTS-STARTPTS,{normalize}[replacement]")
    labels.append("[replacement]")
    if end < duration:
        filters.append(f"[0:v]trim=start={end},setpts=PTS-STARTPTS,{normalize}[after]")
        labels.append("[after]")
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[outv]")
    await _run_cmd(["ffmpeg", "-y", "-i", str(source), "-i", str(candidate),
                    "-filter_complex", ";".join(filters), "-map", "[outv]", "-map", "0:a?",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-t", str(duration), "-movflags", "+faststart", str(output)])
    if abs(await _probe_video_duration(str(output)) - duration) > 0.12:
        output.unlink(missing_ok=True)
        raise ValueError("拼接后的时长校验失败")


async def run_processing(*, project_dir: Path, job_id: str, payload: dict) -> tuple[Path, dict]:
    from novelvideo.freezone.jobs import _probe_video_duration
    source = Path(payload["source_path"])
    if fingerprint(source) != payload["source_fingerprint"]:
        raise ValueError("原视频已变化，请重新选择来源")
    mode = payload["operation"]
    root = outputs_dir(project_dir, "freezone_video_upscale")
    root.mkdir(parents=True, exist_ok=True)
    output = root / f"{job_id}.mp4"
    if mode == "adopt":
        candidate = Path(payload["candidate_path"])
        if fingerprint(candidate) != payload["candidate_fingerprint"]:
            raise ValueError("候选片段已变化")
        await adopt_segment(source, candidate, output, payload["start"], payload["end"])
        return output, {"operation": mode, "source_fingerprint": payload["source_fingerprint"], "audio_policy": "preserve_source"}
    stages = ["upscale", "interpolate"] if mode == "enhance" else [mode]
    if mode == "enhance":
        stages = [s for s in stages if payload.get("resolution" if s == "upscale" else "fps")]
    if not stages:
        raise ValueError("请选择超分或插帧")
    duration = await _probe_video_duration(str(source))
    current = source
    completed = []
    for stage in stages:
        adapter = require_capability(stage, project_dir, payload.get("model_config_snapshot"))
        if adapter.max_input_seconds and duration > adapter.max_input_seconds:
            raise ValueError("输入视频超过模型允许的时长")
        if stage == "upscale" and payload.get("resolution") not in adapter.resolutions:
            raise ValueError("模型不支持请求的分辨率")
        if stage == "interpolate" and payload.get("fps") not in adapter.frame_rates:
            raise ValueError("模型不支持请求的帧率")
        operation_id = stage_id(job_id, stage)
        stage_output = root / f"{operation_id}.mp4"
        receipt = root / f"{operation_id}.json"
        signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        reusable = False
        if receipt.exists() and stage_output.exists():
            try:
                saved = json.loads(receipt.read_text())
                reusable = isinstance(saved, dict) and saved.get("signature") == signature and saved.get("sha256") == fingerprint(stage_output)
            except (ValueError, UnicodeError):
                # This is a local validation cache, not the provider submission
                # receipt. Recover through the same operation ID and remote task.
                reusable = False
        if not reusable:
            produced = await adapter.execute(source=current, output=stage_output, operation_id=operation_id,
                                             request={**payload, "operation": stage, "source_duration": duration})
            if Path(produced) != stage_output or not stage_output.exists():
                raise RuntimeError("供应商未返回约定的阶段结果")
            actual_duration = await _probe_video_duration(str(stage_output))
            expected = (payload["end"] - payload["start"]) if stage == "retake" else duration
            if stage == "extend":
                expected = payload["additional_seconds"] + (duration if adapter.output_kind == "full" else 0)
            if abs(actual_duration - expected) > 0.12:
                raise ValueError("供应商结果时长与请求不符")
            stream = await probe_stream(stage_output)
            source_stream = await probe_stream(current)
            if abs(stream["width"] / stream["height"] - source_stream["width"] / source_stream["height"]) > 0.02:
                raise ValueError("供应商改变了来源视频画幅")
            if stage == "upscale" and abs(max(stream["width"], stream["height"]) - {"1080p": 1920, "2k": 2560, "4k": 3840}[payload["resolution"]]) > 2:
                raise ValueError("超分结果实际分辨率与请求不符")
            if stage == "interpolate" and abs(stream["fps"] - payload["fps"]) > 0.1:
                raise ValueError("插帧结果实际帧率与请求不符")
            if stage == "extend" and adapter.output_kind == "continuation":
                joined = root / f"{operation_id}_joined.mp4"
                await join_continuation(source, stage_output, joined, duration, payload["additional_seconds"])
                if abs(await _probe_video_duration(str(joined)) - (duration + payload["additional_seconds"])) > 0.12:
                    raise ValueError("延长拼接后时长校验失败")
                joined.replace(stage_output)
            _save_stage_receipt(receipt, {"signature": signature, "sha256": fingerprint(stage_output)})
        current = stage_output
        completed.append({"operation": stage, "operation_id": operation_id, "reused": reusable})
    if mode == "enhance":
        from novelvideo.freezone.jobs import _probe_has_audio, _run_cmd
        if await _probe_has_audio(str(source)):
            await _run_cmd(["ffmpeg", "-y", "-i", str(current), "-i", str(source), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-t", str(duration), str(output)])
        else:
            shutil.copyfile(current, output)
    else:
        shutil.copyfile(current, output)
    return output, {"operation": mode, "stages": completed, "source_fingerprint": payload["source_fingerprint"],
                    "range": [payload.get("start"), payload.get("end")] if mode == "retake" else None}
