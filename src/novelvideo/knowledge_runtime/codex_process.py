"""Timeout and process-tree supervision for Codex CLI executions."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import KnowledgeRuntimeError

DEFAULT_EXEC_TIMEOUT_SECONDS = 600.0
_CLEANUP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class CodexProcessResult:
    stdout: bytes
    stderr: bytes
    returncode: int
    output: str
    completed_from_final_message: bool


def parse_exec_timeout_seconds(raw_value: str | None) -> float:
    """Parse a positive integer timeout from configuration."""
    value = "" if raw_value is None else raw_value.strip()
    if not value:
        return DEFAULT_EXEC_TIMEOUT_SECONDS
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise KnowledgeRuntimeError(
            "Codex execution timeout must be a positive integer number of seconds.",
            code="CODEX_EXEC_CONFIG_INVALID",
        )
    return float(value)


async def terminate_process_tree(process: Any, *, grace_seconds: float = 5.0) -> None:
    """Terminate one process tree by exact PID, never by executable name."""
    if process.returncode is not None:
        return

    pid = int(process.pid)
    if _is_windows():
        try:
            taskkill = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            taskkill_code = int(
                await asyncio.wait_for(taskkill.wait(), timeout=grace_seconds)
            )
        except Exception:
            await _kill_exact_process(process, grace_seconds)
            return
        if taskkill_code == 0 and await _wait_for_exit(process, grace_seconds):
            return
        await _kill_exact_process(process, grace_seconds)
        return

    getpgid = getattr(os, "getpgid", None)
    killpg = getattr(os, "killpg", None)
    is_group_leader = False
    if callable(getpgid) and callable(killpg):
        try:
            is_group_leader = int(getpgid(pid)) == pid
        except ProcessLookupError:
            is_group_leader = process.returncode is not None

    if is_group_leader and callable(killpg):
        try:
            killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            if process.returncode is not None:
                return
        if await _wait_for_exit(process, grace_seconds):
            return
        try:
            killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            if process.returncode is not None:
                return
        if await _wait_for_exit(process, grace_seconds):
            return

    process.terminate()
    if await _wait_for_exit(process, grace_seconds):
        return
    await _kill_exact_process(process, grace_seconds)


async def _wait_for_exit(process: Any, timeout_seconds: float) -> bool:
    if process.returncode is not None:
        return True
    waiter = asyncio.create_task(process.wait())
    done, _ = await asyncio.wait({waiter}, timeout=timeout_seconds)
    if waiter not in done:
        waiter.cancel()
        waiter.add_done_callback(_consume_task_result)
        return process.returncode is not None
    with suppress(asyncio.CancelledError, Exception):
        waiter.result()
    return process.returncode is not None


async def _kill_exact_process(process: Any, timeout_seconds: float) -> None:
    if process.returncode is None:
        process.kill()
    await _wait_for_exit(process, timeout_seconds)

async def supervise_codex_process(
    process: Any,
    prompt: str,
    output_path: str | Path,
    timeout_seconds: float,
    stable_seconds: float = 5.0,
    poll_interval: float = 0.25,
    terminate: Callable[[Any], Awaitable[None]] = terminate_process_tree,
) -> CodexProcessResult:
    """Communicate with Codex while supervising its final-message file."""
    path = Path(output_path)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    stable_fingerprint: tuple[int, int] | None = None
    stable_since: float | None = None
    communication = asyncio.create_task(process.communicate(prompt.encode("utf-8")))

    try:
        while True:
            if communication.done():
                stdout, stderr = await communication
                return _result_after_normal_exit(process, path, stdout, stderr)

            now = loop.time()
            fingerprint = _nonempty_file_fingerprint(path)
            if fingerprint is None:
                stable_fingerprint = None
                stable_since = None
            elif fingerprint != stable_fingerprint:
                stable_fingerprint = fingerprint
                stable_since = now
            elif stable_since is not None and now - stable_since >= stable_seconds:
                output = _read_stable_output(path, fingerprint)
                if output is not None:
                    stdout, stderr = await _cleanup_running_execution(
                        process, communication, terminate
                    )
                    return CodexProcessResult(
                        stdout=stdout,
                        stderr=stderr,
                        returncode=await _returncode(process),
                        output=output,
                        completed_from_final_message=True,
                    )
                stable_fingerprint = None
                stable_since = None

            if now - started_at >= timeout_seconds:
                await _cleanup_running_execution(process, communication, terminate)
                raise KnowledgeRuntimeError(
                    "Codex execution timed out.", code="CODEX_EXEC_TIMEOUT"
                )

            remaining = timeout_seconds - (loop.time() - started_at)
            delay = max(0.0, min(poll_interval, remaining))
            try:
                await asyncio.wait_for(asyncio.shield(communication), timeout=delay)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError as cancelled:
        try:
            await _cleanup_running_execution(process, communication, terminate)
        except KnowledgeRuntimeError as cleanup_error:
            raise cancelled from cleanup_error
        raise


def _is_windows() -> bool:
    return sys.platform == "win32"


async def _terminate_running_process(
    process: Any, terminate: Callable[[Any], Awaitable[None]]
) -> None:
    if process.returncode is not None:
        return
    try:
        await asyncio.wait_for(
            terminate(process),
            timeout=_CLEANUP_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, OSError):
        pass
    if process.returncode is None:
        try:
            await _kill_exact_process(process, _CLEANUP_TIMEOUT_SECONDS)
        except OSError:
            pass
    if process.returncode is None:
        raise KnowledgeRuntimeError(
            "Codex process could not be stopped during cleanup.",
            code="CODEX_PROCESS_CLEANUP_FAILED",
        )


async def _cleanup_running_execution(
    process: Any,
    communication: asyncio.Task[tuple[bytes, bytes]],
    terminate: Callable[[Any], Awaitable[None]],
) -> tuple[bytes, bytes]:
    cleanup_error: KnowledgeRuntimeError | None = None
    try:
        await _terminate_running_process(process, terminate)
    except KnowledgeRuntimeError as exc:
        cleanup_error = exc
    output = await _drain_communication(communication)
    if cleanup_error is not None:
        raise cleanup_error
    return output


async def _drain_communication(
    communication: asyncio.Task[tuple[bytes, bytes]],
) -> tuple[bytes, bytes]:
    done, _ = await asyncio.wait(
        {communication}, timeout=_CLEANUP_TIMEOUT_SECONDS
    )
    if communication not in done:
        communication.cancel()
        done, _ = await asyncio.wait(
            {communication}, timeout=_CLEANUP_TIMEOUT_SECONDS
        )
    if communication not in done:
        communication.add_done_callback(_consume_task_result)
        return b"", b""
    try:
        return communication.result()
    except (asyncio.CancelledError, OSError):
        return b"", b""


async def _returncode(process: Any) -> int:
    if process.returncode is None:
        raise KnowledgeRuntimeError(
            "Codex process could not be stopped after producing final output.",
            code="CODEX_PROCESS_CLEANUP_FAILED",
        )
    return int(process.returncode)


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        task.result()


def _result_after_normal_exit(
    process: Any,
    output_path: Path,
    stdout: bytes,
    stderr: bytes,
) -> CodexProcessResult:
    if output_path.exists():
        output = output_path.read_text(encoding="utf-8", errors="replace")
    else:
        output = stdout.decode("utf-8", errors="replace")
    return CodexProcessResult(
        stdout=stdout,
        stderr=stderr,
        returncode=int(process.returncode),
        output=output,
        completed_from_final_message=False,
    )


def _nonempty_file_fingerprint(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size <= 0:
        return None
    return stat.st_size, stat.st_mtime_ns


def _read_stable_output(path: Path, fingerprint: tuple[int, int]) -> str | None:
    try:
        output = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if output and _nonempty_file_fingerprint(path) == fingerprint:
        return output
    return None
