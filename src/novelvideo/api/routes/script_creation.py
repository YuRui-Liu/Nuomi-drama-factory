"""Creative document endpoints scoped to a project."""
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.script_creation.documents import import_episode_source
from novelvideo.script_creation.store import DocumentConflict, DocumentNotFound, DocumentStore, DocumentValidation

router = APIRouter()
PREFIX = "/projects/{project}/script-creation"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlockBody(Strict):
    id: str = ""
    markdown: str


class CreateBody(Strict):
    kind: str
    title: str
    markdown: str = ""
    episode_number: int | None = None
    client_mutation_id: str = Field(min_length=1)
    blocks: list[BlockBody] | None = None


class SaveBody(Strict):
    base_revision_id: str
    markdown: str
    client_mutation_id: str = Field(min_length=1)
    blocks: list[BlockBody] | None = None


class RestoreBody(Strict):
    revision_id: str
    base_revision_id: str
    client_mutation_id: str = Field(min_length=1)


class ImportBody(Strict):
    episode_number: int = Field(ge=1)


def _blocks(items):
    return [item.model_dump() for item in items] if items is not None else None


def _ok(value):
    if isinstance(value, list):
        return {"ok": True, "data": [asdict(item) for item in value]}
    return {"ok": True, "data": asdict(value)}


def _error(exc):
    if isinstance(exc, DocumentNotFound):
        return HTTPException(404, detail=str(exc))
    if isinstance(exc, DocumentConflict):
        return HTTPException(409, detail={"message": str(exc), "current_revision_id": exc.current_revision_id})
    return HTTPException(422, detail=str(exc))


async def _store(project, user, role):
    resolved = await resolve_project_scope(project, user, required_role=role)
    store = DocumentStore(Path(resolved.state_dir) / "data.db")
    await store.initialize()
    return store, resolved


@router.get(PREFIX + "/documents")
async def list_documents(project: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "viewer")
    return _ok(await store.list())


@router.post(PREFIX + "/documents")
async def create_document(project: str, body: CreateBody, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "editor")
    try:
        return _ok(await store.create(**body.model_dump(exclude={"blocks"}), blocks=_blocks(body.blocks)))
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.get(PREFIX + "/documents/{document_id}")
async def get_document(project: str, document_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "viewer")
    try:
        return _ok(await store.get(document_id))
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.put(PREFIX + "/documents/{document_id}")
async def save_document(project: str, document_id: str, body: SaveBody, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "editor")
    try:
        return _ok(await store.save(document_id, **body.model_dump(exclude={"blocks"}), blocks=_blocks(body.blocks)))
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.get(PREFIX + "/documents/{document_id}/revisions")
async def list_revisions(project: str, document_id: str, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "viewer")
    try:
        return _ok(await store.revisions(document_id))
    except DocumentNotFound as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/documents/{document_id}/restore")
async def restore_document(project: str, document_id: str, body: RestoreBody, user: dict = Depends(get_api_user)):
    store, _ = await _store(project, user, "editor")
    try:
        return _ok(await store.restore(document_id, **body.model_dump()))
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/imports")
async def import_document(project: str, body: ImportBody, user: dict = Depends(get_api_user)):
    store, resolved = await _store(project, user, "editor")
    sqlite = await make_sqlite_store_for_context(resolved.ctx)
    try:
        return _ok(await import_episode_source(store, EpisodeSourceStore(sqlite), body.episode_number))
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc
    finally:
        await sqlite.close()
