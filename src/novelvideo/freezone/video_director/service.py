"""Crash-aware, paid-task-safe canvas video Director orchestration."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import traceback
from io import BytesIO
from itertools import islice
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from novelvideo.freezone.paths import resolve_static_url_to_path
from novelvideo.knowledge_runtime.codex import StructuredImage, validate_structured_images
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
from novelvideo.media_capabilities.video.h3_prompt_profile import H3_CANVAS_WRITING_RULES
from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubError

from .capabilities import validate_generation
from .models import DirectorDraft, DirectorImage, OptimizedDirector
from .optimizer import optimize
from .store import DirectorAttemptStore


_MIMES = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"),
          "WEBP": ("image/webp", ".webp")}


def _images(draft: DirectorDraft):
    return (*draft.references, *(image for segment in draft.segments
                                  for image in (segment.first_frame, segment.last_frame)
                                  if image is not None))


def _image_path(ctx, url: str) -> Path:
    split = urlsplit(url)
    if split.scheme or split.netloc or not url or "\\" in url or url.startswith("//"):
        raise ValueError("image URL must be a project-local path")
    path = unquote(split.path)
    if path.startswith("/static/projects/"):
        prefix = f"/static/projects/{ctx.project_id}/"
        if not path.startswith(prefix):
            raise ValueError("image URL belongs to another project")
    elif path.startswith("/static/"):
        prefix = f"/static/{ctx.owner_username}/{ctx.project_name}/"
        if not path.startswith(prefix):
            raise ValueError("image URL belongs to another project")
    elif path.startswith("/api/v1/projects/"):
        prefix = f"/api/v1/projects/{ctx.project_id}/media/"
        if not path.startswith(prefix):
            raise ValueError("image URL belongs to another project")
    elif path.startswith("/") and not path.startswith("/freezone/"):
        raise ValueError("absolute image path is not allowed")
    result = resolve_static_url_to_path(url, Path(ctx.output_dir))
    if not result.is_file():
        raise ValueError("image does not exist")
    return result


def _read_image(ctx, image: DirectorImage) -> tuple[StructuredImage, str]:
    path = _image_path(ctx, image.url)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("image is larger than 20 MiB")
    data = path.read_bytes()
    try:
        with Image.open(BytesIO(data)) as opened:
            media_type, suffix = _MIMES[opened.format]
            opened.verify()
    except (UnidentifiedImageError, KeyError, OSError) as exc:
        raise ValueError("image cannot be decoded as PNG, JPEG, or WebP") from exc
    return StructuredImage(data, media_type), suffix


def _current_rules_hash() -> str:
    return hashlib.sha256(H3_CANVAS_WRITING_RULES.encode()).hexdigest()


def _known_rejection(exc: Exception) -> bool:
    if not isinstance(exc, RunningHubError) or exc.retriable:
        return False
    if exc.code in {"INVALID_RESPONSE", "INVALID_JSON"}:
        return False
    return (exc.code is not None or exc.http_status in {400, 401, 403, 404, 422})


def _diagnostic_schema_names() -> frozenset[str]:
    names = set()
    for model in (DirectorDraft, OptimizedDirector):
        schema = model.model_json_schema()
        definitions = schema.get("$defs", {})
        names.update(definitions)
        for definition in (schema, *definitions.values()):
            names.update(definition.get("properties", {}))
    return frozenset(names)


_DIAGNOSTIC_SCHEMA_NAMES = _diagnostic_schema_names()


def _validation_locations(exc: BaseException) -> list[dict]:
    errors = []
    # Only follow explicit causes, with a bound that also handles cycles.
    for _ in range(8):
        if isinstance(exc, ValidationError):
            # Unknown field names and dictionary keys can themselves be private input.
            errors.extend({"loc": [part if isinstance(part, int) or part in _DIAGNOSTIC_SCHEMA_NAMES
                                   else "<redacted>" for part in error["loc"]],
                           "type": error["type"]}
                          for error in exc.errors(include_input=False, include_context=False,
                                                  include_url=False)[:32])
        if exc.__cause__ is None:
            break
        exc = exc.__cause__
    return errors


class DirectorService:
    def __init__(self, ctx, *, store=None, provider=None, runtime=None,
                 optimizer=optimize, reference_limit: int = 5, probe=None):
        self.ctx = ctx
        self.store = store or DirectorAttemptStore(Path(ctx.runtime_dir) / "freezone" / "video_director")
        self.provider = provider
        self.runtime = runtime
        self.optimizer = optimizer
        self.reference_limit = min(reference_limit, 8)
        self.probe = probe

    def _provider(self):
        if self.provider is None:
            from .provider import RunningHubDirectorProvider
            self.provider = RunningHubDirectorProvider(self.ctx)
        return self.provider

    def _freeze(self, draft: DirectorDraft):
        seen: dict[str, DirectorImage] = {}
        frozen: dict[str, str] = {}
        references = []
        for image in _images(draft):
            former = seen.get(image.image_id)
            if former is not None:
                if former.model_dump(exclude={"sha256"}) != image.model_dump(exclude={"sha256"}):
                    raise ValueError(f"conflicting image identity: {image.image_id}")
                continue
            seen[image.image_id] = image
            attachment, suffix = _read_image(self.ctx, image)
            if image in draft.references:
                references.append(attachment)
            digest = hashlib.sha256(attachment.data).hexdigest()
            path = self.store.root / "frozen" / f"{digest}{suffix}"
            path.parent.mkdir(exist_ok=True, mode=0o700)
            temporary = path.parent / f".{uuid4().hex}.tmp"
            with temporary.open("xb") as stream:
                stream.write(attachment.data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
            frozen[image.image_id] = path.name
        validate_structured_images(references)

        def replace(image):
            return image.model_copy(update={"sha256": frozen[image.image_id].split(".", 1)[0]}) if image else None
        snapshot = draft.model_copy(update={
            "references": tuple(replace(image) for image in draft.references),
            "segments": tuple(segment.model_copy(update={
                "first_frame": replace(segment.first_frame),
                "last_frame": replace(segment.last_frame),
            }) for segment in draft.segments),
        })
        return snapshot, frozen

    def create(self, canvas_id: str, node_id: str, request_id: str, draft: DirectorDraft):
        if not all(isinstance(x, str) and x.strip() and len(x) <= 128
                   for x in (canvas_id, node_id, request_id)):
            raise ValueError("canvas_id, node_id and request_id are required")
        existing = next((item for item in self.store.list(self.ctx.project_id, canvas_id, node_id)
                         if item["request_id"] == request_id), None)
        if existing:
            return existing, False
        validate_generation(draft, reference_limit=self.reference_limit)
        snapshot, frozen = self._freeze(draft)
        attempt, created = self.store.create(self.ctx.project_id, canvas_id, node_id,
                                             request_id, snapshot, detail={
                                                 "frozen_images": frozen,
                                                 "reference_limit": self.reference_limit,
                                             })
        return attempt, created

    def get(self, attempt_id: str):
        item = self.store.get(attempt_id)
        if item is None or item["project_id"] != self.ctx.project_id:
            raise KeyError(attempt_id)
        return item

    def list(self, canvas_id=None, node_id=None):
        return self.store.list(self.ctx.project_id, canvas_id, node_id)

    def retry(self, attempt_id: str):
        item = self.get(attempt_id)
        if item["stage"] == "submission_unknown":
            raise ValueError("submission status is unknown; automatic retry could create a duplicate paid task")
        if item["stage"] in {"queued", "generating", "completed"} and item.get("remote_url"):
            return item, False
        if item["stage"] == "failed" and item.get("failed_stage") == "downloading" and item.get("provider_task_id"):
            return self.store.update(attempt_id, stage="queued", task_id=None,
                                     error=None, failed_stage=None), True
        if item["stage"] != "failed":
            raise ValueError("only failed attempts can be retried")
        return self.store.retry(attempt_id)

    async def resume(self, attempt_id: str):
        self.get(attempt_id)
        lock_path = self.store.root / f"{attempt_id}.lock"
        with lock_path.open("a+b") as lock:
            await asyncio.to_thread(fcntl.flock, lock.fileno(), fcntl.LOCK_EX)
            try:
                return await self._resume_locked(attempt_id)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    async def _resume_locked(self, attempt_id: str):
        item = self.get(attempt_id)
        if item["stage"] in {"completed", "failed", "submission_unknown"}:
            return item
        if item["stage"] == "submitting" and not item.get("provider_task_id"):
            return self.store.update(attempt_id, stage="submission_unknown",
                                     failed_stage="submitting", error="submit response was not recorded")
        draft = DirectorDraft.model_validate(item["snapshot"])
        reference_limit = int(item.get("reference_limit") or self.reference_limit)
        if not item.get("provider_task_id"):
            current_hash = _current_rules_hash()
            cached = item.get("optimized")
            from novelvideo.media_capabilities.video.h3_prompt_profile import (
                H3_PROMPT_PROFILE_ID, H3_PROMPT_PROFILE_VERSION,
            )
            if (cached and item.get("rules_hash") == current_hash and
                cached.get("profile_id") == H3_PROMPT_PROFILE_ID and
                cached.get("profile_version") == H3_PROMPT_PROFILE_VERSION):
                optimized = OptimizedDirector.model_validate(cached)
            else:
                item = self.store.update(attempt_id, stage="optimizing")
                try:
                    paths = item.get("frozen_images") or {}
                    images = {}
                    for image_id, filename in paths.items():
                        path = self.store.root / "frozen" / filename
                        media_type = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[path.suffix]
                        images[image_id] = StructuredImage(path.read_bytes(), media_type)
                    runtime = self.runtime
                    if runtime is None:
                        from novelvideo.text_task_runtime.runtime import current_text_task_runtime
                        runtime = current_text_task_runtime()
                    if runtime is None:
                        raise RuntimeError("director text runtime is unavailable")
                    optimized = await self.optimizer(runtime, draft, frozen_images=images,
                                                     reference_limit=reference_limit)
                except Exception as exc:
                    error = "Director optimization failed"
                    if isinstance(exc, KnowledgeRuntimeError) and exc.code == "DSH_IMAGES_UNSUPPORTED":
                        error = ("当前导演提示词运行时 DeepSeek Harness 不支持图片输入；"
                                 "请将导演规划运行时切换为 Codex 或支持图片的模型 API 后重试。")
                    return self.store.update(attempt_id, stage="failed", failed_stage="optimizing",
                                             error=error,
                                             optimization_validation_errors=_validation_locations(exc),
                                             optimization_error_type=type(exc).__name__,
                                             optimization_error_code=(exc.code if isinstance(exc, KnowledgeRuntimeError)
                                                 and exc.code in {"DSH_IMAGES_UNSUPPORTED", "CODEX_STRUCTURED_OUTPUT_INVALID",
                                                                  "CODEX_NOT_INSTALLED", "CODEX_EXEC_FAILED",
                                                                  "CODEX_NOT_AUTHENTICATED", "CODEX_SCHEMA_INVALID"}
                                                 else None),
                                             # Store locations only: exception messages, source lines and
                                             # frame locals may contain credentials or private prompts.
                                             optimization_error_trace=[
                                                 {"filename": Path(frame.f_code.co_filename).name,
                                                  "function": frame.f_code.co_name, "line": line}
                                                 for frame, line in islice(traceback.walk_tb(exc.__traceback__), 32)
                                             ])
                item = self.store.update(attempt_id, optimized=optimized.model_dump(mode="json"),
                                         rules_hash=current_hash, stage="preparing")
            # Upload and compilation happen before the paid submission claim.
            try:
                paths = {key: self.store.root / "frozen" / value
                         for key, value in item["frozen_images"].items()}
                prepared = await self._provider().prepare(draft, optimized, paths, reference_limit)
            except Exception:
                return self.store.update(attempt_id, stage="failed", failed_stage="uploading",
                                         error="Director upload or workflow preparation failed")
            validation = validate_generation(draft, reference_limit=reference_limit)
            item = self.store.update(attempt_id, stage="submitting", workflow_id=prepared["workflow_id"],
                                     workflow_profile_id=prepared["profile_id"],
                                     workflow_profile_version=prepared.get("profile_version"),
                                     actual_parameters={"route": optimized.route, "resolution": draft.resolution,
                                                        "aspect_ratio": draft.aspect_ratio,
                                                        "segments": len(draft.segments),
                                                        "timeline": [{"segment_id": segment.id,
                                                                      "requested_duration_seconds": segment.requested_duration_seconds,
                                                                      "duration_seconds": segment.duration_seconds,
                                                                      "frames": segment.frames,
                                                                      "start_frame": segment.start_frame}
                                                                     for segment in validation.timeline],
                                                        "fps": 24,
                                                        "frames": validation.total_frames,
                                                        "duration_seconds": validation.actual_duration_seconds,
                                                        "width": validation.size.width,
                                                        "height": validation.size.height,
                                                        "reference_limit": reference_limit})
            try:
                task_id = await self._provider().submit(prepared)
                if not isinstance(task_id, str) or not task_id.strip():
                    raise ValueError("Provider submission returned no task ID")
            except Exception as exc:
                if _known_rejection(exc):
                    return self.store.update(attempt_id, stage="failed", failed_stage="submitting",
                                             error="Provider rejected the submission")
                return self.store.update(attempt_id, stage="submission_unknown", failed_stage="submitting",
                                         error="Provider submission outcome is unknown")
            item = self.store.update(attempt_id, stage="queued", provider_task_id=task_id)
        if item.get("remote_url"):
            provider = self._provider()
            if hasattr(provider, "set_route"):
                provider.set_route((item.get("optimized") or {}).get("route", "h3"), draft)
            return await self._download(item)
        provider = self._provider()
        if hasattr(provider, "set_route"):
            provider.set_route((item.get("optimized") or {}).get("route", "h3"), draft)
        try:
            snapshot = await provider.query(item["provider_task_id"])
        except Exception:
            return self.store.update(attempt_id, stage="queued", error="Provider status is temporarily unavailable")
        if snapshot.status in {"failed", "cancelled"}:
            return self.store.update(attempt_id, stage="failed", failed_stage="generating",
                                     error="Provider generation failed")
        if snapshot.status == "succeeded":
            video = next((result for result in snapshot.results if result.node_id == "7"), None)
            if video is None:
                return self.store.update(attempt_id, stage="failed", failed_stage="generating",
                                         error="Provider video output is missing")
            item = self.store.update(attempt_id, stage="generating", remote_url=video.url)
            return await self._download(item)
        return self.store.update(attempt_id,
                                 stage="generating" if snapshot.status == "running" else "queued",
                                 error=None)

    async def _download(self, item):
        attempt_id = item["id"]
        try:
            data = await self._provider().download(item["remote_url"])
            if not data:
                raise ValueError("empty provider output")
            output = Path(self.ctx.output_dir) / "freezone" / "_outputs" / "video_director" / f"{attempt_id}.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(".tmp")
            temporary.write_bytes(data)
            if self.probe is None:
                from novelvideo.media_capabilities.video.runtime import _probe_video
                probe = _probe_video
            else:
                probe = self.probe
            result = await probe(temporary)
            if result.duration <= 0 or result.width <= 0 or result.height <= 0:
                raise ValueError("provider video is unreadable")
            os.replace(temporary, output)
            from novelvideo.api.deps import make_static_url_for_context
            local_url = make_static_url_for_context(self.ctx, output.relative_to(self.ctx.output_dir).as_posix())
            return self.store.update(attempt_id, stage="completed", result_url=local_url,
                                     failed_stage=None, error=None)
        except Exception:
            return self.store.update(attempt_id, stage="failed", failed_stage="downloading",
                                     error="Provider video could not be saved or read")
