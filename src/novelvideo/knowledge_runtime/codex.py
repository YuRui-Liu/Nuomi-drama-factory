"""Structured Cognee text output through the local Codex CLI."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .settings import KnowledgeRuntimeError


@dataclass(frozen=True)
class CodexCliStatus:
    installed: bool
    authenticated: bool
    version: str
    message: str


class _ThreadedProcess:
    """Async-compatible wrapper for event loops without subprocess support."""

    def __init__(self, argv: list[str], *, stdin: int) -> None:
        self._process = subprocess.Popen(
            argv,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
        )

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def communicate(self, input_data: bytes | None = None) -> tuple[bytes, bytes]:
        stdout, stderr = await asyncio.to_thread(self._process.communicate, input_data)
        return stdout or b"", stderr or b""

    async def wait(self) -> int:
        return await asyncio.to_thread(self._process.wait)

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()


async def _create_codex_process(argv: list[str], *, stdin: int) -> Any:
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
    except NotImplementedError:
        return await asyncio.to_thread(_ThreadedProcess, argv, stdin=stdin)


def parse_codex_login_status(
    returncode: int, stdout: str, stderr: str, *, version: str = ""
) -> CodexCliStatus:
    message = (stdout or stderr).strip()
    authenticated = returncode == 0 and "not logged in" not in message.lower()
    return CodexCliStatus(
        installed=True,
        authenticated=authenticated,
        version=version.strip(),
        message=message,
    )


def normalize_codex_process_argv(
    argv: list[str],
    *,
    platform: str | None = None,
    comspec: str | None = None,
    powershell: str | None = None,
) -> list[str]:
    """Make Windows batch launchers executable without enabling shell strings."""

    if not argv:
        raise ValueError("Codex command cannot be empty")
    effective_platform = platform or os.name
    suffix = Path(argv[0]).suffix.lower()
    if effective_platform == "nt" and suffix in {".cmd", ".bat"}:
        command_shell = comspec or os.getenv("ComSpec", "").strip() or "cmd.exe"
        return [command_shell, "/d", "/s", "/c", *argv]
    if effective_platform == "nt" and suffix == ".ps1":
        powershell_exe = powershell or shutil.which("powershell.exe") or "powershell.exe"
        return [
            powershell_exe,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            *argv,
        ]
    return list(argv)


async def _run_status_command(*argv: str) -> tuple[int, str, str]:
    process_argv = normalize_codex_process_argv(list(argv))
    process = await _create_codex_process(
        process_argv,
        stdin=asyncio.subprocess.DEVNULL,
    )
    stdout, stderr = await process.communicate()
    return (
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def get_codex_cli_status(codex_bin: str | None = None) -> CodexCliStatus:
    executable = codex_bin or os.getenv("CODEX_BIN", "").strip() or shutil.which("codex")
    if not executable:
        return CodexCliStatus(False, False, "", "Codex CLI 未安装")
    try:
        version_code, version_out, version_err = await _run_status_command(
            executable, "--version"
        )
        if version_code != 0:
            return CodexCliStatus(
                True,
                False,
                "",
                (version_err or version_out).strip() or "Codex CLI 无法启动",
            )
        code, stdout, stderr = await _run_status_command(
            executable, "login", "status"
        )
    except OSError as exc:
        launcher = normalize_codex_process_argv([executable, "--version"])[0]
        return CodexCliStatus(
            True,
            False,
            "",
            f"Codex CLI 无法启动 ({Path(launcher).name}): {exc}",
        )
    return parse_codex_login_status(
        code,
        stdout,
        stderr,
        version=(version_out or version_err).strip(),
    )


def build_codex_exec_argv(
    *,
    codex_bin: str,
    cwd: str,
    output_path: str,
    schema_path: str | None,
    model: str,
) -> list[str]:
    argv = [
        codex_bin,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--color",
        "never",
        "-C",
        cwd,
        "--output-last-message",
        output_path,
    ]
    if model:
        argv.extend(["--model", model])
    if schema_path:
        argv.extend(["--output-schema", schema_path])
    argv.append("-")
    return argv


def normalize_codex_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert Pydantic JSON Schema to the strict subset accepted by Codex."""
    normalized = copy.deepcopy(schema)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            properties = value.get("properties")
            if isinstance(properties, dict):
                value["additionalProperties"] = False
                value["required"] = list(properties)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(normalized)
    return normalized


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _validation_summary(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:600]


def _build_prompt(
    system_prompt: str,
    text_input: str,
    schema: dict[str, Any] | None,
    repair_error: str,
) -> str:
    parts = [
        "你正在执行 DramaClaw 知识抽取任务。不得调用工具，不得读写文件，只返回最终答案。",
        f"系统要求：\n{system_prompt}",
        f"输入文本：\n{text_input}",
    ]
    if schema is not None:
        parts.append(
            "输出必须是符合以下 JSON Schema 的单个 JSON 对象，不要代码围栏或解释：\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        )
    if repair_error:
        parts.append(f"上次输出校验失败：{repair_error}。请修复后重新输出。")
    return "\n\n".join(parts)


class CodexCliStructuredBackend:
    def __init__(self, *, codex_bin: str | None = None, model: str | None = None):
        self.codex_bin = (
            codex_bin
            or os.getenv("CODEX_BIN", "").strip()
            or shutil.which("codex")
            or "codex"
        )
        self.model = model or os.getenv("CODEX_MODEL", "").strip() or "gpt-5.4"

    async def acreate_structured_output(
        self,
        text_input: str,
        system_prompt: str,
        response_model: type[Any],
        **_kwargs: Any,
    ) -> Any:
        schema = None if response_model is str else response_model.model_json_schema()
        repair_error = ""
        for attempt in range(3):
            raw = await self._run_once(
                _build_prompt(system_prompt, text_input, schema, repair_error),
                schema=schema,
            )
            if response_model is str:
                return raw.strip()
            try:
                return response_model.model_validate_json(_strip_json_fence(raw))
            except (ValueError, ValidationError) as exc:
                repair_error = _validation_summary(exc)
                if attempt == 2:
                    raise KnowledgeRuntimeError(
                        "Codex 未能返回符合 Schema 的结构化结果。",
                        code="CODEX_STRUCTURED_OUTPUT_INVALID",
                    ) from exc
        raise AssertionError("unreachable")

    async def _run_once(
        self, prompt: str, *, schema: dict[str, Any] | None
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="dramaclaw-knowledge-") as raw_dir:
            temp_dir = Path(raw_dir)
            output_path = temp_dir / "result.txt"
            schema_path: Path | None = None
            if schema is not None:
                schema_path = temp_dir / "schema.json"
                schema_path.write_text(
                    json.dumps(
                        normalize_codex_output_schema(schema),
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            argv = build_codex_exec_argv(
                codex_bin=self.codex_bin,
                cwd=str(temp_dir),
                output_path=str(output_path),
                schema_path=str(schema_path) if schema_path else None,
                model=self.model,
            )
            process_argv = normalize_codex_process_argv(argv)
            try:
                process = await _create_codex_process(
                    process_argv,
                    stdin=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise KnowledgeRuntimeError(
                    f"Codex CLI 无法启动: {exc}", code="CODEX_NOT_INSTALLED"
                ) from exc
            try:
                stdout, stderr = await process.communicate(prompt.encode("utf-8"))
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                raise
            if process.returncode != 0:
                message = stderr.decode("utf-8", errors="replace").strip()
                code = (
                    "CODEX_NOT_AUTHENTICATED"
                    if "not logged in" in message.lower()
                    else "CODEX_EXEC_FAILED"
                )
                raise KnowledgeRuntimeError(
                    message[:600] or "Codex 执行失败。", code=code
                )
            if output_path.exists():
                return output_path.read_text(encoding="utf-8")
            return stdout.decode("utf-8", errors="replace")
