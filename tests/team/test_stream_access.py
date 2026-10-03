import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_stream_rechecks_project_grant(monkeypatch):
    from novelvideo.api.routes import tasks

    async def credential(request):
        return {"id": "member", "username": "member"}

    async def revoked(**kwargs):
        raise HTTPException(403, "revoked")

    monkeypatch.setattr(tasks, "verify_credential_for_request", credential)
    monkeypatch.setattr(tasks, "resolve_project_context", revoked)
    valid, _ = await tasks._sse_token_still_valid(None, 0, project_id="project")
    assert not valid


@pytest.mark.asyncio
async def test_stream_auth_errors_fail_closed(monkeypatch):
    from novelvideo.api.routes import tasks

    async def unavailable(request):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(tasks, "verify_credential_for_request", unavailable)
    valid, _ = await tasks._sse_token_still_valid(None, 0)
    assert not valid
