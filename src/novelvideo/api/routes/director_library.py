"""Authenticated global personal library; never changes project or task state."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes.agent_teams import personal, errors
from novelvideo.creative_studios.director_library import DirectorInput, DirectorLibrary

router = APIRouter()


class Update(DirectorInput):
    expected_revision: int = Field(strict=True, ge=1)


def library(user):
    if user.get('credential_kind') == 'agent_session':
        raise HTTPException(403, '个人导演库请使用用户会话访问')
    service = personal(user)
    return DirectorLibrary(service.library, user['username'])


@router.get('/director-library')
def list_directors(user: dict = Depends(get_api_user)):
    return {'data': library(user).list()}


@router.post('/director-library', status_code=201)
def create_director(body: DirectorInput, user: dict = Depends(get_api_user)):
    with errors():
        return {'data': library(user).save(body)}


@router.put('/director-library/{entry_id}')
def update_director(entry_id: str, body: Update, user: dict = Depends(get_api_user)):
    with errors():
        try:
            data = DirectorInput.model_validate(body.model_dump(exclude={'expected_revision'}))
            return {'data': library(user).save(data, body.expected_revision, entry_id)}
        except KeyError as exc:
            raise HTTPException(404, '个人导演已删除或不存在；当前副本仍可另存') from exc


@router.delete('/director-library/{entry_id}')
def delete_director(entry_id: str, expected_revision: int = Query(ge=1), user: dict = Depends(get_api_user)):
    with errors():
        try:
            library(user).delete(entry_id, expected_revision)
            return {'data': {'deleted': True}}
        except KeyError as exc:
            raise HTTPException(404, '个人导演已删除或不存在') from exc
