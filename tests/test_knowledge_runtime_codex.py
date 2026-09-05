from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from novelvideo.episode_graph.models import EpisodeGraphExtraction
from novelvideo.knowledge_runtime.codex import (
    CodexCliStructuredBackend,
    MIN_CODEX_VERSION,
    get_codex_cli_status,
    parse_codex_version,
    _process_group_kwargs,
    _run_status_command,
    build_codex_exec_argv,
    normalize_codex_process_argv,
    normalize_codex_output_schema,
    parse_codex_login_status,
    build_codex_process_env,
    _create_codex_process,
)
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError


class Extracted(BaseModel):
    name: str


def test_normalize_codex_output_schema_makes_nested_objects_strict() -> None:
    schema = {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string", "default": ""},
                    },
                    "required": ["id"],
                },
            }
        },
        "required": ["nodes"],
    }

    normalized = normalize_codex_output_schema(schema)

    assert normalized["additionalProperties"] is False
    node_schema = normalized["properties"]["nodes"]["items"]
    assert node_schema["additionalProperties"] is False
    assert node_schema["required"] == ["id", "name"]
    assert "default" not in node_schema["properties"]["name"]


def test_episode_graph_schema_closes_attribute_objects_for_codex() -> None:
    normalized = normalize_codex_output_schema(
        EpisodeGraphExtraction.model_json_schema()
    )

    attribute_schema = normalized["$defs"]["GraphAttributes"]
    assert attribute_schema["additionalProperties"] is False
    assert "description" in attribute_schema["properties"]
    serialized = str(normalized)
    for unsupported in (
        "uniqueItems",
        "minLength",
        "minItems",
        "exclusiveMinimum",
    ):
        assert unsupported not in serialized


def test_build_codex_exec_argv_is_ephemeral_read_only() -> None:
    argv = build_codex_exec_argv(
        codex_bin="codex",
        cwd="C:/temp/knowledge",
        output_path="C:/temp/knowledge/result.json",
        schema_path="C:/temp/knowledge/schema.json",
        model="gpt-5.4",
    )
    assert argv[:2] == ["codex", "exec"]
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--ignore-rules" in argv
    for feature in ("plugins", "remote_plugin", "apps", "skill_search"):
        assert ["--disable", feature] == argv[
            argv.index(feature) - 1 : argv.index(feature) + 1
        ]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--output-schema") + 1].endswith("schema.json")
    assert argv[-1] == "-"


def test_windows_cmd_launcher_runs_through_comspec() -> None:
    argv = normalize_codex_process_argv(
        ["D:/tools/codex.CMD", "login", "status"],
        platform="nt",
        comspec="C:/Windows/System32/cmd.exe",
    )

    assert argv == [
        "C:/Windows/System32/cmd.exe",
        "/d",
        "/s",
        "/c",
        "D:/tools/codex.CMD",
        "login",
        "status",
    ]


def test_native_executable_does_not_use_command_shell() -> None:
    argv = ["D:/tools/codex.exe", "--version"]
    assert normalize_codex_process_argv(argv, platform="nt") == argv

def test_codex_subprocesses_use_isolated_process_groups() -> None:
    assert _process_group_kwargs(platform="posix") == {"start_new_session": True}
    windows = _process_group_kwargs(platform="nt")
    assert windows["creationflags"] > 0


def test_process_env_inherits_system_proxies() -> None:
    env = build_codex_process_env(
        environ={"KEEP": "yes"},
        proxies={"http": "http://system-http", "https": "http://system-https"},
    )

    assert env == {
        "KEEP": "yes",
        "HTTP_PROXY": "http://system-http",
        "HTTPS_PROXY": "http://system-https",
    }


def test_process_env_preserves_explicit_proxy_case_insensitively() -> None:
    env = build_codex_process_env(
        environ={
            "http_proxy": "http://explicit-http",
            "HTTPS_PROXY": "http://explicit-https",
        },
        proxies={"http": "http://system-http", "https": "http://system-https"},
    )

    assert env == {
        "http_proxy": "http://explicit-http",
        "HTTPS_PROXY": "http://explicit-https",
    }


@pytest.mark.asyncio
async def test_create_codex_process_passes_shared_proxy_env_to_async_launcher(
    monkeypatch,
) -> None:
    expected_env = {"HTTPS_PROXY": "http://system-proxy"}
    create = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "novelvideo.knowledge_runtime.codex.build_codex_process_env",
        lambda: expected_env,
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    await _create_codex_process(["codex", "--version"], stdin=asyncio.subprocess.DEVNULL)

    assert create.await_args.kwargs["env"] is expected_env


@pytest.mark.asyncio
async def test_create_codex_process_passes_same_env_to_threaded_fallback(
    monkeypatch,
) -> None:
    expected_env = {"HTTPS_PROXY": "http://system-proxy"}
    observed: dict[str, object] = {}
    sentinel = object()

    def threaded(argv, *, stdin, env):
        observed.update(argv=argv, stdin=stdin, env=env)
        return sentinel

    monkeypatch.setattr(
        "novelvideo.knowledge_runtime.codex.build_codex_process_env",
        lambda: expected_env,
    )
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=NotImplementedError),
    )
    monkeypatch.setattr("novelvideo.knowledge_runtime.codex._ThreadedProcess", threaded)

    result = await _create_codex_process(
        ["codex", "--version"], stdin=asyncio.subprocess.DEVNULL
    )

    assert result is sentinel
    assert observed == {
        "argv": ["codex", "--version"],
        "stdin": asyncio.subprocess.DEVNULL,
        "env": expected_env,
    }

def test_windows_powershell_launcher_runs_through_powershell_exe() -> None:
    argv = normalize_codex_process_argv(
        ["D:/tools/codex.ps1", "login", "status"],
        platform="nt",
        powershell="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    )

    assert argv == [
        "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "D:/tools/codex.ps1",
        "login",
        "status",
    ]


@pytest.mark.asyncio
async def test_structured_backend_validates_json() -> None:
    backend = CodexCliStructuredBackend()
    backend._run_once = AsyncMock(return_value='{"name":"林默"}')
    assert await backend.acreate_structured_output("正文", "提取人物", Extracted) == Extracted(
        name="林默"
    )


@pytest.mark.asyncio
async def test_structured_backend_retries_invalid_output_twice() -> None:
    backend = CodexCliStructuredBackend()
    backend._run_once = AsyncMock(
        side_effect=["not json", '{"wrong":1}', '{"name":"林默"}']
    )
    result = await backend.acreate_structured_output("正文", "提取人物", Extracted)
    assert result.name == "林默"
    assert backend._run_once.await_count == 3


@pytest.mark.asyncio
async def test_structured_backend_does_not_retry_codex_exec_failure() -> None:
    backend = CodexCliStructuredBackend()
    backend._run_once = AsyncMock(
        side_effect=KnowledgeRuntimeError(
            "transport failed",
            code="CODEX_EXEC_FAILED",
        )
    )

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await backend.acreate_structured_output("正文", "提取人物", Extracted)

    assert exc_info.value.code == "CODEX_EXEC_FAILED"
    assert backend._run_once.await_count == 1


@pytest.mark.asyncio
async def test_structured_backend_fails_after_repair_limit() -> None:
    backend = CodexCliStructuredBackend()
    backend._run_once = AsyncMock(return_value="not json")
    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await backend.acreate_structured_output("正文", "提取人物", Extracted)
    assert exc_info.value.code == "CODEX_STRUCTURED_OUTPUT_INVALID"


@pytest.mark.asyncio
async def test_structured_backend_returns_plain_string() -> None:
    backend = CodexCliStructuredBackend()
    backend._run_once = AsyncMock(return_value="  摘要内容  ")
    assert await backend.acreate_structured_output("正文", "摘要", str) == "摘要内容"


def test_parse_codex_login_status() -> None:
    assert parse_codex_login_status(0, "Logged in using ChatGPT", "").authenticated is True
    status = parse_codex_login_status(1, "", "Not logged in")
    assert status.authenticated is False
    assert status.ready is False
    assert status.message == "Not logged in"


def test_parse_codex_version_and_minimum() -> None:
    assert parse_codex_version("codex-cli 0.146.0") == (0, 146, 0)
    assert MIN_CODEX_VERSION == (0, 100, 0)
    assert parse_codex_version("unknown") is None


@pytest.mark.asyncio
async def test_status_prefers_codex_bin_environment(monkeypatch) -> None:
    commands: list[tuple[str, ...]] = []

    async def run(*argv: str, timeout_seconds: float = 10.0):
        assert timeout_seconds == 10.0
        commands.append(argv)
        if argv[-1] == "--version":
            return 0, "codex-cli 0.146.0", ""
        return 0, "Logged in using ChatGPT", ""

    monkeypatch.setenv("CODEX_BIN", "E:/npm-global/codex.cmd")
    monkeypatch.setattr("novelvideo.knowledge_runtime.codex._run_status_command", run)

    status = await get_codex_cli_status()

    assert status.installed is True
    assert status.compatible is True
    assert status.authenticated is True
    assert status.ready is True
    assert status.path == "E:/npm-global/codex.cmd"
    assert commands == [
        ("E:/npm-global/codex.cmd", "--version"),
        ("E:/npm-global/codex.cmd", "login", "status"),
    ]


@pytest.mark.asyncio
async def test_status_rejects_old_version_without_login_probe(monkeypatch) -> None:
    run = AsyncMock(return_value=(0, "codex-cli 0.99.0", ""))
    monkeypatch.setattr("novelvideo.knowledge_runtime.codex._run_status_command", run)

    status = await get_codex_cli_status("E:/tools/codex.cmd")

    assert status.installed is True
    assert status.compatible is False
    assert status.authenticated is False
    assert status.ready is False
    assert status.state == "version_unsupported"
    assert "0.100.0" in status.message
    run.assert_awaited_once_with(
        "E:/tools/codex.cmd",
        "--version",
        timeout_seconds=10.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_message"),
    [
        (asyncio.TimeoutError(), "版本检查超时"),
        (OSError("cannot start"), "无法启动"),
        ((1, "", "launcher failed"), "launcher failed"),
        ((0, "unexpected output", ""), "无法解析"),
    ],
)
async def test_version_probe_failures_are_exec_failures_not_old_versions(
    monkeypatch,
    outcome,
    expected_message: str,
) -> None:
    run = AsyncMock()
    if isinstance(outcome, BaseException):
        run.side_effect = outcome
    else:
        run.return_value = outcome
    monkeypatch.setattr("novelvideo.knowledge_runtime.codex._run_status_command", run)

    status = await get_codex_cli_status("E:/tools/codex.cmd")

    assert status.installed is True
    assert status.compatible is False
    assert status.authenticated is False
    assert status.ready is False
    assert status.state == "exec_failed"
    assert expected_message in status.message


@pytest.mark.asyncio
async def test_status_reports_login_timeout(monkeypatch) -> None:
    run = AsyncMock(
        side_effect=[
            (0, "codex-cli 0.146.0", ""),
            asyncio.TimeoutError(),
        ]
    )
    monkeypatch.setattr("novelvideo.knowledge_runtime.codex._run_status_command", run)

    status = await get_codex_cli_status("E:/tools/codex.cmd")

    assert status.compatible is True
    assert status.authenticated is False
    assert status.ready is False
    assert "登录状态检查超时" in status.message

@pytest.mark.asyncio
async def test_status_command_falls_back_when_event_loop_rejects_subprocess(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=NotImplementedError),
    )

    returncode, stdout, stderr = await _run_status_command(
        sys.executable,
        "-c",
        "print('threaded-subprocess-ok')",
    )

    assert returncode == 0
    assert stdout.strip() == "threaded-subprocess-ok"
    assert stderr == ""




@pytest.mark.asyncio
async def test_run_once_uses_shared_supervisor(monkeypatch) -> None:
    from novelvideo.knowledge_runtime import codex as codex_runtime
    from novelvideo.knowledge_runtime.codex_process import CodexProcessResult

    class CreatedProcess:
        pid = 2468
        returncode = None

        async def communicate(self, _stdin):
            raise AssertionError("shared supervisor must own communication")

    created = CreatedProcess()

    async def create_process(*_args, **_kwargs):
        return created

    observed: dict[str, object] = {}

    async def supervise(process, prompt, output_path, timeout_seconds):
        observed.update(
            process=process,
            prompt=prompt,
            output_path=output_path,
            timeout_seconds=timeout_seconds,
        )
        return CodexProcessResult(
            stdout=b"",
            stderr=b"",
            returncode=0,
            output="监督器结果",
            completed_from_final_message=False,
        )

    monkeypatch.setenv("CODEX_EXEC_TIMEOUT_SECONDS", "12")
    monkeypatch.setattr(codex_runtime, "_create_codex_process", create_process)
    monkeypatch.setattr(
        codex_runtime,
        "supervise_codex_process",
        supervise,
        raising=False,
    )
    backend = CodexCliStructuredBackend()

    result = await backend._run_once("中文 prompt", schema=None)

    assert result == "监督器结果"
    assert observed["process"] is created
    assert observed["prompt"] == "中文 prompt"
    assert observed["timeout_seconds"] == 12.0


@pytest.mark.asyncio
async def test_run_once_rejects_invalid_timeout_before_start(monkeypatch) -> None:
    from novelvideo.knowledge_runtime import codex as codex_runtime

    create_process = AsyncMock()
    monkeypatch.setenv("CODEX_EXEC_TIMEOUT_SECONDS", "0")
    monkeypatch.setattr(codex_runtime, "_create_codex_process", create_process)
    backend = CodexCliStructuredBackend()

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await backend._run_once("prompt", schema=None)

    assert exc_info.value.code == "CODEX_EXEC_CONFIG_INVALID"
    create_process.assert_not_awaited()

@pytest.mark.asyncio
async def test_cancelled_run_terminates_process(monkeypatch, tmp_path) -> None:
    started = asyncio.Event()

    class HangingProcess:
        pid = 4321
        returncode = None

        def __init__(self) -> None:
            self.terminated = False

        async def communicate(self, _stdin):
            started.set()
            await asyncio.Future()

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 1

        def kill(self) -> None:
            self.returncode = 1

        async def wait(self) -> int:
            return int(self.returncode or 0)

    process = HangingProcess()
    create_calls = 0

    class TaskKill:
        async def wait(self) -> int:
            process.terminate()
            return 0

    async def create_subprocess(*_args, **_kwargs):
        nonlocal create_calls
        create_calls += 1
        return process if create_calls == 1 else TaskKill()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    backend = CodexCliStructuredBackend()
    task = asyncio.create_task(backend._run_once("prompt", schema=None))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated is True


@pytest.mark.asyncio
async def test_failed_run_reports_stderr_tail(monkeypatch) -> None:
    class FailedProcess:
        returncode = 1

        async def communicate(self, _stdin):
            noisy_prefix = ("startup warning\n" * 100).encode()
            return b"", noisy_prefix + b"fatal: transport unavailable\n"

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FailedProcess()),
    )
    backend = CodexCliStructuredBackend()

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await backend._run_once("prompt", schema=None)

    assert "fatal: transport unavailable" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "expected_code"),
    [
        (b"not logged in\n", "CODEX_NOT_AUTHENTICATED"),
        (b"invalid_json_schema\n", "CODEX_SCHEMA_INVALID"),
    ],
)
async def test_failed_run_preserves_stderr_classification_with_long_stdout(
    monkeypatch, stderr: bytes, expected_code: str
) -> None:
    class FailedProcess:
        returncode = 1

        async def communicate(self, _stdin):
            return b"x" * 5000, stderr

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=FailedProcess()),
    )
    backend = CodexCliStructuredBackend()

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await backend._run_once("prompt", schema=None)

    assert exc_info.value.code == expected_code
