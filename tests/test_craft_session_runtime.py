import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel

from novelvideo.knowledge_runtime import codex
from novelvideo.knowledge_runtime.codex_process import CodexProcessResult
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
from novelvideo.text_task_runtime.craft_session import author_runtime
from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
from novelvideo.text_task_runtime.runtime import CodexStructuredRuntime

SESSION = "12345678-1234-1234-1234-123456789abc"


class Result(BaseModel):
    text: str


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr("novelvideo.shared.runtime_env.edition", lambda: "community")
    route = AgentTaskRouteSnapshot(runtime="codex", model="gpt-5.4", reasoning_effort="high", task_role="script_creation", source="task")
    base = CodexStructuredRuntime(route)
    base._backend._cost_capture = Mock()
    base._backend._cost_capture.prepare.return_value = None
    calls = []

    async def create(argv, **kwargs):
        calls.append((argv, kwargs))
        schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
        assert schema["additionalProperties"] is False
        return object()

    monkeypatch.setattr(codex, "_create_codex_process", create)
    outcome = CodexProcessResult(json.dumps({"type": "thread.started", "thread_id": SESSION}).encode(), b"", 0, '{"text":"ok"}', False)
    supervise = AsyncMock(return_value=outcome)
    monkeypatch.setattr(codex, "supervise_codex_process", supervise)
    return base, calls, supervise


async def test_native_exact_session_and_frozen_route(native):
    base, calls, supervise = native
    persist = AsyncMock()
    runtime = author_runtime(base, on_session=persist)
    assert runtime.mode == "native" and runtime.snapshot is base.snapshot
    assert (await runtime.run_structured(prompt="draft", output_type=Result)).text == "ok"
    assert runtime.session_id == SESSION
    await runtime.run_structured(prompt="repair", output_type=Result)
    persist.assert_awaited_once_with(SESSION)
    first, second = calls[0][0], calls[1][0]
    assert "resume" not in first
    assert second[second.index("resume") + 1] == SESSION
    for argv in (first, second):
        assert "--ephemeral" not in argv and "--last" not in argv
        assert "--json" in argv and "--ignore-rules" in argv and "--ignore-user-config" in argv
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert argv[argv.index("--model") + 1] == "gpt-5.4"
        assert 'model_reasoning_effort="high"' in argv
        assert "plugins" in argv and "apps" in argv
    resume_args = second[second.index("resume"):]
    assert "--output-schema" in resume_args and "--output-last-message" in resume_args
    assert "--ephemeral" in base._backend.build_argv(cwd="/tmp", output_path="/tmp/out", schema_path=None)
    assert supervise.await_count == 2


async def test_failed_session_callback_is_retried_before_next_paid_call(native):
    base, calls, _ = native
    persist = AsyncMock(side_effect=[RuntimeError('database unavailable'),None])
    runtime = author_runtime(base,on_session=persist)
    with pytest.raises(RuntimeError):
        await runtime.run_structured(prompt='draft',output_type=Result)
    await runtime.run_structured(prompt='repair',output_type=Result)
    assert persist.await_count == 2
    assert 'resume' in calls[-1][0]


async def test_two_wrappers_serialize_and_discover_same_session(native,tmp_path):
    import asyncio
    base, calls, supervise = native
    original = supervise.return_value
    active, maximum = 0,0
    async def slow(*args):
        nonlocal active,maximum
        active += 1; maximum = max(maximum,active)
        await asyncio.sleep(.02)
        active -= 1
        return original
    supervise.side_effect = slow
    first,second = [author_runtime(base,session_dir=tmp_path/'author') for _ in range(2)]
    await asyncio.gather(first.run_structured(prompt='a',output_type=Result),second.run_structured(prompt='b',output_type=Result))
    assert maximum == 1
    assert 'resume' in calls[1][0]


async def test_team_reuses_private_home_and_isolation(native, monkeypatch):
    base, calls, _ = native
    monkeypatch.setattr("novelvideo.shared.runtime_env.edition", lambda: "team")
    roots = []

    def prepare(argv, root, *, runtime):
        roots.append(root)
        assert runtime == "codex"
        return argv, {"HOME": str(root / "home")}

    monkeypatch.setattr("novelvideo.team.execution.prepare_cli", prepare)
    runtime = author_runtime(base)
    await runtime.run_structured(prompt="draft", output_type=Result)
    await runtime.run_structured(prompt="repair", output_type=Result)
    assert roots[0] == roots[1]
    for argv, options in calls:
        assert options["cwd"] == str(roots[0])
        assert options["env"] == {"HOME": str(roots[0] / "home")}
        for disabled in ("shell_tool", "unified_exec", "browser_use", "computer_use"):
            assert disabled in argv


async def test_session_mismatch_and_missing_fail_closed(native):
    base, _, supervise = native
    runtime = author_runtime(base, session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime.run_structured(prompt="repair", output_type=Result)
    assert error.value.code == "CODEX_AUTHOR_SESSION_MISMATCH"
    supervise.return_value = CodexProcessResult(b"", b"session not found", 1, "", False)
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime.run_structured(prompt="repair", output_type=Result)
    assert error.value.code == "CODEX_AUTHOR_SESSION_UNAVAILABLE"
    with pytest.raises(KnowledgeRuntimeError):
        author_runtime(base, session_id="--last")


async def test_replay_preserves_delegate_and_method(native):
    from novelvideo.agent_teams.adapters import MethodRuntime
    base, _, supervise = native
    method = SimpleNamespace(resolved_method=SimpleNamespace(prompt="method guidance", director_preferences=None), resource_snapshots=[])
    runtime = author_runtime(MethodRuntime(base, method))
    await runtime.run_structured(prompt="draft", output_type=Result)
    assert runtime.method is method
    assert "method guidance" in supervise.call_args.args[1]
    fake = SimpleNamespace(snapshot=base.snapshot, run_structured=AsyncMock(return_value=Result(text="replay")))
    runtime = author_runtime(fake, session_id=SESSION)
    assert runtime.mode == "replay" and runtime.session_id is None
    assert (await runtime.run_structured(prompt="history", output_type=Result)).text == "replay"
    fake.run_structured.assert_awaited_once_with(prompt="history", output_type=Result)


async def test_callback_failure_prevents_return(native):
    base, _, _ = native
    runtime = author_runtime(base, on_session=AsyncMock(side_effect=RuntimeError("persistence failed")))
    with pytest.raises(RuntimeError, match="persistence failed"):
        await runtime.run_structured(prompt="draft", output_type=Result)


async def test_replay_accepts_minimal_runtime_without_snapshot():
    fake = SimpleNamespace(run_structured=AsyncMock(return_value=Result(text="minimal")))
    runtime = author_runtime(fake)
    assert runtime.snapshot is None
    assert runtime.mode == "replay" and runtime.session_id is None
    assert (await runtime.run_structured(prompt="history", output_type=Result)).text == "minimal"
    fake.run_structured.assert_awaited_once_with(prompt="history", output_type=Result)


async def test_team_recovers_same_private_home_in_new_wrapper(native, monkeypatch, tmp_path):
    base, calls, _ = native
    monkeypatch.setattr("novelvideo.shared.runtime_env.edition", lambda: "team")
    session_dir = tmp_path / "craft-sessions" / "server-generated-logical-id"
    roots = []

    def prepare(argv, root, *, runtime):
        roots.append(root)
        home = root / "home" / ".codex"
        home.mkdir(parents=True, exist_ok=True)
        session = home / "saved-session"
        if "resume" in argv:
            assert session.read_text() == SESSION
        else:
            session.write_text(SESSION)
        return argv, {"HOME": str(root / "home"), "CODEX_HOME": str(home)}

    monkeypatch.setattr("novelvideo.team.execution.prepare_cli", prepare)
    persist = AsyncMock()
    first = author_runtime(base, on_session=persist, session_dir=session_dir)
    await first.run_structured(prompt="draft", output_type=Result)
    saved_id = first.session_id
    del first
    second = author_runtime(base, session_id=saved_id, session_dir=session_dir)
    await second.run_structured(prompt="repair after worker restart", output_type=Result)
    assert roots == [session_dir.resolve(), session_dir.resolve()]
    assert session_dir.stat().st_mode & 0o777 == 0o700
    assert calls[0][1]["env"] == calls[1][1]["env"]
    assert calls[1][0][calls[1][0].index("resume") + 1] == saved_id
    assert "--last" not in calls[1][0]
    persist.assert_awaited_once_with(saved_id)
