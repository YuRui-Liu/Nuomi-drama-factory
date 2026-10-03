"""Exercise dispatcher and real event consumers without models or credentials."""
from unittest.mock import AsyncMock, Mock

import pytest

from novelvideo.chat import dispatcher, service
from novelvideo.chat.cli_agent import CliAgentThread, Decision
from novelvideo.chat.runtime_settings import ChatRuntimeSettings


@pytest.fixture
def team(monkeypatch, tmp_path):
    monkeypatch.setenv("ST_EDITION", "team")
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["codex", "workbuddy", "deepseek_harness"])
async def test_dispatcher_builds_configured_cli(team, monkeypatch, backend):
    monkeypatch.setattr(dispatcher, "load_chat_runtime_settings", lambda: ChatRuntimeSettings(backend=backend, model="test-model", reasoning_effort="low"))
    thread = await dispatcher.get_chat_thread("alice", scope_kind="project", project_id="p1")
    assert isinstance(thread, CliAgentThread)
    assert (thread.snapshot.runtime, thread.snapshot.model, thread.snapshot.reasoning_effort) == (backend, "test-model", "low")
    assert (thread.username, thread.scope_kind, thread.project_id) == ("alice", "project", "p1")


@pytest.mark.asyncio
async def test_dispatcher_forwards_hermes_model(team, monkeypatch):
    from novelvideo.chat.hermes_pool import pool
    monkeypatch.setattr(dispatcher, "load_chat_runtime_settings", lambda: ChatRuntimeSettings(backend="hermes", model="gateway-model"))
    get = AsyncMock(return_value=object())
    monkeypatch.setattr(pool, "get_for_user", get)
    assert await dispatcher.get_chat_thread("alice", scope_kind="home", project_id=None) is get.return_value
    get.assert_awaited_once_with("alice", scope_kind="home", project_id=None, model="gateway-model")


@pytest.mark.asyncio
async def test_cancel_reaches_both_backends_after_configuration_changes(team, monkeypatch):
    from novelvideo.chat import cli_agent
    from novelvideo.chat.hermes_pool import pool
    monkeypatch.setattr(dispatcher, "load_chat_runtime_settings", Mock(side_effect=AssertionError("Cancellation must not depend on current settings")))
    cli_cancel, hermes_cancel = AsyncMock(return_value=True), AsyncMock(return_value=False)
    monkeypatch.setattr(cli_agent, "cancel_user", cli_cancel)
    monkeypatch.setattr(pool, "close_user", hermes_cancel)
    assert await dispatcher.cancel_user("alice") is True
    cli_cancel.assert_awaited_once_with("alice")
    hermes_cancel.assert_awaited_once_with("alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["codex", "workbuddy", "deepseek_harness"])
async def test_team_service_uses_common_event_path(team, monkeypatch, backend):
    monkeypatch.setattr(service, "_acquire_chat_run_lock", lambda *args: "lock")
    monkeypatch.setattr(service, "_release_chat_run_lock", Mock())
    monkeypatch.setattr(service, "_chat_run_lock_heartbeat_loop", AsyncMock())
    monkeypatch.setattr(service, "_chat_backend", lambda: backend)
    common = AsyncMock(return_value={"content": "reply"})
    monkeypatch.setattr(service, "_stream_assistant_reply_hermes", common)
    monkeypatch.setattr(service, "_stream_assistant_reply_codex", AsyncMock(side_effect=AssertionError("Legacy SDK")))
    monkeypatch.setattr(service, "_stream_assistant_reply_claude", AsyncMock(side_effect=AssertionError("Legacy SDK")))
    result = await service.stream_assistant_reply("alice", "p1", "hello", AsyncMock())
    assert result == {"content": "reply"}
    common.assert_awaited_once()


@pytest.fixture
def fake_cli_runtime(team, monkeypatch):
    from novelvideo.chat import cli_agent
    class Session:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def specs(self):
            return []
        async def validate(self):
            pass
    runtime = Mock()
    runtime.run_structured = AsyncMock(return_value=Decision(message="A completed reply", tool_calls=[]))
    monkeypatch.setattr(cli_agent, "AgentToolSession", Session)
    monkeypatch.setattr(cli_agent, "build_text_task_runtime", lambda snapshot: runtime)
    monkeypatch.setattr(dispatcher, "load_chat_runtime_settings", lambda: ChatRuntimeSettings(backend="codex", model="test-model"))
    return runtime


@pytest.mark.asyncio
async def test_project_consumer_calls_real_cli_stream_and_persists_reply(fake_cli_runtime, monkeypatch):
    monkeypatch.setattr(service, "_prompt_with_user_context", lambda username, project, prompt: prompt)
    monkeypatch.setattr(service, "_assistant_history_contents", lambda *a, **kw: [])
    monkeypatch.setattr(service, "_trace_history_contents", lambda *a, **kw: [])
    monkeypatch.setattr(service, "_extract_media", lambda *a, **kw: [])
    persist = Mock(side_effect=lambda username, project, content, media, **kw: {"content": content})
    monkeypatch.setattr(service, "add_assistant_message", persist)
    events = AsyncMock()
    result = await service._stream_assistant_reply_hermes("alice", "p1", "hello", events)
    assert result["content"] == "A completed reply"
    persist.assert_called_once()
    assert persist.call_args.args[:3] == ("alice", "p1", "A completed reply")
    assert any(call.args[0]["type"] == "done" for call in events.await_args_list)
    fake_cli_runtime.run_structured.assert_awaited_once()


@pytest.mark.asyncio
async def test_home_consumer_calls_real_cli_stream_and_persists_reply(fake_cli_runtime, monkeypatch):
    from novelvideo.api.routes import chat
    from novelvideo.chat.store import ChatScope
    monkeypatch.setattr(chat, "list_user_projects", lambda username: [])
    monkeypatch.setattr(chat.chat_store, "list_messages", lambda *a: [])
    persist = Mock(side_effect=lambda username, scope, role, content, **kw: {"role": role, "content": content})
    monkeypatch.setattr(chat.chat_store, "append_message", persist)
    monkeypatch.setattr(chat, "_chat_heartbeat", AsyncMock())
    socket = Mock()
    socket.send_json = AsyncMock()
    await chat._stream_home_turn(websocket=socket, username="alice", scope=ChatScope(kind="home"), text="hello", attachments=[], turn_id="turn-1")
    replies = [call.args[3] for call in persist.call_args_list if call.args[2] == "assistant"]
    assert replies == ["A completed reply"]
    assert any(call.args[0]["type"] == "chat.done" for call in socket.send_json.await_args_list)
    fake_cli_runtime.run_structured.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_kind", ["home", "project"])
async def test_failed_tool_result_reaches_websocket_as_failure(team, monkeypatch, scope_kind):
    from novelvideo.api.routes import chat
    from novelvideo.chat.backend_sdk import ChatBackendEvent
    from novelvideo.chat.store import ChatScope

    class FailedThread:
        id = "failed-thread"
        async def stream(self, prompt, *, current_project=None):
            yield ChatBackendEvent(type="tool_update", name="list_project", text="执行失败 list_project", raw={"status": "failed"})
            raise RuntimeError("Tool failed")

    monkeypatch.setattr(dispatcher, "get_chat_thread", AsyncMock(return_value=FailedThread()))
    monkeypatch.setattr(chat, "_chat_heartbeat", AsyncMock())
    monkeypatch.setattr(chat, "list_user_projects", lambda username: [])
    monkeypatch.setattr(chat.chat_store, "list_messages", lambda *a: [])
    monkeypatch.setattr(chat.chat_store, "append_message", Mock())
    monkeypatch.setattr(chat, "_project_context_for_scope", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_prompt_with_user_context", lambda username, project, prompt: prompt)
    monkeypatch.setattr(service, "_assistant_history_contents", lambda *a, **kw: [])
    monkeypatch.setattr(service, "_trace_history_contents", lambda *a, **kw: [])
    monkeypatch.setattr(service, "add_user_message", Mock())
    monkeypatch.setattr(service, "_acquire_chat_run_lock", lambda *a: "lock")
    monkeypatch.setattr(service, "_release_chat_run_lock", Mock())
    monkeypatch.setattr(service, "_chat_run_lock_heartbeat_loop", AsyncMock())
    monkeypatch.setattr(service, "_chat_backend", lambda: "codex")
    socket = Mock()
    socket.send_json = AsyncMock()
    kwargs = dict(websocket=socket, username="alice", scope=ChatScope(kind=scope_kind, id="p1" if scope_kind == "project" else None), text="hello", attachments=[], turn_id="turn-1")
    with pytest.raises(RuntimeError, match="Tool failed"):
        if scope_kind == "home":
            await chat._stream_home_turn(**kwargs)
        else:
            await chat._stream_project_turn(**kwargs, user={"id": "alice"})
    tools = [call.args[0] for call in socket.send_json.await_args_list if call.args[0]["type"] == "tool.result"]
    assert len(tools) == 1
    assert tools[0]["success"] is False
    assert "执行失败" in tools[0]["error"]
