"""Authenticated personal libraries and project-scoped, revisioned team bindings."""
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, ValidationError

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import get_user_base_dir, resolve_project_scope
from novelvideo.agent_teams.models import Contract
from novelvideo.agent_teams.service import AgentTeamService, TeamError, builtin_template
from novelvideo.agent_teams.store import AgentTeamStore, RevisionConflict

router = APIRouter()
Revision = Annotated[int, Field(strict=True, ge=0)]


@router.get('/agent-team-builtin-methods')
async def builtin_methods(user: dict = Depends(get_api_user)):
    from novelvideo.agent_teams.builtin_methods import builtin_methods as inspect
    return inspect()


def connected_subtasks():
    try:
        from novelvideo.agent_teams.runtime import connected_subtasks as connected
    except ModuleNotFoundError as exc:
        if exc.name != 'novelvideo.agent_teams.runtime':
            raise
        return set()
    return connected()


class Save(Contract):
    data: dict
    expected_revision: Revision


class Activate(Contract):
    draft_revision: Revision
    expected_active_revision: Revision


class Restore(Contract):
    role_id: str
    subtask_id: str
    field: str
    expected_revision: Revision


class Upgrade(Contract):
    template_id: str
    template_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_revision: Revision


class Rollback(Activate):
    active_revision: Annotated[int, Field(strict=True, ge=1)]


class Copy(Contract):
    new_id: str
    name: str
    revision: Annotated[int, Field(strict=True, ge=1)] = 1


@contextmanager
def errors():
    try:
        yield
    except RevisionConflict as exc:
        raise HTTPException(409, {'code': 'REVISION_CONFLICT', 'field': 'revision', 'message': str(exc)}) from exc
    except TeamError as exc:
        raise HTTPException(403 if exc.code == 'ACCESS_DENIED' else 422,
                            {'code': exc.code, 'field': exc.field, 'message': str(exc)}) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(422, {'code': 'INVALID_DATA', 'field': 'data', 'message': str(exc)}) from exc


def personal(user):
    username = user.get('username', '')
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_@.\-]+', username) or username in ('.', '..'):
        raise HTTPException(403, {'code': 'INVALID_IDENTITY', 'field': 'username'})
    library = AgentTeamStore(get_user_base_dir(username) / '.creative-studios' / 'agent-team-library.db')
    return AgentTeamService(library, library, username, connected_subtasks)


async def project_service(project, user, role):
    scope = await resolve_project_scope(project, user, required_role=role)
    service = personal(user)
    service.store = AgentTeamStore(Path(scope.state_dir) / 'agent-team.db')
    service.project_id = scope.ctx.project_id
    return service


@router.get('/projects/{project}/agent-team')
async def overview(project: str, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'viewer')
    with errors():
        return service.read(service.project_id)


@router.put('/projects/{project}/agent-team/draft')
async def save_draft(project: str, body: Save, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'editor')
    with errors():
        return service.save_draft(service.project_id, body.data, body.expected_revision)


@router.get('/projects/{project}/agent-team/versions')
async def versions(project: str, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'viewer')
    return service.store.list_versions(service.project_id)


@router.get('/projects/{project}/agent-team/diff')
async def diff(project: str, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'viewer')
    with errors():
        return service.diff(service.project_id)


@router.post('/projects/{project}/agent-team/activate')
async def activate(project: str, body: Activate, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'editor')
    with errors():
        return service.activate(service.project_id, **body.model_dump())


@router.post('/projects/{project}/agent-team/restore-field')
async def restore(project: str, body: Restore, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'editor')
    with errors():
        return service.restore_field(service.project_id, **body.model_dump())


@router.post('/projects/{project}/agent-team/upgrade')
async def upgrade(project: str, body: Upgrade, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'editor')
    with errors():
        return service.upgrade(service.project_id, **body.model_dump())


@router.post('/projects/{project}/agent-team/rollback')
async def rollback(project: str, body: Rollback, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'editor')
    with errors():
        return service.rollback(service.project_id, **body.model_dump())


@router.post('/projects/{project}/agent-team/copy-template-from-project')
async def copy_project(project: str, body: Copy, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'viewer')
    with errors():
        return service.copy_template('', 1, body.new_id, body.name, project=service.project_id)


@router.get('/projects/{project}/agent-team/resources/{id}/usage')
async def project_resource_usage(project: str, id: str, user: dict = Depends(get_api_user)):
    service = await project_service(project, user, 'viewer')
    return service.store.list_resource_usage(id)


@router.get('/agent-team-resources/{id}/usage')
async def resource_usage(id: str, user: dict = Depends(get_api_user)):
    return personal(user).library.list_resource_usage(id)


@router.get('/agent-team-templates')
async def templates(user: dict = Depends(get_api_user)):
    service = personal(user)
    return [builtin_template(), *service.library.list_templates(service.username)]


@router.get('/agent-team-resources')
async def resources(user: dict = Depends(get_api_user)):
    service = personal(user)
    return service.library.list_resources(service.username)


@router.get('/agent-team-templates/{id}')
async def template(id: str, revision: int | None = Query(None, ge=1), user: dict = Depends(get_api_user)):
    service = personal(user)
    with errors():
        result = builtin_template() if id == 'builtin' and revision in (None, 1) else service.library.get_template(id, revision)
        if result is None:
            raise HTTPException(404, {'code': 'TEMPLATE_NOT_FOUND', 'field': 'id'})
        return result


@router.get('/agent-team-resources/{id}')
async def resource(id: str, revision: int | None = Query(None, ge=1), user: dict = Depends(get_api_user)):
    result = personal(user).library.get_resource(id, revision)
    if result is None:
        raise HTTPException(404, {'code': 'RESOURCE_NOT_FOUND', 'field': 'id'})
    return result


@router.put('/agent-team-templates/{id}')
async def publish_template(id: str, body: Save, user: dict = Depends(get_api_user)):
    with errors():
        if body.data.get('id', id) != id:
            raise TeamError('IDENTITY_MISMATCH', 'id')
        return personal(user).publish_template({**body.data, 'id': id}, body.expected_revision)


@router.put('/agent-team-resources/{id}')
async def publish_resource(id: str, body: Save, user: dict = Depends(get_api_user)):
    with errors():
        if body.data.get('id', id) != id:
            raise TeamError('IDENTITY_MISMATCH', 'id')
        return personal(user).publish_resource({**body.data, 'id': id}, body.expected_revision)


@router.post('/agent-team-templates/{id}/copy')
async def copy_template(id: str, body: Copy, user: dict = Depends(get_api_user)):
    with errors():
        return personal(user).copy_template(id, body.revision, body.new_id, body.name)
