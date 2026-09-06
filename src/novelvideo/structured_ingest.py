"""Deterministic source planning for the ``structured_v1`` knowledge pipeline.

This module deliberately has no Cognee dependency.  It validates source text,
creates a resumable run manifest, and writes the public ``novel.txt`` success
marker only after the plan is durable.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

import portalocker

STRUCTURED_PIPELINE_VERSION = "structured_v1"
STRUCTURED_SCHEMA_VERSION = "1"

SectionType = Literal["scene", "chapter", "window"]
ProgressCallback = Callable[[float, str], None]
Transition = Callable[..., Any]

_WINDOW_CHARS = 6000
_WINDOW_OVERLAP = 200
_SCENE_HEADER = re.compile(r"(?m)^[ \t]*\d+[.-]\d+\s+[^\r\n]+")
_CHAPTER_HEADER = re.compile(r"(?m)^[ \t]*第[^\r\n]{1,24}章(?:\s+[^\r\n]*)?")


@dataclass(frozen=True, slots=True)
class SourceChunk:
    chunk_id: str
    chunk_index: int
    section_type: SectionType
    section_label: str
    source_start: int
    source_end: int
    text: str

    @property
    def source_hash(self) -> str:
        return source_sha256(self.text)


@dataclass(frozen=True, slots=True)
class StructuredRunIdentity:
    source_sha256: str
    schema_version: str
    pipeline_version: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StructuredRunPlan:
    run_id: str
    identity: StructuredRunIdentity
    spine_template: str
    chunks: tuple[SourceChunk, ...]


@dataclass(frozen=True, slots=True)
class StructuredProgressEvent:
    progress: float
    phase: str
    message: str


_PROGRESS = (
    StructuredProgressEvent(0.05, "read", "读取并校验原文..."),
    StructuredProgressEvent(0.35, "validated", "原文校验完成"),
    StructuredProgressEvent(0.65, "chunked", "确定性切分完成"),
    StructuredProgressEvent(0.85, "planned", "分析计划已记录"),
    StructuredProgressEvent(0.95, "save", "保存导入结果..."),
    StructuredProgressEvent(1.0, "complete", "导入完成"),
)


def source_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _section_chunks(
    text: str,
    matches: list[re.Match[str]],
    section_type: Literal["scene", "chapter"],
) -> list[SourceChunk]:
    starts = [match.start() for match in matches]
    if starts and starts[0] > 0:
        starts.insert(0, 0)
    chunks: list[SourceChunk] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        if end <= start:
            continue
        first_line = next(
            (line.strip() for line in text[start:end].splitlines() if line.strip()),
            f"片段 {index + 1}",
        )
        chunks.append(
            SourceChunk(
                chunk_id=f"{section_type}-{index:04d}",
                chunk_index=index,
                section_type=section_type,
                section_label=first_line,
                source_start=start,
                source_end=end,
                text=text[start:end],
            )
        )
    return chunks


def _window_chunks(text: str) -> list[SourceChunk]:
    chunks: list[SourceChunk] = []
    start = 0
    while start < len(text):
        end = min(start + _WINDOW_CHARS, len(text))
        index = len(chunks)
        chunks.append(
            SourceChunk(
                chunk_id=f"window-{index:04d}",
                chunk_index=index,
                section_type="window",
                section_label=f"片段 {index + 1}",
                source_start=start,
                source_end=end,
                text=text[start:end],
            )
        )
        if end >= len(text):
            break
        start = end - _WINDOW_OVERLAP
    return chunks


def chunk_source_text(text: str, spine_template: str | None) -> list[SourceChunk]:
    """Split on stable source markers and fall back to bounded windows."""
    if not (text or "").strip():
        return []
    template = str(spine_template or "").strip()
    if template == "drama":
        matches = list(_SCENE_HEADER.finditer(text))
        if matches:
            return _section_chunks(text, matches, "scene")
    else:
        matches = list(_CHAPTER_HEADER.finditer(text))
        if matches:
            return _section_chunks(text, matches, "chapter")
    return _window_chunks(text)


def build_structured_run_plan(
    text: str,
    spine_template: str | None,
    *,
    schema_version: str = STRUCTURED_SCHEMA_VERSION,
    pipeline_version: str = STRUCTURED_PIPELINE_VERSION,
) -> StructuredRunPlan:
    template = str(spine_template or "").strip()
    identity = StructuredRunIdentity(
        source_sha256=source_sha256(text),
        schema_version=str(schema_version),
        pipeline_version=str(pipeline_version),
    )
    run_key = json.dumps(
        {**identity.as_dict(), "spine_template": template},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    run_id = hashlib.sha256(run_key.encode("utf-8")).hexdigest()
    return StructuredRunPlan(
        run_id=run_id,
        identity=identity,
        spine_template=template,
        chunks=tuple(chunk_source_text(text, template)),
    )


def _manifest_payload(plan: StructuredRunPlan) -> dict[str, Any]:
    return {
        "run_id": plan.run_id,
        "identity": plan.identity.as_dict(),
        "spine_template": plan.spine_template,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "chunk_index": chunk.chunk_index,
                "section_type": chunk.section_type,
                "section_label": chunk.section_label,
                "source_start": chunk.source_start,
                "source_end": chunk.source_end,
                "source_hash": chunk.source_hash,
                "status": "pending",
            }
            for chunk in plan.chunks
        ],
    }


def _persist_manifest(state_dir: Path, plan: StructuredRunPlan) -> tuple[Path, bool]:
    runs_dir = state_dir / "structured_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{plan.run_id}.json"
    if path.is_file():
        return path, True
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(_manifest_payload(plan), stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path, False


def _underlying_store(store: Any) -> Any:
    return getattr(store, "sqlite_store", store)


def _report(callback: ProgressCallback | None, event: StructuredProgressEvent) -> None:
    if callback is not None:
        callback(event.progress, event.message)


def _default_transition(state_dir: Path, status: str, **kwargs: Any) -> Any:
    from novelvideo.knowledge_pipeline import transition_structured_pipeline

    return transition_structured_pipeline(state_dir, status, **kwargs)


async def _call_transition(
    transition: Transition,
    state_dir: Path,
    status: str,
    **kwargs: Any,
) -> Any:
    result = transition(state_dir, status, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _restore_novel_marker_if_unchanged(
    path: Path,
    *,
    previous: bytes | None,
    written: bytes,
) -> bool:
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        return False
    if current != written:
        return False
    if previous is None:
        path.unlink(missing_ok=True)
        return True
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.rollback")
    try:
        with temporary.open("wb") as stream:
            stream.write(previous)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


@asynccontextmanager
async def _project_mutation_lock(state_dir: Path):
    """Share the episode-import mutation lock for publication side effects."""
    lock_path = state_dir / "episode_import.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(str(lock_path), mode="a", timeout=None)
    await asyncio.to_thread(lock.acquire)
    try:
        yield
    finally:
        await asyncio.to_thread(lock.release)


def _fail_ready_transition_for_attempt(
    state_dir: Path,
    *,
    attempt_id: str,
    error: str,
) -> Literal["failed", "different_attempt", "not_ready"]:
    """Fail a persisted ready state only when this exact attempt still owns it."""
    from novelvideo.knowledge_pipeline import (
        KNOWLEDGE_PIPELINE_ATTEMPT_ID_KEY,
        KNOWLEDGE_PIPELINE_ERROR_KEY,
        KNOWLEDGE_PIPELINE_KEY,
        KNOWLEDGE_PIPELINE_STATUS_KEY,
        KNOWLEDGE_PIPELINE_STRUCTURED,
        STATUS_STRUCTURED_FAILED,
        STATUS_STRUCTURED_READY,
        STRUCTURED_STATUSES,
    )
    from novelvideo.project_config import update_project_config_file_in_state_dir

    outcome: Literal["failed", "different_attempt", "not_ready"] = "not_ready"

    def apply(config: dict[str, Any]) -> None:
        nonlocal outcome
        status = config.get(KNOWLEDGE_PIPELINE_STATUS_KEY)
        if (
            config.get(KNOWLEDGE_PIPELINE_KEY) != KNOWLEDGE_PIPELINE_STRUCTURED
            or status not in STRUCTURED_STATUSES
        ):
            return
        current_attempt = str(
            config.get(KNOWLEDGE_PIPELINE_ATTEMPT_ID_KEY) or ""
        ).strip()
        if current_attempt and current_attempt != attempt_id:
            outcome = "different_attempt"
            return
        if current_attempt != attempt_id:
            return
        if status != STATUS_STRUCTURED_READY:
            return
        config[KNOWLEDGE_PIPELINE_STATUS_KEY] = STATUS_STRUCTURED_FAILED
        if str(error or "").strip():
            config[KNOWLEDGE_PIPELINE_ERROR_KEY] = str(error).strip()
        else:
            config.pop(KNOWLEDGE_PIPELINE_ERROR_KEY, None)
        outcome = "failed"

    update_project_config_file_in_state_dir(state_dir, apply, strict=True)
    return outcome


async def ingest_source_text_structured(
    store: Any,
    novel_path: str,
    *,
    spine_template: str | None = None,
    on_progress: ProgressCallback | None = None,
    on_log: Callable[[str], None] | None = None,
    schema_version: str = STRUCTURED_SCHEMA_VERSION,
    pipeline_version: str = STRUCTURED_PIPELINE_VERSION,
    transition: Transition | None = None,
) -> dict[str, Any]:
    """Persist a reusable structured run without graph or embedding calls."""
    source_path = Path(novel_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"文件不存在: {novel_path}")
    _report(on_progress, _PROGRESS[0])
    if on_log:
        on_log(f"读取文件: {source_path.name}")
    from novelvideo.utils.document_parsers import load_novel_text

    content = load_novel_text(str(source_path))
    plan = build_structured_run_plan(
        content,
        spine_template,
        schema_version=schema_version,
        pipeline_version=pipeline_version,
    )
    base_store = _underlying_store(store)
    state_dir = Path(base_store.state_dir)
    transition_fn = transition or _default_transition
    attempt_id = uuid4().hex
    await _call_transition(
        transition_fn,
        state_dir,
        "structured_running",
        run_identity=plan.identity.as_dict(),
        attempt_id=attempt_id,
    )
    failure_state_handled = False
    different_attempt_active = False
    try:
        if not content.strip():
            raise ValueError("小说内容为空，无法导入")
        _report(on_progress, _PROGRESS[1])
        if not plan.chunks:
            raise ValueError("原文切分结果为空，无法分析")
        _report(on_progress, _PROGRESS[2])
        _, reused = _persist_manifest(state_dir, plan)
        _report(on_progress, _PROGRESS[3])
        _report(on_progress, _PROGRESS[4])
        from novelvideo.structured_builders import publish_structured_publication
        from novelvideo.structured_publication import (
            build_structured_publication_from_source,
        )

        publication = await build_structured_publication_from_source(
            content,
            spine_template,
            on_log=on_log,
        )
        novel_marker = Path(base_store.project_dir) / "novel.txt"
        written_marker = content.encode("utf-8")
        async with _project_mutation_lock(state_dir):
            previous_marker = (
                novel_marker.read_bytes() if novel_marker.is_file() else None
            )
            marker_written = False

            async def mark_ready() -> None:
                await _call_transition(
                    transition_fn,
                    state_dir,
                    "structured_ready",
                    expected_status="structured_running",
                    run_identity=plan.identity.as_dict(),
                    attempt_id=attempt_id,
                )

            try:
                save = base_store.save_novel_content(content)
                if inspect.isawaitable(save):
                    await save
                marker_written = True
                published = await publish_structured_publication(
                    base_store,
                    publication,
                    run_id=plan.run_id,
                    before_commit=mark_ready,
                )
            except BaseException as publish_error:
                if marker_written:
                    _restore_novel_marker_if_unchanged(
                        novel_marker,
                        previous=previous_marker,
                        written=written_marker,
                    )
                try:
                    compensation = _fail_ready_transition_for_attempt(
                        state_dir,
                        attempt_id=attempt_id,
                        error=str(publish_error),
                    )
                except Exception:
                    compensation = "not_ready"
                failure_state_handled = compensation == "failed"
                different_attempt_active = compensation == "different_attempt"
                raise
        _report(on_progress, _PROGRESS[5])
    except BaseException as exc:
        if not failure_state_handled and not different_attempt_active:
            try:
                await _call_transition(
                    transition_fn,
                    state_dir,
                    "structured_failed",
                    expected_status="structured_running",
                    error=str(exc),
                    run_identity=plan.identity.as_dict(),
                    attempt_id=attempt_id,
                )
            except Exception as transition_error:
                from novelvideo.knowledge_pipeline import (
                    KnowledgePipelineTransitionError,
                )

                if not isinstance(transition_error, KnowledgePipelineTransitionError):
                    raise
        raise
    return {
        "char_count": len(content),
        "status": "source_ready",
        "pipeline": STRUCTURED_PIPELINE_VERSION,
        "published": published,
        "run_id": plan.run_id,
        "reused": reused,
        "chunks": len(plan.chunks),
        "section_type": plan.chunks[0].section_type,
    }


__all__ = [
    "STRUCTURED_PIPELINE_VERSION",
    "STRUCTURED_SCHEMA_VERSION",
    "SourceChunk",
    "StructuredProgressEvent",
    "StructuredRunIdentity",
    "StructuredRunPlan",
    "build_structured_run_plan",
    "chunk_source_text",
    "ingest_source_text_structured",
    "source_sha256",
]
