"""Structured Cognee text output through the local Codex CLI."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from .codex_process import (
    parse_exec_timeout_seconds,
    supervise_codex_process,
    terminate_process_tree,
)
from .settings import KnowledgeRuntimeError


MIN_CODEX_VERSION = (0, 100, 0)
MIN_CODEX_VERSION_TEXT = "0.100.0"
CODEX_STATUS_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class CodexCliStatus:
    installed: bool
    compatible: bool
    authenticated: bool
    ready: bool
    path: str
    version: str
    message: str
    state: str


def build_codex_process_env(*, environ: Mapping[str, str] | None = None, proxies: Mapping[str, str] | None = None) -> dict[str, str]:
    """Preserve explicit proxy variables and fill missing ones from the OS."""
    env = dict(os.environ if environ is None else environ)
    normalized_keys = {key.lower() for key in env}
    system_proxies = dict(urllib.request.getproxies() if proxies is None else proxies)
    for scheme, variable in (("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY")):
        proxy = str(system_proxies.get(scheme) or "").strip()
        if variable.lower() not in normalized_keys and proxy:
            env[variable] = proxy
            normalized_keys.add(variable.lower())
    return env


def _process_group_kwargs(*, platform: str | None = None) -> dict[str, Any]:
    effective_platform = platform or os.name
    if effective_platform == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}

class _ThreadedProcess:
    """Async-compatible wrapper for event loops without subprocess support."""

    def __init__(self, argv: list[str], *, stdin: int, env: Mapping[str, str]) -> None:
        self._process = subprocess.Popen(
            argv,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            **_process_group_kwargs(),
        )

    @property
    def pid(self) -> int:
        return int(self._process.pid)

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
    env = build_codex_process_env()
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **_process_group_kwargs(),
        )
    except NotImplementedError:
        return await asyncio.to_thread(_ThreadedProcess, argv, stdin=stdin, env=env)


def parse_codex_version(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _codex_status(
    *,
    path: str,
    compatible: bool = False,
    authenticated: bool = False,
    version: str = "",
    message: str,
    state: str | None = None,
) -> CodexCliStatus:
    installed = bool(path)
    resolved_state = state or ("not_installed" if not installed else "ready" if compatible and authenticated else "not_authenticated" if compatible else "exec_failed")
    return CodexCliStatus(
        installed=installed,
        compatible=compatible,
        authenticated=authenticated,
        ready=resolved_state == "ready",
        path=path,
        version=version,
        message=message,
        state=resolved_state,
    )


def parse_codex_login_status(
    returncode: int,
    stdout: str,
    stderr: str,
    *,
    path: str = "codex",
    version: str = "",
) -> CodexCliStatus:
    message = (stdout or stderr).strip()
    authenticated = returncode == 0 and "not logged in" not in message.lower()
    return _codex_status(
        path=path,
        compatible=True,
        authenticated=authenticated,
        version=version.strip(),
        message=message or ("Codex CLI 可用" if authenticated else "Codex 未登录"),
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


async def _run_status_command(
    *argv: str,
    timeout_seconds: float = CODEX_STATUS_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    process_argv = normalize_codex_process_argv(list(argv))
    process = await _create_codex_process(
        process_argv,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        await terminate_process_tree(process)
        raise
    return (
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def get_codex_cli_status(codex_bin: str | None = None) -> CodexCliStatus:
    executable = (
        (codex_bin or "").strip()
        or os.getenv("CODEX_BIN", "").strip()
        or shutil.which("codex")
        or ""
    )
    if not executable:
        return _codex_status(path="", message="Codex CLI 未安装")

    try:
        version_code, version_out, version_err = await _run_status_command(
            executable,
            "--version",
            timeout_seconds=CODEX_STATUS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _codex_status(path=executable, message="Codex CLI 版本检查超时")
    except OSError as exc:
        return _codex_status(path=executable, message=f"Codex CLI 无法启动: {exc}")

    version = (version_out or version_err).strip()
    if version_code != 0:
        return _codex_status(
            path=executable,
            version=version,
            message=(version_err or version_out).strip() or "Codex CLI 无法启动",
        )

    parsed_version = parse_codex_version(version)
    if parsed_version is None:
        return _codex_status(
            path=executable,
            version=version,
            message="无法解析 Codex CLI 版本",
        )
    if parsed_version < MIN_CODEX_VERSION:
        return _codex_status(
            path=executable,
            version=version,
            message=f"Codex CLI 版本过低，需要 >= {MIN_CODEX_VERSION_TEXT}",
            state="version_unsupported",
        )

    try:
        code, stdout, stderr = await _run_status_command(
            executable,
            "login",
            "status",
            timeout_seconds=CODEX_STATUS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _codex_status(
            path=executable,
            compatible=True,
            version=version,
            message="Codex 登录状态检查超时",
            state="exec_failed",
        )
    except OSError as exc:
        return _codex_status(
            path=executable,
            compatible=True,
            version=version,
            message=f"Codex CLI 无法启动: {exc}",
            state="exec_failed",
        )

    return parse_codex_login_status(
        code,
        stdout,
        stderr,
        path=executable,
        version=version,
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
        "--disable",
        "plugins",
        "--disable",
        "remote_plugin",
        "--disable",
        "apps",
        "--disable",
        "skill_search",
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
    unsupported_constraints = {
        "default",
        "examples",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
    }

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for keyword in unsupported_constraints:
                value.pop(keyword, None)
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


def _codex_error_summary(
    stderr: bytes, stdout: bytes = b"", *, limit: int = 1200
) -> str:
    """Keep the actionable tail of each diagnostic stream independently."""

    def stream_tail(payload: bytes) -> str:
        message = payload.decode("utf-8", errors="replace").strip()
        if len(message) <= limit:
            return message
        return "...\n" + message[-limit:]

    return "\n".join(
        part for part in (stream_tail(stderr), stream_tail(stdout)) if part
    )


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
    def build_argv(
        self,
        *,
        cwd: str,
        output_path: str,
        schema_path: str | None,
    ) -> list[str]:
        return build_codex_exec_argv(
            codex_bin=self.codex_bin,
            cwd=cwd,
            output_path=output_path,
            schema_path=schema_path,
            model=self.model,
        )

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
                        (
                            "Codex 未能返回符合 Schema 的结构化结果："
                            f"{repair_error}"
                        ),
                        code="CODEX_STRUCTURED_OUTPUT_INVALID",
                    ) from exc
        raise AssertionError("unreachable")

    async def _run_once(
        self, prompt: str, *, schema: dict[str, Any] | None
    ) -> str:
        timeout_seconds = parse_exec_timeout_seconds(
            os.getenv("CODEX_EXEC_TIMEOUT_SECONDS")
        )
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
            argv = self.build_argv(
                cwd=str(temp_dir),
                output_path=str(output_path),
                schema_path=str(schema_path) if schema_path else None,
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
            outcome = await supervise_codex_process(
                process,
                prompt,
                output_path,
                timeout_seconds,
            )
            if outcome.completed_from_final_message:
                return outcome.output
            if outcome.returncode != 0:
                message = _codex_error_summary(outcome.stderr, outcome.stdout)
                lowered = message.lower()
                if "not logged in" in lowered:
                    code = "CODEX_NOT_AUTHENTICATED"
                elif "invalid_json_schema" in lowered:
                    code = "CODEX_SCHEMA_INVALID"
                else:
                    code = "CODEX_EXEC_FAILED"
                raise KnowledgeRuntimeError(message or "Codex 执行失败。", code=code)
            return outcome.output
