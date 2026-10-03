import os
import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture
def port(monkeypatch):
    from novelvideo.chat import agent_tools
    calls = []

    async def create(**kwargs):
        calls.append(("create", kwargs))
        return SimpleNamespace(value="secret-token")

    async def verify(token):
        calls.append(("verify", token))
        return {"username": "alice"}

    async def revoke(token):
        calls.append(("revoke", token))

    monkeypatch.setattr(agent_tools, "get_auth_session_port", lambda: SimpleNamespace(create_agent_session=create, verify_agent_session=verify, revoke_agent_session=revoke))
    return calls


@pytest.mark.asyncio
async def test_tool_sessions_do_not_share_environment_and_revoke(port):
    from novelvideo.chat.agent_tools import AgentToolSession
    original = dict(os.environ)
    async with AgentToolSession("alice", "project", "p1") as first:
        async with AgentToolSession("bob", "project", "p2") as second:
            assert first._plugin._default_project_id() == "p1"
            assert second._plugin._default_project_id() == "p2"
            assert first._plugin.os.environ is not second._plugin.os.environ
            assert "secret-token" not in str(first.specs())
    assert dict(os.environ) == original
    assert len([c for c in port if c[0] == "revoke"]) == 2


@pytest.mark.parametrize("path", ["https://evil.test/api/v1/projects/p1", "/api/v1/projects/p2", "/api/v1/projects/p1/grants", "/api/v1/projects/p1/../p2", "/api/v1/projects/p1/%252e%252e/p2", "/api/v1/admin/users", "/api/v1/projects", "/api/v1/projects/p1/delete"])
def test_tool_path_rejects_escape_and_management(path):
    from novelvideo.chat.agent_tools import AgentToolSession
    session = AgentToolSession("alice", "project", "p1")
    with pytest.raises(ValueError):
        session._normalize_request_path("GET", path)


@pytest.mark.asyncio
async def test_tool_http_has_scoped_bearer_and_no_redirect(port):
    from novelvideo.chat.agent_tools import AgentToolSession
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://evil.test/"})

    async with AgentToolSession("alice", "project", "p1") as session:
        session._client.close()
        session._client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False)
        result = await session.call("dramaclaw_get", {"path": "/projects/p1"})
        assert "302" in str(result)
        assert len(requests) == 1
        assert requests[0].headers["Authorization"] == "Bearer secret-token"
        assert [c[0] for c in port].count("verify") >= 1
    with pytest.raises(RuntimeError):
        await session.validate()


@pytest.mark.asyncio
async def test_cancel_revokes_before_inflight_tool_finishes_without_closing_client(port):
    from novelvideo.chat.agent_tools import AgentToolSession
    entered = threading.Event()
    release = threading.Event()
    session = AgentToolSession("alice", "project", "p1")

    def handler(arguments):
        entered.set()
        release.wait(3)
        assert not session._client.is_closed
        with pytest.raises(RuntimeError, match="closed"):
            session._request("GET", "/projects/p1")
        return "finished"

    async def run():
        async with session:
            session._tools["blocked"] = ({"parameters": {"type": "object"}}, handler)
            await session.call("blocked", {})

    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert any(kind == "revoke" for kind, _ in port)
        assert not session._client.is_closed
        pending = list(session._pending)
        release.set()
        await asyncio.gather(*pending)
        assert session._client.is_closed
    finally:
        release.set()


@pytest.mark.asyncio
async def test_optional_tool_query_values_are_omitted(port):
    from novelvideo.chat.agent_tools import AgentToolSession
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with AgentToolSession("alice", "project", "p1") as session:
        session._client.close()
        session._client = httpx.Client(transport=httpx.MockTransport(handle))
        await asyncio.to_thread(session._request, "GET", "/projects/p1/tasks", query={"episode": None, "beat_num": "", "limit": 0})
    assert dict(requests[0].url.params) == {"limit": "0"}
