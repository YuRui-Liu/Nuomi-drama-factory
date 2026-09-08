from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.asset_imports import AssetImportService, AssetType
from novelvideo.utils.upload_safety import MAX_UPLOAD_BYTES

router = APIRouter()


def _kind(value: str) -> AssetType:
    try:
        return AssetType(value)
    except ValueError as exc:
        raise HTTPException(422, detail="asset_type 仅允许 character、scene、prop") from exc


def _user_id(user: dict) -> str:
    return str(user.get("user_id") or user.get("id") or user.get("username") or "")


@router.post("/projects/{project}/asset-imports/{asset_type}/preview")
async def preview_asset_import(
    project: str,
    asset_type: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail="导入文件过大")
    store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        return await AssetImportService(store).preview(
            asset_type=_kind(asset_type), filename=file.filename or "", payload=payload,
            project_id=project, user_id=_user_id(user),
        )
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    finally:
        await store.close()


@router.post("/projects/{project}/asset-imports/{asset_type}/{import_id}/confirm")
async def confirm_asset_import(
    project: str,
    asset_type: str,
    import_id: str,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        return await AssetImportService(store).confirm(import_id, _kind(asset_type), project, _user_id(user))
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    finally:
        await store.close()
