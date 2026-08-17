"""Two-phase, revision-safe episode source imports."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.chapter_preview import load_novel_text
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.api.schemas import EpisodeImportCommitRequest
from novelvideo.episode_source_store import EpisodeImportPreviewNotFound, EpisodeSourceRevisionConflict, EpisodeSourceStore
from novelvideo.episode_import_records import EpisodeImportRecords
from novelvideo.episode_legacy_migration import ensure_legacy_migration
from novelvideo.episode_sources import apply_manual_episode_numbers, build_episode_candidate, resolve_episode_candidates
from novelvideo.ports import get_task_backend
from novelvideo.task_identity import project_task_state_key
from novelvideo.utils.document_parsers import DocumentParseError, is_supported_novel_path
from novelvideo.utils.upload_safety import MAX_UPLOAD_BYTES, sanitize_upload_filename

router = APIRouter()


async def _ensure_migration(store: EpisodeSourceStore, *, confirmed: bool = False):
    # Lightweight route tests use protocol fakes without project path metadata.
    if not hasattr(store, "sqlite_store"):
        from novelvideo.episode_source_store import EpisodeSourceMigrationResult
        return EpisodeSourceMigrationResult("not_needed")
    return await ensure_legacy_migration(store, confirmed_fallback=confirmed)


async def _resolve_store(project: str, user: dict) -> EpisodeSourceStore:
    resolved = await resolve_project_scope(project, user, required_role="editor")
    if resolved.ctx is None:
        raise HTTPException(status_code=409, detail={"code": "project_context_required"})
    return EpisodeSourceStore(await make_sqlite_store_for_context(resolved.ctx))


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(409, detail={"code": code, "error": message})


def _read_upload(upload: UploadFile) -> tuple[str, str] | dict:
    original = upload.filename or ""
    safe_name = sanitize_upload_filename(original)
    if not safe_name or safe_name != original:
        return {"file_id": original or "invalid", "filename": original, "episode_number": None, "title": None, "number_source": None, "status": "invalid", "error": "文件名不安全"}
    if not is_supported_novel_path(safe_name):
        return {"file_id": safe_name, "filename": safe_name, "episode_number": None, "title": None, "number_source": None, "status": "invalid", "error": "不支持的文件格式"}
    raw = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return {"file_id": safe_name, "filename": safe_name, "episode_number": None, "title": None, "number_source": None, "status": "invalid", "error": "文件过大"}
    try:
        with TemporaryDirectory(prefix="episode-import-") as temporary:
            path = Path(temporary) / safe_name
            path.write_bytes(raw)
            return safe_name, load_novel_text(path)
    except (DocumentParseError, UnicodeError, OSError):
        return {"file_id": safe_name, "filename": safe_name, "episode_number": None, "title": None, "number_source": None, "status": "invalid", "error": "文件解析失败"}


@router.post("/projects/{project}/episode-imports/preview")
async def preview_episode_imports(project: str, files: list[UploadFile] = File(...), user: dict = Depends(get_api_user)):
    store = await _resolve_store(project, user)
    migration = await _ensure_migration(store)
    base_revision = await store.current_revision()
    existing = {source.episode_number: source for source in await store.list_sources()}
    candidates, response_items = [], []
    for upload in files:
        parsed = _read_upload(upload)
        if isinstance(parsed, dict):
            response_items.append(parsed)
            continue
        candidate = build_episode_candidate(*parsed)
        candidates.append(candidate)
        response_items.append({
            "file_id": candidate.file_id,
            "filename": candidate.source_filename,
            "title": candidate.title or None,
            "episode_number": candidate.episode_number,
            "number_source": candidate.number_source,
            "warnings": list(candidate.warnings),
        })
    counts: dict[int, int] = {}
    for candidate in candidates:
        if candidate.episode_number is not None:
            counts[candidate.episode_number] = counts.get(candidate.episode_number, 0) + 1
    for item in response_items:
        number = item.get("episode_number")
        if number is None and "status" not in item:
            item["status"] = "needs_episode_number"
        elif number is not None and counts.get(number, 0) > 1:
            item["status"] = "conflict"
            item.setdefault("warnings", []).append("批次内部集号重复")
        elif number in existing:
            item.update(status="conflict", existing_revision=existing[number].source_revision)
        elif "status" not in item:
            item["status"] = "new"
    preview = await store.save_preview(base_revision=base_revision, items=candidates)
    return {"ok": True, "data": {"preview_id": preview.id, "base_revision": base_revision, "expires_at": preview.expires_at, "migration_status": migration.status, "confirmation_required": migration.status == "confirmation_required", "files": response_items}}


@router.post("/projects/{project}/episode-imports/legacy-migration/confirm")
async def confirm_legacy_episode_migration(project: str, user: dict = Depends(require_scope("tasks:submit"))):
    store = await _resolve_store(project, user)
    result = await _ensure_migration(store, confirmed=True)
    return {"ok": True, "data": {"status": result.status, "episode_numbers": list(result.episode_numbers)}}


@router.post("/projects/{project}/episode-imports/commit")
async def commit_episode_imports(project: str, body: EpisodeImportCommitRequest, user: dict = Depends(require_scope("tasks:submit"))):
    try:
        store = await _resolve_store(project, user)
        migration = await _ensure_migration(store)
        if migration.status == "confirmation_required":
            raise _conflict(
                "EPISODE_IMPORT_LEGACY_CONFIRMATION_REQUIRED",
                "旧项目原文无法可靠识别分集，请先确认迁移",
            )
        preview = await store.get_preview(body.preview_id)
        if preview is None:
            raise EpisodeImportPreviewNotFound(body.preview_id)
        if preview.base_revision != body.expected_revision or await store.current_revision() != body.expected_revision:
            raise EpisodeSourceRevisionConflict("project revision changed")
        submitted = {item.file_id: item for item in body.resolutions}
        filename_counts = {
            item.source_filename: sum(
                candidate.source_filename == item.source_filename for candidate in preview.items
            )
            for item in preview.items
        }
        for item in preview.items:
            if (
                item.file_id not in submitted
                and filename_counts[item.source_filename] == 1
                and item.source_filename in submitted
            ):
                submitted[item.file_id] = submitted.pop(item.source_filename)
        if set(submitted) != {item.file_id for item in preview.items}:
            raise ValueError("每个可导入文件都必须提供明确动作")
        manual_numbers = {
            item.file_id: submitted[item.file_id].episode_number
            for item in preview.items if item.episode_number is None
        }
        items = apply_manual_episode_numbers(preview.items, manual_numbers)
        for item in items:
            if submitted[item.file_id].episode_number != item.episode_number:
                raise ValueError(f"{item.source_filename} 集号与预检不一致")
        existing = {source.episode_number for source in await store.list_sources()}
        decisions = {
            item.episode_number: submitted[item.file_id].action
            for item in items if item.episode_number in existing
        }
        if any(submitted[item.file_id].action != "import" for item in items if item.episode_number not in existing):
            raise ValueError("新增文件动作必须为 import")
        resolution = resolve_episode_candidates(items, existing, decisions)
    except EpisodeImportPreviewNotFound as exc:
        raise _conflict("EPISODE_IMPORT_PREVIEW_STALE", "预检已过期，请重新预检") from exc
    except EpisodeSourceRevisionConflict as exc:
        raise _conflict("EPISODE_IMPORT_REVISION_CONFLICT", "项目已更新，请重新预检") from exc
    except ValueError as exc:
        code = "EPISODE_IMPORT_CONFLICT_UNRESOLVED" if "明确" in str(exc) or "overwrite 或 skip" in str(exc) else "EPISODE_IMPORT_INVALID_RESOLUTION"
        raise _conflict(code, str(exc)) from exc
    resolved = await resolve_project_scope(project, user, required_role="editor")
    if resolved.ctx is None:
        raise _conflict("project_context_required", "导入需要 project context")
    target_revision = body.expected_revision + (1 if resolution.imports else 0)
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx, task_type="episode_import", queue_kind="default", episode=0,
        payload={
            "expected_revision": body.expected_revision,
            "target_revision": target_revision,
            "snapshot": {
                "preview_id": body.preview_id,
                "expected_revision": body.expected_revision,
                "resolutions": decisions,
                "items": [asdict(item) for item in items],
            },
            "items": [asdict(item) for item in items],
        },
    )
    return {"ok": True, "task_type": "episode_import", "task_id": queued.task_state.task_id, "task_key": project_task_state_key("episode_import", resolved.ctx.project_id, 0), "backend": queued.backend, "queue": queued.queue, "target_revision": target_revision}


@router.get("/projects/{project}/episode-imports")
async def list_episode_imports(project: str, user: dict = Depends(get_api_user)):
    store = await _resolve_store(project, user)
    migration = await _ensure_migration(store)
    items = []
    for source in await store.list_sources():
        item = asdict(source)
        item["revision"] = item.pop("source_revision")
        item["filename"] = item.pop("source_filename")
        item.pop("content", None)
        items.append(item)
    imports, stale = [], []
    if hasattr(store, "sqlite_store"):
        records = EpisodeImportRecords(store.sqlite_store)
        imports, stale = await records.list_results(), await records.list_stale()
    return {"ok": True, "data": {"project_revision": await store.current_revision(), "migration_status": migration.status, "confirmation_required": migration.status == "confirmation_required", "items": items, "imports": imports, "stale": stale}}


@router.delete("/projects/{project}/episode-imports/stale/{episode_number}/{stage}")
async def clear_episode_import_stale(episode_number: int, stage: str, project: str, source_revision: int, user: dict = Depends(require_scope("tasks:submit"))):
    store = await _resolve_store(project, user)
    try:
        cleared = await EpisodeImportRecords(store.sqlite_store).mark_stage_consumed(
            episode_number=episode_number,
            stage=stage,
            source_revision=source_revision,
        )
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "EPISODE_IMPORT_INVALID_STALE_STAGE", "error": str(exc)}) from exc
    return {"ok": True, "data": {"cleared": cleared}}
