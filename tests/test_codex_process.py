from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

import pytest

from novelvideo.knowledge_runtime import codex_process
from novelvideo.knowledge_runtime.codex_process import (
    parse_exec_timeout_seconds,
    supervise_codex_process,
    terminate_process_tree,
)
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        block: bool = False,
        pid: int = 4321,
    ) -> None:
        self.pid = pid
        self.stdout_bytes = stdout
        self.stderr_bytes = stderr
        self.returncode: int | None = None
        self._final_returncode = returncode
        self._block = block
        self._finished = asyncio.Event()
        self.input_received = asyncio.Event()
        self.stdin_payload: bytes | None = None
        self.terminate_calls = 0
        self.kill_calls = 0

    async def communicate(self, payload: bytes) -> tuple[bytes, bytes]:
        self.stdin_payload = payload
        self.input_received.set()
        if self._block:
            await self._finished.wait()
        elif self.returncode is None:
            self.returncode = self._final_returncode
        return self.stdout_bytes, self.stderr_bytes

    async def wait(self) -> int:
        if self.returncode is None:
            await self._finished.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, returncode: int | None = None) -> None:
        self.returncode = (
            self._final_returncode if returncode is None else returncode
        )
        self._finished.set()

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.finish(-9)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_parse_exec_timeout_uses_ten_minute_default(raw: str | None) -> None:
    assert parse_exec_timeout_seconds(raw) == 600.0


def test_parse_exec_timeout_accepts_positive_integer_string() -> None:
    assert parse_exec_timeout_seconds(" 42 ") == 42.0


@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "abc", "+2"])
def test_parse_exec_timeout_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        parse_exec_timeout_seconds(raw)

    assert exc_info.value.code == "CODEX_EXEC_CONFIG_INVALID"


async def test_normal_exit_writes_utf8_and_falls_back_to_stdout(
    tmp_path: Path,
) -> None:
    process = FakeProcess(stdout="你好，世界".encode())
    output_path = tmp_path / "output-last-message.txt"

    result = await supervise_codex_process(
        process,
        "提示：写一段中文",
        output_path,
        timeout_seconds=1,
        poll_interval=0.001,
    )

    assert process.stdin_payload == "提示：写一段中文".encode("utf-8")
    assert result.stdout == "你好，世界".encode("utf-8")
    assert result.stderr == b""
    assert result.returncode == 0
    assert result.output == "你好，世界"
    assert result.completed_from_final_message is False


async def test_normal_exit_prefers_existing_output_last_message(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "output-last-message.txt"
    output_path.write_text("最终答案", encoding="utf-8")
    process = FakeProcess(stdout=b"stream output")

    result = await supervise_codex_process(
        process,
        "prompt",
        output_path,
        timeout_seconds=1,
        poll_interval=0.001,
    )

    assert result.output == "最终答案"
    assert result.completed_from_final_message is False


async def test_stable_final_message_completes_and_cleans_only_process(
    tmp_path: Path,
) -> None:
    process = FakeProcess(stdout=b"stream", stderr=b"warning", block=True, pid=8765)
    output_path = tmp_path / "output-last-message.txt"
    terminated_pids: list[int] = []

    async def terminate(target: FakeProcess) -> None:
        terminated_pids.append(target.pid)
        target.finish(-15)

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            "prompt",
            output_path,
            timeout_seconds=1,
            stable_seconds=0.02,
            poll_interval=0.005,
            terminate=terminate,
        )
    )
    await process.input_received.wait()
    output_path.write_text("稳定的最终消息", encoding="utf-8")

    result = await task

    assert terminated_pids == [8765]
    assert result.output == "稳定的最终消息"
    assert result.returncode == -15
    assert result.stdout == b"stream"
    assert result.stderr == b"warning"
    assert result.completed_from_final_message is True


async def test_timeout_cleans_process_and_raises_stable_error_code(
    tmp_path: Path,
) -> None:
    process = FakeProcess(block=True)
    terminated_pids: list[int] = []

    async def terminate(target: FakeProcess) -> None:
        terminated_pids.append(target.pid)
        target.finish(-15)

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await supervise_codex_process(
            process,
            "prompt",
            tmp_path / "missing.txt",
            timeout_seconds=0.03,
            poll_interval=0.005,
            terminate=terminate,
        )

    assert exc_info.value.code == "CODEX_EXEC_TIMEOUT"
    assert terminated_pids == [4321]


async def test_cancellation_cleans_process_and_propagates_cancelled_error(
    tmp_path: Path,
) -> None:
    process = FakeProcess(block=True)
    terminated_pids: list[int] = []

    async def terminate(target: FakeProcess) -> None:
        terminated_pids.append(target.pid)
        target.finish(-15)

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            "prompt",
            tmp_path / "missing.txt",
            timeout_seconds=10,
            poll_interval=0.005,
            terminate=terminate,
        )
    )
    await process.input_received.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert terminated_pids == [4321]


async def test_windows_termination_uses_exact_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []
    process = FakeProcess(block=True, pid=2468)

    class FakeTaskkill:
        async def wait(self) -> int:
            process.finish(-15)
            return 0

    async def fake_create_subprocess_exec(*argv: object, **kwargs: object) -> FakeTaskkill:
        calls.append(argv)
        return FakeTaskkill()

    monkeypatch.setattr(
        "novelvideo.knowledge_runtime.codex_process._is_windows",
        lambda: True,
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await terminate_process_tree(process)

    assert calls == [("taskkill", "/PID", "2468", "/T", "/F")]


async def test_cancellation_is_bounded_when_communication_does_not_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_process, "_CLEANUP_TIMEOUT_SECONDS", 0.01)
    process = FakeProcess(block=True)

    async def delayed_terminate(target: FakeProcess) -> None:
        asyncio.get_running_loop().call_later(0.2, target.finish, -15)

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            "prompt",
            tmp_path / "missing.txt",
            timeout_seconds=10,
            poll_interval=0.005,
            terminate=delayed_terminate,
        )
    )
    await process.input_received.wait()
    started_at = asyncio.get_running_loop().time()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert asyncio.get_running_loop().time() - started_at < 0.1


async def test_cancellation_is_bounded_when_communication_ignores_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_process, "_CLEANUP_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()

    class CancellationResistantProcess(FakeProcess):
        async def communicate(self, payload: bytes) -> tuple[bytes, bytes]:
            self.stdin_payload = payload
            self.input_received.set()
            try:
                await self._finished.wait()
            except asyncio.CancelledError:
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        continue
            return self.stdout_bytes, self.stderr_bytes

    process = CancellationResistantProcess(block=True)

    async def delayed_terminate(target: FakeProcess) -> None:
        return None

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            "prompt",
            tmp_path / "missing.txt",
            timeout_seconds=10,
            poll_interval=0.005,
            terminate=delayed_terminate,
        )
    )
    await process.input_received.wait()
    task.cancel()

    done, _ = await asyncio.wait({task}, timeout=0.08)
    try:
        assert task in done
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        process.finish(-9)
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=0.1)


async def test_drain_communication_is_bounded_when_task_ignores_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_process, "_CLEANUP_TIMEOUT_SECONDS", 0.01)
    started = asyncio.Event()
    release = asyncio.Event()

    async def ignore_cancel() -> tuple[bytes, bytes]:
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return b"late stdout", b"late stderr"

    communication = asyncio.create_task(ignore_cancel())
    await started.wait()
    drain = asyncio.create_task(codex_process._drain_communication(communication))

    done, _ = await asyncio.wait({drain}, timeout=0.08)
    try:
        assert drain in done
        assert await drain == (b"", b"")
    finally:
        release.set()
        if not drain.done():
            drain.cancel()
        with suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(drain, timeout=0.1)
        with suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(communication, timeout=0.1)


async def test_stable_output_reports_cleanup_failure_when_process_never_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_process, "_CLEANUP_TIMEOUT_SECONDS", 0.01)

    class UnkillableProcess(FakeProcess):
        def kill(self) -> None:
            self.kill_calls += 1

    process = UnkillableProcess(block=True)
    output_path = tmp_path / "output-last-message.txt"

    async def ineffective_terminate(target: FakeProcess) -> None:
        return None

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            "prompt",
            output_path,
            timeout_seconds=1,
            stable_seconds=0.01,
            poll_interval=0.005,
            terminate=ineffective_terminate,
        )
    )
    await process.input_received.wait()
    output_path.write_text("稳定输出", encoding="utf-8")

    done, _ = await asyncio.wait({task}, timeout=0.12)
    try:
        assert task in done
        with pytest.raises(KnowledgeRuntimeError) as exc_info:
            await task
        assert exc_info.value.code == "CODEX_PROCESS_CLEANUP_FAILED"
    finally:
        process.finish(-9)
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=0.1)


async def test_stable_output_survives_communication_pipe_error_after_terminate(
    tmp_path: Path,
) -> None:
    class BrokenPipeProcess(FakeProcess):
        async def communicate(self, payload: bytes) -> tuple[bytes, bytes]:
            self.stdin_payload = payload
            self.input_received.set()
            await self._finished.wait()
            raise OSError("pipe closed during cleanup")

    process = BrokenPipeProcess(block=True)
    output_path = tmp_path / "output-last-message.txt"

    async def terminate(target: FakeProcess) -> None:
        target.finish(-15)

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            "prompt",
            output_path,
            timeout_seconds=1,
            stable_seconds=0.01,
            poll_interval=0.005,
            terminate=terminate,
        )
    )
    await process.input_received.wait()
    output_path.write_text("稳定输出", encoding="utf-8")

    result = await task

    assert result.output == "稳定输出"
    assert result.returncode == -15
    assert result.completed_from_final_message is True


async def test_timeout_survives_communication_pipe_error_after_terminate(
    tmp_path: Path,
) -> None:
    class BrokenPipeProcess(FakeProcess):
        async def communicate(self, payload: bytes) -> tuple[bytes, bytes]:
            self.stdin_payload = payload
            self.input_received.set()
            await self._finished.wait()
            raise OSError("pipe closed during cleanup")

    process = BrokenPipeProcess(block=True)

    async def terminate(target: FakeProcess) -> None:
        target.finish(-15)

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await supervise_codex_process(
            process,
            "prompt",
            tmp_path / "missing.txt",
            timeout_seconds=0.03,
            poll_interval=0.005,
            terminate=terminate,
        )

    assert exc_info.value.code == "CODEX_EXEC_TIMEOUT"


async def test_cancellation_survives_communication_pipe_error_after_terminate(
    tmp_path: Path,
) -> None:
    class BrokenPipeProcess(FakeProcess):
        async def communicate(self, payload: bytes) -> tuple[bytes, bytes]:
            self.stdin_payload = payload
            self.input_received.set()
            await self._finished.wait()
            raise OSError("pipe closed during cleanup")

    process = BrokenPipeProcess(block=True)

    async def terminate(target: FakeProcess) -> None:
        target.finish(-15)

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            "prompt",
            tmp_path / "missing.txt",
            timeout_seconds=10,
            poll_interval=0.005,
            terminate=terminate,
        )
    )
    await process.input_received.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_timeout_survives_terminate_oserror_when_exact_kill_succeeds(
    tmp_path: Path,
) -> None:
    process = FakeProcess(block=True)

    async def broken_terminate(target: FakeProcess) -> None:
        raise OSError("terminate failed")

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await supervise_codex_process(
            process,
            "prompt",
            tmp_path / "missing.txt",
            timeout_seconds=0.03,
            poll_interval=0.005,
            terminate=broken_terminate,
        )

    assert exc_info.value.code == "CODEX_EXEC_TIMEOUT"
    assert process.kill_calls == 1
    assert process.returncode == -9


async def test_cancellation_survives_terminate_process_lookup_error_when_kill_succeeds(
    tmp_path: Path,
) -> None:
    process = FakeProcess(block=True)

    async def vanished_during_terminate(target: FakeProcess) -> None:
        raise ProcessLookupError("terminate raced with process exit")

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            "prompt",
            tmp_path / "missing.txt",
            timeout_seconds=10,
            poll_interval=0.005,
            terminate=vanished_during_terminate,
        )
    )
    await process.input_received.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.kill_calls == 1
    assert process.returncode == -9


async def test_timeout_reports_cleanup_failure_when_exact_kill_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_process, "_CLEANUP_TIMEOUT_SECONDS", 0.01)

    class KillErrorProcess(FakeProcess):
        def kill(self) -> None:
            self.kill_calls += 1
            raise OSError("kill failed")

    process = KillErrorProcess(block=True)

    async def ineffective_terminate(target: FakeProcess) -> None:
        return None

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await asyncio.wait_for(
            supervise_codex_process(
                process,
                "prompt",
                tmp_path / "missing.txt",
                timeout_seconds=0.03,
                poll_interval=0.005,
                terminate=ineffective_terminate,
            ),
            timeout=0.15,
        )

    assert exc_info.value.code == "CODEX_PROCESS_CLEANUP_FAILED"
    assert process.returncode is None


async def test_cancellation_chains_cleanup_failure_when_exact_kill_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_process, "_CLEANUP_TIMEOUT_SECONDS", 0.01)

    class KillErrorProcess(FakeProcess):
        def kill(self) -> None:
            self.kill_calls += 1
            raise ProcessLookupError("kill raced but process is still reported alive")

    process = KillErrorProcess(block=True)

    async def ineffective_terminate(target: FakeProcess) -> None:
        return None

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            "prompt",
            tmp_path / "missing.txt",
            timeout_seconds=10,
            poll_interval=0.005,
            terminate=ineffective_terminate,
        )
    )
    await process.input_received.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await asyncio.wait_for(task, timeout=0.15)

    cleanup_error = exc_info.value.__cause__
    assert isinstance(cleanup_error, KnowledgeRuntimeError)
    assert cleanup_error.code == "CODEX_PROCESS_CLEANUP_FAILED"
    assert process.returncode is None

async def test_windows_taskkill_failure_falls_back_to_exact_process_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedTaskkill:
        async def wait(self) -> int:
            return 1

    async def fake_create_subprocess_exec(
        *argv: object, **kwargs: object
    ) -> FailedTaskkill:
        return FailedTaskkill()

    monkeypatch.setattr(codex_process, "_is_windows", lambda: True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    process = FakeProcess(block=True, pid=1357)

    await terminate_process_tree(process, grace_seconds=0.01)

    assert process.kill_calls == 1
    assert process.returncode == -9


async def test_windows_taskkill_start_error_falls_back_to_exact_process_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_create_subprocess_exec(
        *argv: object, **kwargs: object
    ) -> object:
        raise OSError("taskkill is unavailable")

    monkeypatch.setattr(codex_process, "_is_windows", lambda: True)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", failed_create_subprocess_exec
    )
    process = FakeProcess(block=True, pid=1358)

    await terminate_process_tree(process, grace_seconds=0.01)

    assert process.kill_calls == 1
    assert process.returncode == -9


async def test_windows_taskkill_wait_error_falls_back_to_exact_process_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenTaskkill:
        async def wait(self) -> int:
            raise RuntimeError("taskkill wait failed")

    async def fake_create_subprocess_exec(
        *argv: object, **kwargs: object
    ) -> BrokenTaskkill:
        return BrokenTaskkill()

    monkeypatch.setattr(codex_process, "_is_windows", lambda: True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    process = FakeProcess(block=True, pid=1359)

    await terminate_process_tree(process, grace_seconds=0.01)

    assert process.kill_calls == 1
    assert process.returncode == -9


async def test_posix_non_group_leader_falls_back_to_exact_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_process, "_is_windows", lambda: False)
    monkeypatch.setattr(
        codex_process.os,
        "getpgid",
        lambda pid: pid + 1,
        raising=False,
    )

    def unexpected_killpg(pid: int, sig: int) -> None:
        raise AssertionError("must not signal an unrelated process group")

    monkeypatch.setattr(
        codex_process.os,
        "killpg",
        unexpected_killpg,
        raising=False,
    )
    process = FakeProcess(block=True, pid=9753)

    await terminate_process_tree(process, grace_seconds=0.01)

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.returncode == -9
