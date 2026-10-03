import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_team_uses_configured_chat_backend(monkeypatch):
    from novelvideo.chat import service
    from novelvideo.chat import runtime_settings
    monkeypatch.setenv("ST_EDITION", "team")
    monkeypatch.setenv("DRAMACLAW_CHAT_BACKEND", "codex")
    monkeypatch.setattr(service, "is_codex_backend_available", lambda: True)
    monkeypatch.setattr(runtime_settings, "load_chat_runtime_settings", lambda: SimpleNamespace(backend="workbuddy"))
    assert service._chat_backend() == "workbuddy"


def test_team_hermes_workspaces_separate_project_memory(monkeypatch, tmp_path):
    from novelvideo.chat import hermes_workspace as workspaces
    monkeypatch.setenv("ST_EDITION", "team")
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))
    first = workspaces.ensure_user_hermes_workspace("alice", scope_key="project:first")
    second = workspaces.ensure_user_hermes_workspace("alice", scope_key="project:second")
    assert first != second
    assert workspaces.ensure_user_hermes_workspace("alice", scope_key="project:first") == first
    assert first.parent == second.parent


@pytest.mark.asyncio
async def test_running_chat_cancelled_when_authorization_revoked(monkeypatch):
    from novelvideo.api.routes import chat
    from novelvideo.chat.store import ChatScope
    from novelvideo.chat.hermes_pool import pool
    monkeypatch.setenv("ST_EDITION", "team")
    monkeypatch.setattr(chat, "_TEAM_AUTH_CHECK_INTERVAL", 0.001)
    cancelled = asyncio.Event()
    closed = []
    frames = []

    async def authenticate(ws):
        raise HTTPException(401, "revoked")

    async def close_user(username):
        closed.append(username)

    async def turn():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    async def send_json(payload):
        frames.append(payload)

    async def close(**kwargs):
        pass

    monkeypatch.setattr(chat, "_authenticate_ws", authenticate)
    monkeypatch.setattr(pool, "close_user", close_user)
    ws = SimpleNamespace(send_json=send_json, close=close)
    with pytest.raises(HTTPException):
        await chat._run_team_chat_turn(turn(), ws, "alice", ChatScope(kind="home"), turn_id="turn1")
    assert cancelled.is_set()
    assert closed == ["alice"]
    assert frames[-1]["type"] == "error"
    assert frames[-1]["message"] == "unauthorized"
    assert frames[-1]["turn_id"] == "turn1"


@pytest.mark.asyncio
async def test_team_turn_stops_after_project_editor_access_removed(monkeypatch):
    from novelvideo.api.routes import chat
    from novelvideo.chat.store import ChatScope
    from novelvideo.chat.hermes_pool import pool
    monkeypatch.setenv("ST_EDITION", "team")
    monkeypatch.setattr(chat, "_TEAM_AUTH_CHECK_INTERVAL", 0.001)
    checked = []
    closed = []

    async def authenticate(ws):
        return {"username": "alice"}

    async def access(*, user, scope, write, delegate):
        checked.append((scope.id, write, delegate))
        raise HTTPException(403, "editor access removed")

    async def close_user(username):
        closed.append(username)

    async def close(**kwargs):
        pass

    monkeypatch.setattr(chat, "_authenticate_ws", authenticate)
    monkeypatch.setattr(chat, "_require_chat_scope_access", access)
    monkeypatch.setattr(pool, "close_user", close_user)
    with pytest.raises(HTTPException):
        await chat._run_team_chat_turn(asyncio.sleep(10), SimpleNamespace(close=close), "alice", ChatScope(kind="project", id="p1"))
    assert checked == [("p1", True, True)]
    assert closed == ["alice"]


@pytest.mark.asyncio
async def test_completed_team_turn_keeps_worker_available(monkeypatch):
    from novelvideo.api.routes import chat
    from novelvideo.chat.store import ChatScope
    from novelvideo.chat.hermes_pool import pool
    monkeypatch.setenv("ST_EDITION", "team")

    async def unexpected_close(username):
        pytest.fail("successful turn must preserve the conversation worker")

    monkeypatch.setattr(pool, "close_user", unexpected_close)
    assert await chat._run_team_chat_turn(asyncio.sleep(0, result="done"), None, "alice", ChatScope(kind="home")) == "done"


@pytest.mark.asyncio
async def test_busy_second_tab_does_not_close_first_worker(monkeypatch):
    from novelvideo.api.routes import chat
    from novelvideo.chat.store import ChatScope
    from novelvideo.chat.hermes_pool import pool
    monkeypatch.setenv("ST_EDITION", "team")

    async def unexpected_close(username):
        pytest.fail("busy second tab must not terminate the first tab's turn")

    async def busy():
        raise RuntimeError("当前用户已有 AI 对话正在处理中")

    monkeypatch.setattr(pool, "close_user", unexpected_close)
    with pytest.raises(RuntimeError, match="处理中"):
        await chat._run_team_chat_turn(busy(), None, "alice", ChatScope(kind="home"))
