"""Authenticated project-scoped studio documents, with optimistic revisions."""

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, field_validator

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import resolve_project_scope
from novelvideo.creative_studios.store import RevisionConflict, StudioStore

router = APIRouter()
Kind = Literal['character', 'director', 'previs', 'intro']
DocumentId = Annotated[str, PathParam(pattern=r'^[A-Za-z0-9_-]{1,100}$')]


class SaveDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    data: dict[str, Any]
    expected_revision: int = Field(ge=0, strict=True)

    @field_validator('name')
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('请输入草稿名称')
        return value.strip()


async def _store(project: str, user: dict, *, write: bool = False) -> StudioStore:
    scope = await resolve_project_scope(project, user, required_role='editor' if write else 'viewer')
    return StudioStore(Path(scope.state_dir) / 'creative-studios.db')


@router.get('/projects/{project}/studios/{kind}')
async def list_documents(project: str, kind: Kind, user: dict = Depends(get_api_user)):
    return {'ok': True, 'data': (await _store(project, user)).list(kind)}


@router.get('/projects/{project}/studios/{kind}/{document_id}')
async def get_document(project: str, kind: Kind, document_id: DocumentId, user: dict = Depends(get_api_user)):
    document = (await _store(project, user)).get(kind, document_id)
    if document is None:
        raise HTTPException(404, '草稿不存在或已移除')
    return {'ok': True, 'data': document}


@router.get('/projects/{project}/studios/{kind}/{document_id}/history')
async def document_history(project: str, kind: Kind, document_id: DocumentId, user: dict = Depends(get_api_user)):
    return {'ok': True, 'data': (await _store(project, user)).history(kind, document_id)}


@router.put('/projects/{project}/studios/{kind}/{document_id}')
async def save_document(project: str, kind: Kind, document_id: DocumentId, body: SaveDocument, user: dict = Depends(get_api_user)):
    store = await _store(project, user, write=True)
    try:
        if kind == 'previs':
            from novelvideo.creative_studios.previs import PrevisScene
            body.data = PrevisScene.model_validate(body.data).model_dump(mode='json', exclude_none=True)
        if kind == 'intro' and 'spec' in body.data:
            from novelvideo.creative_studios.intro import IntroSpec
            body.data = {**body.data, 'spec': IntroSpec.model_validate(body.data['spec']).model_dump(mode='json')}
        result = store.save(kind, document_id, body.name, body.data, body.expected_revision)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(422, '草稿内容无效或过大，请检查输入并单独上传媒体') from exc
    return {'ok': True, 'data': result}
