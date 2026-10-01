"""Authenticated project ledger and administrator-only global cost settings."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
from novelvideo.api.routes.media_capabilities import require_media_capability_admin
from novelvideo.costs.models import CostStatus, Identity, MediaType, PriceRule
from novelvideo.costs.queries import CostQueries
from novelvideo.costs.reprice import RepriceService
from novelvideo.costs.presets import preset_status, install_presets
from novelvideo.costs.service import get_cost_service
from novelvideo.costs.storage_models import Subscription
from novelvideo.costs.store import _json
from novelvideo.ports import get_project_registry
from novelvideo.project_context import resolve_project_context, require_project_home_node

router = APIRouter(prefix='/projects/{project}/costs')
settings_router = APIRouter(prefix='/cost-settings', dependencies=[Depends(require_media_capability_admin)])


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: Identity


class StopBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ends_at: AwareDatetime


async def _project(project, user, role='viewer'):
    ctx = await resolve_project_context(user=user, project_id=project, required_role=role)
    require_project_home_node(ctx, operation='access project costs')
    return ctx.project_id


def _result(action):
    try:
        return {'ok': True, 'data': action()}
    except KeyError:
        raise HTTPException(404, 'Cost record not found') from None
    except ValidationError:
        raise HTTPException(422, 'Invalid cost settings') from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get('/snapshot')
async def snapshot(project: str, user: dict = Depends(get_api_user)):
    project_id = await _project(project, user)
    record = await get_project_registry().get_project(project_id)
    return _result(lambda: CostQueries(get_cost_service().store).snapshot(project_id, datetime.now(timezone.utc), created_at=record.created_at if record else None))


@router.get('/entries')
async def entries(project: str, channel: str | None = None, media: MediaType | None = None,
                  status: CostStatus | None = None, cursor: str | None = None,
                  limit: int = Query(50, ge=1, le=500), user: dict = Depends(get_api_user)):
    project_id = await _project(project, user)
    try:
        return {'ok': True, 'data': CostQueries(get_cost_service().store).entries(project_id, channel, media, status, cursor, limit)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('/entries/{attempt_id}')
async def detail(project: str, attempt_id: str, user: dict = Depends(get_api_user)):
    project_id = await _project(project, user)
    return _result(lambda: CostQueries(get_cost_service().store).entry_detail(project_id, attempt_id))


@router.post('/reprice-preview')
async def preview(project: str, user: dict = Depends(get_api_user)):
    project_id = await _project(project, user, 'editor')
    return _result(lambda: RepriceService(get_cost_service().store).preview(project_id))


@router.post('/recover')
async def recover(project: str, user: dict = Depends(require_media_capability_admin)):
    from novelvideo.costs.setup import recover_project_costs

    project_id = await _project(project, user, 'editor')
    record = await get_project_registry().get_project(project_id)
    if record is None:
        raise HTTPException(404, 'Project not found')
    service = get_cost_service()
    result = await recover_project_costs(record, service,
        get_media_capability_store().list_providers(), get_media_credential_resolver())
    reprice = RepriceService(service.store)
    preview = reprice.preview(project_id)
    applied = reprice.apply(project_id, preview['preview_id'])
    return {'ok': True, 'data': {**result, 'repriced': applied['affected_count']}}


@router.post('/reprice-apply')
async def apply(project: str, body: ApplyBody, user: dict = Depends(get_api_user)):
    project_id = await _project(project, user, 'editor')
    return _result(lambda: RepriceService(get_cost_service().store).apply(project_id, body.preview_id))


@settings_router.get('/price-rules')
async def price_rules():
    return _result(lambda: get_cost_service().store.list_price_rules())


@settings_router.get('/presets')
async def presets():
    return _result(lambda: preset_status(get_cost_service().store, get_media_capability_store().list_providers()))


@settings_router.post('/presets')
async def save_presets(user: dict = Depends(require_media_capability_admin)):
    store = get_cost_service().store
    return _result(lambda: _audit(store, user,
        lambda: install_presets(store, get_media_capability_store().list_providers()),
        {'operation': 'install_platform_presets', 'version': '2026-09-24'}))


def _audit(store, user, operation, value):
    # Store only identifiers and validated configuration; never user credentials.
    with store.transaction() as db:
        result = operation()
        db.execute('INSERT INTO cost_settings_audit(actor,recorded_at,body_json) VALUES (?,?,?)',
                   (str(user.get('id') or user.get('user_id') or user.get('username') or 'unknown'), datetime.now(timezone.utc).isoformat(), _json(value)))
        return result


@settings_router.post('/price-rules')
async def create_price_rule(body: PriceRule, user: dict = Depends(require_media_capability_admin)):
    store = get_cost_service().store
    return _result(lambda: _audit(store, user, lambda: store.add_price_rule(body), {'operation':'create_price_rule','rule':body.model_dump(mode='json')}))


@settings_router.post('/price-rules/{rule_id}/versions/{version}/stop')
async def stop_price_rule(rule_id: str, version: str, body: StopBody, user: dict = Depends(require_media_capability_admin)):
    store = get_cost_service().store
    return _result(lambda: _audit(store, user, lambda: store.stop_price_rule(rule_id, version, body.ends_at),
                                 {'operation':'stop_price_rule','id':rule_id,'version':version, **body.model_dump(mode='json')}))


@settings_router.get('/subscriptions')
async def subscriptions():
    return _result(lambda: get_cost_service().store.list_subscriptions())


@settings_router.post('/subscriptions')
async def save_subscription(body: Subscription, user: dict = Depends(require_media_capability_admin)):
    store = get_cost_service().store
    return _result(lambda: _audit(store, user, lambda: store.save_subscription(body), {'operation':'save_subscription','subscription':body.model_dump(mode='json')}))
