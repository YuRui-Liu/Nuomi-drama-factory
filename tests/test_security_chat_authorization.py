"""Exercise WebSocket authorization without starting an agent or provider."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from novelvideo.api.routes import chat
from novelvideo.chat.store import ChatScope


@pytest.fixture
def chat_harness(monkeypatch):
    state = SimpleNamespace(user={"id": "user", "username": "alice"}, role="editor")

    async def authenticate(websocket):
        return dict(state.user)

    async def resolve(*, user, project_id, required_role="viewer"):
        if project_id != "project-a" or (required_role == "editor" and state.role == "viewer"):
            raise HTTPException(403, "project access denied")
        return SimpleNamespace(requester_user_id="user", output_dir="/tmp/project-a",
                               state_dir="/tmp/project-a-state")

    async def finish(**kwargs):
        await kwargs["websocket"].send_json({"type": "test.executed"})

    monkeypatch.setattr(chat, "_authenticate_ws", authenticate)
    monkeypatch.setattr(chat, "resolve_project_context", resolve)
    monkeypatch.setattr(chat, "_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat.chat_service, "chat_run_lock_is_active", lambda username: False)
    state.prewarm = AsyncMock()
    state.sync = AsyncMock()
    state.project_turn = AsyncMock(side_effect=finish)
    state.home_turn = AsyncMock(side_effect=finish)
    monkeypatch.setattr(chat.chat_service, "prewarm_chat_backend", state.prewarm)
    monkeypatch.setattr(chat, "_sync_running_agent_scope", state.sync)
    monkeypatch.setattr(chat, "_stream_project_turn", state.project_turn)
    monkeypatch.setattr(chat, "_stream_home_turn", state.home_turn)
    meter = SimpleNamespace(require_feature_credit_balance=AsyncMock())
    monkeypatch.setattr(chat, "get_usage_meter", lambda: meter)
    app = FastAPI()
    app.include_router(chat.router)
    state.client = TestClient(app)
    return state


def _agent(*, full=False, home=False):
    return {
        "id": "user", "username": "alice", "credential_kind": "agent_session",
        "current_scope_kind": "home" if home else "project",
        "current_project_id": None if home else "project-a",
        "scopes": list(chat.chat_service.PAGE_AGENT_SCOPES) if full else ["projects:read"],
    }


def test_project_agent_starts_in_authorized_project_and_cannot_escape_to_home(chat_harness):
    h = chat_harness
    h.user = _agent(full=True)
    with h.client.websocket_connect("/chat/ws") as ws:
        initial = ws.receive_json()
        assert initial["scope"] == {"kind": "project", "id": "project-a"}
        ws.send_json({"type": "chat.message", "text": "change project", "scope": {"kind": "home"}})
        assert ws.receive_json()["type"] == "error"
    h.home_turn.assert_not_awaited()


@pytest.mark.parametrize("viewer", [False, True])
def test_readonly_agent_and_browser_viewer_cannot_execute_or_prewarm(chat_harness, viewer):
    h = chat_harness
    if viewer:
        h.role = "viewer"
    else:
        h.user = _agent()
    with h.client.websocket_connect("/chat/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "scope.set", "scope": {"kind": "project", "id": "project-a"}})
        assert ws.receive_json()["type"] == "scope.changed"
        ws.send_json({"type": "chat.message", "text": "change project"})
        assert ws.receive_json()["type"] == "error"
    h.project_turn.assert_not_awaited()
    h.prewarm.assert_not_awaited()
    h.sync.assert_not_awaited()


@pytest.mark.parametrize("agent", [False, True])
def test_editor_and_fully_scoped_agent_can_execute_in_project(chat_harness, agent):
    h = chat_harness
    if agent:
        h.user = _agent(full=True)
    with h.client.websocket_connect("/chat/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "scope.set", "scope": {"kind": "project", "id": "project-a"}})
        assert ws.receive_json()["type"] == "scope.changed"
        ws.send_json({"type": "chat.message", "text": "change project"})
        assert ws.receive_json()["type"] == "test.executed"
    h.project_turn.assert_awaited_once()


@pytest.mark.parametrize("missing", [s for s in chat.chat_service.PAGE_AGENT_SCOPES if s != "projects:read"])
def test_agent_missing_any_delegated_scope_cannot_gain_it_through_chat(chat_harness, missing):
    h = chat_harness
    h.user = _agent(full=True, home=True)
    h.user["scopes"].remove(missing)
    with h.client.websocket_connect("/chat/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.message", "text": "generate a video"})
        assert ws.receive_json()["type"] == "error"
    h.home_turn.assert_not_awaited()


def test_agent_scope_changes_are_rechecked_on_open_websocket(chat_harness):
    h = chat_harness
    h.user = _agent(full=True, home=True)
    with h.client.websocket_connect("/chat/ws") as ws:
        ws.receive_json()
        h.user = _agent(full=False, home=True)
        ws.send_json({"type": "chat.message", "text": "permissions have changed"})
        assert ws.receive_json()["type"] == "error"
    h.home_turn.assert_not_awaited()


def test_scope_switch_denial_preserves_authorized_scope(chat_harness):
    h = chat_harness
    h.user = _agent(full=True)
    with h.client.websocket_connect("/chat/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "scope.set", "scope": {"kind": "project", "id": "project-b"}})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "chat.message", "text": "still in project-a"})
        assert ws.receive_json()["type"] == "test.executed"
    assert h.project_turn.await_args.kwargs["scope"].id == "project-a"


@pytest.mark.parametrize("kind", ["asset", "task"])
def test_unsupported_scope_cannot_access_unvalidated_history_path(chat_harness, kind):
    h = chat_harness
    with h.client.websocket_connect("/chat/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "scope.set", "scope": {"kind": kind, "id": "../../other/project"}})
        assert ws.receive_json()["type"] == "error"
    assert chat._history.await_count == 1


async def test_browser_viewer_cannot_append_project_notification(chat_harness, monkeypatch):
    h = chat_harness
    h.role = "viewer"
    appended = []
    monkeypatch.setattr(chat.chat_service, "add_assistant_message", lambda *args, **kwargs: appended.append(args))
    payload = chat.ChatNotificationIn(scope=chat.ChatScopePayload(kind="project", id="project-a"), text="changed")
    with pytest.raises(HTTPException) as exc:
        await chat.append_chat_notification(payload, h.user)
    assert exc.value.status_code == 403
    assert appended == []
