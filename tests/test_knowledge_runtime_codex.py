from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from novelvideo.knowledge_runtime.codex import (
    CodexCliStructuredBackend,
    CodexCliStatus,
    _run_status_command,
    build_codex_exec_argv,
    normalize_codex_process_argv,
    normalize_codex_output_schema,
    parse_codex_login_status,
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
    assert status == CodexCliStatus(installed=True, authenticated=False, version="", message="Not logged in")


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
async def test_cancelled_run_terminates_process(monkeypatch, tmp_path) -> None:
    started = asyncio.Event()

    class HangingProcess:
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
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    backend = CodexCliStructuredBackend()
    task = asyncio.create_task(backend._run_once("prompt", schema=None))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated is True
