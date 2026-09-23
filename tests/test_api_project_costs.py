from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from novelvideo.api.auth import get_api_user
from novelvideo.costs.service import CostService
from novelvideo.costs.store import CostStore

@pytest.fixture
def client(tmp_path, monkeypatch):
    from novelvideo.api.routes import project_costs as routes
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.settings_router)
    user = {'id':'u','role':'admin'}
    app.dependency_overrides[get_api_user] = lambda: user
    monkeypatch.setattr(routes, 'get_cost_service', lambda: CostService(CostStore(tmp_path/'cost.db')))
    async def resolve(**kw):
        if kw['project_id'] == 'foreign':
            raise HTTPException(403)
        if kw['required_role'] == 'editor' and user['role'] == 'viewer':
            raise HTTPException(403)
        return SimpleNamespace(project_id='p')
    monkeypatch.setattr(routes, 'resolve_project_context', resolve)
    monkeypatch.setattr(routes, 'require_project_home_node', lambda *a, **k: None)
    monkeypatch.setattr(routes, 'get_project_registry', lambda: SimpleNamespace(get_project=AsyncMock(return_value=SimpleNamespace(created_at=datetime.now(timezone.utc)))))
    return TestClient(app), user

def test_project_access_and_missing_detail(client):
    c, _ = client
    assert c.get('/projects/foreign/costs/snapshot').status_code == 403
    assert c.get('/projects/p/costs/snapshot').json()['ok'] is True
    assert c.get('/projects/p/costs/entries/missing').status_code == 404
    assert c.get('/projects/p/costs/entries?limit=0').status_code == 422

def test_settings_authorization_validation_conflict(client):
    c, user = client
    user['role'] = 'viewer'
    assert c.post('/cost-settings/price-rules', json={}).status_code == 403
    user['role'] = 'admin'
    assert c.post('/cost-settings/price-rules', json={'formula':'secret'}).status_code == 422
    rule = dict(id='r',version='1',media_type='image',starts_at='2026-09-23T00:00:00Z',items=[dict(unit='item',unit_price='2')])
    assert c.post('/cost-settings/price-rules', json=rule).status_code == 200
    rule['items'][0]['unit_price'] = '3'
    assert c.post('/cost-settings/price-rules', json=rule).status_code == 409
    user['credential_kind'] = 'agent_session'
    assert c.get('/cost-settings/price-rules').status_code == 403

def test_preview_apply_contract(client):
    c, _ = client
    preview = c.post('/projects/p/costs/reprice-preview').json()['data']
    response = c.post('/projects/p/costs/reprice-apply', json={'preview_id': preview['preview_id']})
    assert response.json()['data']['applied'] is True

def test_viewer_cannot_reprice_and_real_foreign_detail(client):
    from novelvideo.api.routes import project_costs as routes
    from novelvideo.costs.models import CostAttempt
    c, user = client
    routes.get_cost_service().store.create_attempt(CostAttempt(attempt_id='other',project_id='other-project',provider='x',account_id='x',model='m',media_type='image',occurred_at=datetime.now(timezone.utc)))
    assert c.get('/projects/p/costs/entries/other').status_code == 404
    user['role'] = 'viewer'
    assert c.post('/projects/p/costs/reprice-preview').status_code == 403
    assert c.post('/projects/p/costs/reprice-apply',json={'preview_id':'x'}).status_code == 403

def test_overlap_stop_and_subscription_contract(client):
    c, _ = client
    rule = dict(id='r',version='1',media_type='image',starts_at='2026-09-23T00:00:00Z',items=[dict(unit='item',unit_price='2')])
    assert c.post('/cost-settings/price-rules',json=rule).status_code == 200
    rule['version'] = '2'
    assert c.post('/cost-settings/price-rules',json=rule).status_code == 409
    assert c.post('/cost-settings/price-rules/r/versions/1/stop',json={'ends_at':'2026-09-24T00:00:00Z'}).status_code == 200
    sub = dict(id='s',provider='x',account_id='a',starts_at='2026-09-23T00:00:00Z')
    assert c.post('/cost-settings/subscriptions',json=sub).status_code == 200
    assert c.get('/cost-settings/subscriptions').json()['data'][0]['id'] == 's'

def test_unauthenticated_denied(client):
    c, _ = client
    c.app.dependency_overrides[get_api_user] = lambda: (_ for _ in ()).throw(HTTPException(401))
    assert c.get('/projects/p/costs/snapshot').status_code == 401

def test_local_audit_actor(client):
    from novelvideo.api.routes import project_costs as routes
    c, user = client
    user.clear()
    user.update(username='local',role='owner',credential_kind='user')
    assert c.post('/cost-settings/subscriptions',json=dict(id='s',provider='x',account_id='a',starts_at='2026-09-23T00:00:00Z')).status_code == 200
    with routes.get_cost_service().store._connection() as db:
        assert db.execute('SELECT actor FROM cost_settings_audit').fetchone()[0] == 'local'
