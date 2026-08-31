# Codex Runtime 可用性实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 NuomiDrama 能轻量、可靠地识别本机 Codex CLI，并让所有 Codex 结构化文本任务共享单次、可超时、可取消、只清理自身进程树的受控执行器。

**架构：** 新建独立的 Codex 进程监督模块，统一总超时、最终消息稳定检测和进程树回收；基础结构化后端负责临时文件、Schema 和错误映射，路由后端只负责追加冻结的模型参数。Runtime 状态检查仅执行 `--version` 与 `login status`，API 和设置页消费同一组 `installed / compatible / authenticated / ready` 状态，不调用模型。

**技术栈：** Python 3.11、asyncio、FastAPI、Pydantic、pytest、React 19、TypeScript、TanStack Query、Vitest、Testing Library

---

## 实施约束

- 基于当前 `main` 工作区实施；用户已明确要求直接在 `main` 写，不创建 worktree。
- 当前工作区包含大量用户与其他任务的未提交修改。每次只暂存本任务列出的精确文件，禁止 `git add .`、`git add -A`、reset、checkout 或清理其他文件。
- RunningHub MiniMax H3、GRSAI、Ollama 和模型 API 路由均不在本计划改动范围内。
- Codex 故障必须停止并返回稳定错误码，不得静默切换到模型 API。
- 默认测试不调用真实模型；只有任务 5 的人工验收命令会产生一次真实 Codex 请求。
- 设计依据：`docs/superpowers/specs/2026-08-31-codex-runtime-availability-design.md`。

## 文件与职责

- 创建：`src/novelvideo/knowledge_runtime/codex_process.py`
  - 解析执行超时。
  - 监督单个 Codex 子进程。
  - 检测稳定的 `--output-last-message`。
  - 按精确 PID 回收本次进程树。
- 创建：`tests/test_codex_process.py`
  - 用假进程验证超时、稳定最终消息、取消、UTF-8 和进程清理。
- 修改：`src/novelvideo/knowledge_runtime/codex.py:21-390`
  - 扩展 Runtime 状态模型。
  - 增加版本解析、最低版本和轻量命令超时。
  - 接入共享进程监督器。
  - 删除外层 `CODEX_EXEC_FAILED` 三次完整重跑。
  - 提供可覆写的 `build_argv()`。
- 修改：`src/novelvideo/text_task_runtime/runtime.py:33-139`
  - 保留冻结路由参数拼装。
  - 删除重复的 `_run_once()`，继承共享执行路径。
- 修改：`src/novelvideo/api/routes/knowledge_runtime.py:72-154`
  - 总状态改用 `codex.ready`。
  - 重新识别接口返回兼容性错误码。
- 修改：`tests/test_knowledge_runtime_codex.py:1-274`
  - 覆盖路径优先级、版本门槛、登录状态、轻量命令超时、单次执行和共享执行路径。
- 修改：`tests/test_text_task_runtime_routing.py:169-188`
  - 验证路由后端复用基础执行器并保留 stdout/stderr 尾部诊断。
- 修改：`tests/test_api_knowledge_runtime.py:1-154`
  - 更新状态契约并验证重新识别不启动 `codex exec`。
- 修改：`frontend/src/lib/queries/knowledge-runtime.ts:7-137`
  - 扩展 Codex 状态类型，将 mutation 命名改为“重新识别”语义。
- 修改：`frontend/src/components/settings/knowledge-runtime-section.tsx:1-132`
  - 展示可用、版本过低、未登录、未安装和无法启动。
  - 按钮改为“重新识别”并展示解析路径。
- 修改：`frontend/src/__tests__/lib/queries/knowledge-runtime.test.tsx:1-61`
  - 覆盖状态字段和重新识别后的缓存刷新。
- 修改：`frontend/src/__tests__/components/settings/knowledge-runtime-section.test.tsx:33-93`
  - 覆盖状态标签、路径与按钮文案。

### 任务 1：建立受控 Codex 进程监督器

**文件：**
- 创建：`src/novelvideo/knowledge_runtime/codex_process.py`
- 创建：`tests/test_codex_process.py`

- [ ] **步骤 1：编写超时配置与监督行为的失败测试**

创建 `tests/test_codex_process.py`，写入以下测试。假进程只暴露生产协议实际使用的属性和方法，避免依赖真实 Codex：

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from novelvideo.knowledge_runtime.codex_process import (
    CodexProcessResult,
    parse_exec_timeout_seconds,
    supervise_codex_process,
)
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError


class FakeProcess:
    def __init__(
        self,
        *,
        pid: int = 4321,
        returncode: int | None = None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        hang: bool = False,
    ) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.hang = hang
        self.started = asyncio.Event()

    async def communicate(self, _input: bytes | None = None) -> tuple[bytes, bytes]:
        self.started.set()
        if self.hang:
            await asyncio.Future()
        return self.stdout, self.stderr

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 143

    def kill(self) -> None:
        self.returncode = 137


def test_parse_exec_timeout_defaults_to_ten_minutes() -> None:
    assert parse_exec_timeout_seconds(None) == 600.0
    assert parse_exec_timeout_seconds("") == 600.0
    assert parse_exec_timeout_seconds("45") == 45.0


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc"])
def test_parse_exec_timeout_rejects_invalid_values(value: str) -> None:
    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        parse_exec_timeout_seconds(value)
    assert exc_info.value.code == "CODEX_EXEC_CONFIG_INVALID"


@pytest.mark.asyncio
async def test_supervisor_times_out_and_cleans_exact_process(tmp_path: Path) -> None:
    process = FakeProcess(hang=True)
    cleaned: list[int] = []

    async def terminate(target: FakeProcess) -> None:
        cleaned.append(target.pid)
        target.kill()

    with pytest.raises(KnowledgeRuntimeError) as exc_info:
        await supervise_codex_process(
            process,
            prompt="中文提示",
            output_path=tmp_path / "result.txt",
            timeout_seconds=0.02,
            stable_seconds=0.01,
            poll_interval=0.001,
            terminate=terminate,
        )

    assert exc_info.value.code == "CODEX_EXEC_TIMEOUT"
    assert cleaned == [4321]


@pytest.mark.asyncio
async def test_supervisor_accepts_stable_final_message_and_cleans_parent(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "result.txt"
    output_path.write_text("RUNTIME_OK_中文", encoding="utf-8")
    process = FakeProcess(hang=True)
    cleaned: list[int] = []

    async def terminate(target: FakeProcess) -> None:
        cleaned.append(target.pid)
        target.kill()

    result = await supervise_codex_process(
        process,
        prompt="只返回结果",
        output_path=output_path,
        timeout_seconds=1,
        stable_seconds=0,
        poll_interval=0.001,
        terminate=terminate,
    )

    assert result == CodexProcessResult(
        stdout=b"",
        stderr=b"",
        returncode=137,
        output="RUNTIME_OK_中文",
        completed_from_final_message=True,
    )
    assert cleaned == [4321]


@pytest.mark.asyncio
async def test_supervisor_propagates_cancel_after_cleanup(tmp_path: Path) -> None:
    process = FakeProcess(hang=True)
    cleaned: list[int] = []

    async def terminate(target: FakeProcess) -> None:
        cleaned.append(target.pid)
        target.kill()

    task = asyncio.create_task(
        supervise_codex_process(
            process,
            prompt="prompt",
            output_path=tmp_path / "result.txt",
            timeout_seconds=60,
            stable_seconds=5,
            poll_interval=0.001,
            terminate=terminate,
        )
    )
    await process.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned == [4321]


@pytest.mark.asyncio
async def test_supervisor_preserves_utf8_stdout(tmp_path: Path) -> None:
    process = FakeProcess(returncode=0, stdout="摘要内容".encode("utf-8"))

    async def terminate(_target: FakeProcess) -> None:
        raise AssertionError("successful process must not be terminated")

    result = await supervise_codex_process(
        process,
        prompt="中文提示",
        output_path=tmp_path / "result.txt",
        timeout_seconds=1,
        stable_seconds=0,
        poll_interval=0.001,
        terminate=terminate,
    )

    assert result.output == "摘要内容"
    assert result.completed_from_final_message is False
```

- [ ] **步骤 2：运行新测试并确认失败**

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_codex_process.py -q -p no:cacheprovider --basetemp '.codex-runtime-plan-red-process'
```

预期：测试收集失败，报错 `ModuleNotFoundError: No module named 'novelvideo.knowledge_runtime.codex_process'`。

- [ ] **步骤 3：实现最小受控执行器**

创建 `src/novelvideo/knowledge_runtime/codex_process.py`。实现以下公开类型与函数；最终消息以 UTF-8 读取，错误输出保持 bytes 交给上层分类：

```python
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import os
from pathlib import Path
import signal
from typing import Any, Awaitable, Callable, Protocol

from .settings import KnowledgeRuntimeError


DEFAULT_EXEC_TIMEOUT_SECONDS = 600.0


class CodexProcess(Protocol):
    pid: int
    returncode: int | None

    async def communicate(
        self, input_data: bytes | None = None
    ) -> tuple[bytes, bytes]: ...

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@dataclass(frozen=True)
class CodexProcessResult:
    stdout: bytes
    stderr: bytes
    returncode: int
    output: str
    completed_from_final_message: bool


TerminateProcess = Callable[[CodexProcess], Awaitable[None]]


def parse_exec_timeout_seconds(raw_value: str | None) -> float:
    value = (raw_value or "").strip()
    if not value:
        return DEFAULT_EXEC_TIMEOUT_SECONDS
    try:
        parsed = int(value)
    except ValueError as exc:
        raise KnowledgeRuntimeError(
            "CODEX_EXEC_TIMEOUT_SECONDS 必须是正整数。",
            code="CODEX_EXEC_CONFIG_INVALID",
        ) from exc
    if parsed <= 0:
        raise KnowledgeRuntimeError(
            "CODEX_EXEC_TIMEOUT_SECONDS 必须是正整数。",
            code="CODEX_EXEC_CONFIG_INVALID",
        )
    return float(parsed)


async def terminate_process_tree(
    process: CodexProcess,
    *,
    platform: str | None = None,
    grace_seconds: float = 5.0,
) -> None:
    if process.returncode is not None:
        return
    effective_platform = platform or os.name
    if effective_platform == "nt":
        taskkill = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await taskkill.communicate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        if effective_platform == "nt":
            process.kill()
        else:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


async def _cancel_communication(task: asyncio.Task[tuple[bytes, bytes]]) -> None:
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def supervise_codex_process(
    process: CodexProcess,
    *,
    prompt: str,
    output_path: Path,
    timeout_seconds: float,
    stable_seconds: float = 5.0,
    poll_interval: float = 0.25,
    terminate: TerminateProcess = terminate_process_tree,
) -> CodexProcessResult:
    communication = asyncio.create_task(process.communicate(prompt.encode("utf-8")))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last_signature: tuple[int, int] | None = None
    stable_since: float | None = None

    try:
        while True:
            if communication.done():
                stdout, stderr = await communication
                output = (
                    output_path.read_text(encoding="utf-8")
                    if output_path.exists()
                    else stdout.decode("utf-8", errors="replace")
                )
                return CodexProcessResult(
                    stdout=stdout,
                    stderr=stderr,
                    returncode=int(process.returncode or 0),
                    output=output,
                    completed_from_final_message=False,
                )

            now = loop.time()
            if output_path.exists() and output_path.stat().st_size > 0:
                stat = output_path.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
                if signature != last_signature:
                    last_signature = signature
                    stable_since = now
                elif stable_since is not None and now - stable_since >= stable_seconds:
                    output = output_path.read_text(encoding="utf-8")
                    await terminate(process)
                    await _cancel_communication(communication)
                    return CodexProcessResult(
                        stdout=b"",
                        stderr=b"",
                        returncode=int(process.returncode or 0),
                        output=output,
                        completed_from_final_message=True,
                    )

            if now >= deadline:
                await terminate(process)
                await _cancel_communication(communication)
                raise KnowledgeRuntimeError(
                    f"Codex 执行超过 {int(timeout_seconds)} 秒。",
                    code="CODEX_EXEC_TIMEOUT",
                )
            await asyncio.sleep(min(poll_interval, max(0.0, deadline - now)))
    except asyncio.CancelledError:
        await terminate(process)
        await _cancel_communication(communication)
        raise
```

实现时同步保留 `Protocol` 方法体的省略号，这是 Python 类型协议语法，不代表未完成实现。

- [ ] **步骤 4：运行监督器测试确认通过**

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_codex_process.py -q -p no:cacheprovider --basetemp '.codex-runtime-plan-green-process'
```

预期：`9 passed`。

- [ ] **步骤 5：提交任务 1**

```powershell
git add -- src/novelvideo/knowledge_runtime/codex_process.py tests/test_codex_process.py
git diff --cached --check
git commit -m "Hermes: 添加 Codex 受控进程监督器"
```

### 任务 2：统一两个结构化后端并取消外层传输重跑

**文件：**
- 修改：`src/novelvideo/knowledge_runtime/codex.py:29-69`
- 修改：`src/novelvideo/knowledge_runtime/codex.py:290-390`
- 修改：`src/novelvideo/text_task_runtime/runtime.py:33-139`
- 修改：`tests/test_knowledge_runtime_codex.py:138-274`
- 修改：`tests/test_text_task_runtime_routing.py:169-188`

- [ ] **步骤 1：把“传输失败只执行一次”写成失败测试**

将 `tests/test_knowledge_runtime_codex.py` 中 `test_structured_backend_retries_transient_codex_exec_failure` 替换为：

```python
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
```

在 `tests/test_text_task_runtime_routing.py` 增加继承契约：

```python
def test_routed_codex_backend_inherits_shared_run_once() -> None:
    assert "_run_once" not in RoutedCodexCliStructuredBackend.__dict__
```

- [ ] **步骤 2：运行两个测试确认旧实现失败**

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_knowledge_runtime_codex.py::test_structured_backend_does_not_retry_codex_exec_failure tests\test_text_task_runtime_routing.py::test_routed_codex_backend_inherits_shared_run_once -q -p no:cacheprovider --basetemp '.codex-runtime-plan-red-shared'
```

预期：第一个测试观察到 `_run_once.await_count == 2`，第二个测试观察到路由类仍定义 `_run_once`。

- [ ] **步骤 3：让基础后端成为唯一执行入口**

在 `src/novelvideo/knowledge_runtime/codex.py`：

1. 为 `_ThreadedProcess` 增加精确 PID：

```python
    @property
    def pid(self) -> int:
        return int(self._process.pid)
```

2. 创建子进程时建立独立进程组。`_ThreadedProcess.__init__` 与 `_create_codex_process` 使用相同规则：

```python
def _process_group_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}
```

将 `subprocess.Popen(...)` 和 `asyncio.create_subprocess_exec(...)` 都追加 `**_process_group_kwargs()`。

3. 在 `CodexCliStructuredBackend` 中增加可覆写参数构造方法：

```python
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
```

4. 将 `acreate_structured_output` 的嵌套 `exec_attempt` 循环改成仅保留 Schema 修复循环：

```python
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
```

5. 将 `_run_once` 中的参数构造改为 `self.build_argv(...)`，创建进程后调用监督器：

```python
            outcome = await supervise_codex_process(
                process,
                prompt=prompt,
                output_path=output_path,
                timeout_seconds=parse_exec_timeout_seconds(
                    os.getenv("CODEX_EXEC_TIMEOUT_SECONDS")
                ),
            )
            if outcome.completed_from_final_message:
                return outcome.output
            if outcome.returncode != 0:
                diagnostic = outcome.stderr + (
                    b"\n" + outcome.stdout if outcome.stdout else b""
                )
                message = _codex_error_summary(diagnostic)
                lowered = message.lower()
                if "not logged in" in lowered:
                    code = "CODEX_NOT_AUTHENTICATED"
                elif "invalid_json_schema" in lowered:
                    code = "CODEX_SCHEMA_INVALID"
                else:
                    code = "CODEX_EXEC_FAILED"
                raise KnowledgeRuntimeError(
                    message or "Codex 执行失败。",
                    code=code,
                )
            return outcome.output
```

文件顶部从 `.codex_process` 导入 `parse_exec_timeout_seconds`、`supervise_codex_process`。

- [ ] **步骤 4：删除路由后端的重复执行实现**

在 `src/novelvideo/text_task_runtime/runtime.py` 删除 `RoutedCodexCliStructuredBackend._run_once` 整个方法，只保留现有 `build_argv`。删除因此不再使用的 `asyncio`、`json`、`Path`、`tempfile` 导入。

`RoutedCodexCliStructuredBackend.build_argv` 仍必须保留以下行为：

```python
    def build_argv(
        self,
        *,
        cwd: str,
        output_path: str,
        schema_path: str | None,
    ) -> list[str]:
        from novelvideo.knowledge_runtime.codex import build_codex_exec_argv

        model = validate_text_task_model_name(self.model)
        argv = build_codex_exec_argv(
            codex_bin=self.codex_bin,
            cwd=cwd,
            output_path=output_path,
            schema_path=schema_path,
            model=model,
        )
        if self.reasoning_effort:
            argv[-1:-1] = [
                "-c",
                f'model_reasoning_effort="{self.reasoning_effort}"',
            ]
        return argv
```

- [ ] **步骤 5：运行后端定向测试确认通过**

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_codex_process.py tests\test_knowledge_runtime_codex.py tests\test_text_task_runtime_routing.py -q -p no:cacheprovider --basetemp '.codex-runtime-plan-green-shared'
```

预期：全部通过；Schema 非法输出仍最多执行三次修复请求，`CODEX_EXEC_FAILED` 只执行一次。

- [ ] **步骤 6：提交任务 2**

```powershell
git add -- src/novelvideo/knowledge_runtime/codex.py src/novelvideo/text_task_runtime/runtime.py tests/test_knowledge_runtime_codex.py tests/test_text_task_runtime_routing.py
git diff --cached --check
git commit -m "Hermes: 统一 Codex 结构化任务执行路径"
```

### 任务 3：实现 Multica 风格轻量 Runtime 识别

**文件：**
- 修改：`src/novelvideo/knowledge_runtime/codex.py:21-161`
- 修改：`src/novelvideo/api/routes/knowledge_runtime.py:72-154`
- 修改：`tests/test_knowledge_runtime_codex.py:193-217`
- 修改：`tests/test_api_knowledge_runtime.py:1-154`

- [ ] **步骤 1：先写路径、版本和登录门槛测试**

在 `tests/test_knowledge_runtime_codex.py` 从 `novelvideo.knowledge_runtime.codex` 额外导入 `MIN_CODEX_VERSION`、`get_codex_cli_status`、`parse_codex_version`，增加：

```python
def test_parse_codex_version_and_minimum() -> None:
    assert parse_codex_version("codex-cli 0.146.0") == (0, 146, 0)
    assert MIN_CODEX_VERSION == (0, 100, 0)
    assert parse_codex_version("unknown") is None


@pytest.mark.asyncio
async def test_status_prefers_explicit_codex_bin(monkeypatch) -> None:
    commands: list[tuple[str, ...]] = []

    async def run(*argv: str, timeout_seconds: float = 10.0):
        commands.append(argv)
        if argv[-1] == "--version":
            return 0, "codex-cli 0.146.0", ""
        return 0, "Logged in using ChatGPT", ""

    monkeypatch.setenv("CODEX_BIN", "E:/npm-global/codex.cmd")
    monkeypatch.setattr(
        "novelvideo.knowledge_runtime.codex._run_status_command",
        run,
    )

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
    monkeypatch.setattr(
        "novelvideo.knowledge_runtime.codex._run_status_command",
        run,
    )

    status = await get_codex_cli_status("E:/tools/codex.cmd")

    assert status.installed is True
    assert status.compatible is False
    assert status.authenticated is False
    assert status.ready is False
    assert "0.100.0" in status.message
    run.assert_awaited_once_with(
        "E:/tools/codex.cmd",
        "--version",
        timeout_seconds=10.0,
    )


@pytest.mark.asyncio
async def test_status_reports_login_timeout(monkeypatch) -> None:
    run = AsyncMock(
        side_effect=[
            (0, "codex-cli 0.146.0", ""),
            asyncio.TimeoutError(),
        ]
    )
    monkeypatch.setattr(
        "novelvideo.knowledge_runtime.codex._run_status_command",
        run,
    )

    status = await get_codex_cli_status("E:/tools/codex.cmd")

    assert status.compatible is True
    assert status.authenticated is False
    assert status.ready is False
    assert "登录状态检查超时" in status.message
```

更新现有 `CodexCliStatus` 断言，全部改成关键字参数，包含七个字段。

- [ ] **步骤 2：运行状态测试确认失败**

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_knowledge_runtime_codex.py -q -p no:cacheprovider --basetemp '.codex-runtime-plan-red-status'
```

预期：因 `compatible`、`ready`、`path`、`parse_codex_version` 和最低版本常量不存在而失败。

- [ ] **步骤 3：扩展状态模型与轻量识别函数**

在 `src/novelvideo/knowledge_runtime/codex.py` 定义：

```python
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


def parse_codex_version(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _codex_status(
    *,
    path: str,
    compatible: bool = False,
    authenticated: bool = False,
    version: str = "",
    message: str,
) -> CodexCliStatus:
    installed = bool(path)
    return CodexCliStatus(
        installed=installed,
        compatible=compatible,
        authenticated=authenticated,
        ready=installed and compatible and authenticated,
        path=path,
        version=version,
        message=message,
    )
```

文件顶部增加 `import re`。`parse_codex_login_status` 改为接收 `path`、`compatible` 和 `version`，并通过 `_codex_status` 构造结果。

将 `_run_status_command` 改为有 10 秒默认超时，并在超时或取消时精确清理进程：

```python
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
```

`get_codex_cli_status` 必须按下列顺序完成：函数参数、`CODEX_BIN`、`shutil.which("codex")`；版本无法解析或低于最低版本时不执行登录命令：

```python
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
        return _codex_status(
            path=executable,
            message=f"Codex CLI 无法启动: {exc}",
        )

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
        )
    except OSError as exc:
        return _codex_status(
            path=executable,
            compatible=True,
            version=version,
            message=f"Codex CLI 无法启动: {exc}",
        )

    message = (stdout or stderr).strip()
    authenticated = code == 0 and "not logged in" not in message.lower()
    return _codex_status(
        path=executable,
        compatible=True,
        authenticated=authenticated,
        version=version,
        message=message or ("Codex CLI 可用" if authenticated else "Codex 未登录"),
    )
```

- [ ] **步骤 4：更新 API 契约并写失败测试**

在 `tests/test_api_knowledge_runtime.py` 中提供统一工厂，避免位置参数失配：

```python
def codex_status(
    *,
    installed: bool = True,
    compatible: bool = True,
    authenticated: bool = True,
    path: str = "E:/npm-global/codex.cmd",
    version: str = "codex-cli 0.146.0",
    message: str = "Logged in using ChatGPT",
) -> CodexCliStatus:
    return CodexCliStatus(
        installed=installed,
        compatible=compatible,
        authenticated=authenticated,
        ready=installed and compatible and authenticated,
        path=path,
        version=version,
        message=message,
    )
```

增加接口测试：

```python
def test_codex_test_returns_version_error_without_exec(client, monkeypatch) -> None:
    probe = AsyncMock(
        return_value=codex_status(
            compatible=False,
            authenticated=False,
            message="Codex CLI 版本过低，需要 >= 0.100.0",
        )
    )
    monkeypatch.setattr(runtime_module, "get_codex_cli_status", probe)

    response = client.post("/api/v1/knowledge-runtime/codex/test")

    assert response.status_code == 200
    assert response.json()["errorCode"] == "CODEX_VERSION_UNSUPPORTED"
    assert response.json()["data"]["ready"] is False
    probe.assert_awaited_once_with()
```

- [ ] **步骤 5：修改 API 使用 `codex.ready` 和稳定错误码**

在 `src/novelvideo/api/routes/knowledge_runtime.py`：

```python
    ready = codex.ready and ollama["configured"]
```

`test_codex` 改为：

```python
@router.post("/codex/test")
async def test_codex() -> dict[str, Any]:
    status = await get_codex_cli_status()
    if status.ready:
        return {"ok": True, "data": asdict(status)}
    if not status.installed:
        error_code = "CODEX_NOT_INSTALLED"
    elif not status.compatible:
        error_code = "CODEX_VERSION_UNSUPPORTED"
    elif not status.authenticated:
        error_code = "CODEX_NOT_AUTHENTICATED"
    else:
        error_code = "CODEX_EXEC_FAILED"
    return {
        "ok": False,
        "errorCode": error_code,
        "message": status.message,
        "data": asdict(status),
    }
```

总状态分支使用 `not codex.ready` 判断 Codex 不可用；Ollama 逻辑保持不变。

- [ ] **步骤 6：运行 Runtime 与 API 测试确认通过**

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_knowledge_runtime_codex.py tests\test_api_knowledge_runtime.py -q -p no:cacheprovider --basetemp '.codex-runtime-plan-green-status'
```

预期：全部通过，测试替身只观察到 `--version` 和 `login status`，没有 `exec`。

- [ ] **步骤 7：提交任务 3**

```powershell
git add -- src/novelvideo/knowledge_runtime/codex.py src/novelvideo/api/routes/knowledge_runtime.py tests/test_knowledge_runtime_codex.py tests/test_api_knowledge_runtime.py
git diff --cached --check
git commit -m "Hermes: 增加 Codex Runtime 兼容性识别"
```

### 任务 4：让设置页准确展示重新识别结果

**文件：**
- 修改：`frontend/src/lib/queries/knowledge-runtime.ts:7-137`
- 修改：`frontend/src/components/settings/knowledge-runtime-section.tsx:1-132`
- 修改：`frontend/src/__tests__/lib/queries/knowledge-runtime.test.tsx:1-61`
- 修改：`frontend/src/__tests__/components/settings/knowledge-runtime-section.test.tsx:33-93`

- [ ] **步骤 1：扩展前端状态测试数据并写按钮失败断言**

将所有测试中的 Codex 状态改为完整形状：

```typescript
const readyCodex = {
  installed: true,
  compatible: true,
  authenticated: true,
  ready: true,
  path: "E:/npm-global/codex.cmd",
  version: "codex-cli 0.146.0",
  message: "Logged in using ChatGPT",
};
```

在组件测试中增加：

```typescript
expect(screen.getByText("可用")).toBeInTheDocument();
expect(screen.getByText("E:/npm-global/codex.cmd")).toBeInTheDocument();
expect(screen.getByRole("button", { name: "重新识别" })).toBeInTheDocument();
expect(screen.queryByRole("button", { name: "检查 Codex" })).not.toBeInTheDocument();
```

在查询测试中导入 `useRecognizeCodex`，增加：

```typescript
it("re-recognizes Codex without invoking a model endpoint", async () => {
  let calls = 0;
  server.use(
    http.post(
      "http://localhost:3000/api/v1/knowledge-runtime/codex/test",
      () => {
        calls += 1;
        return HttpResponse.json({ ok: true, data: readyCodex });
      },
    ),
  );

  const { result } = renderHook(() => useRecognizeCodex(), { wrapper });
  result.current.mutate();
  await waitFor(() => expect(result.current.isSuccess).toBe(true));

  expect(calls).toBe(1);
});
```

- [ ] **步骤 2：运行前端定向测试确认失败**

运行：

```powershell
pnpm exec vitest run src/__tests__/lib/queries/knowledge-runtime.test.tsx src/__tests__/components/settings/knowledge-runtime-section.test.tsx
```

工作目录：`frontend`。

预期：`useRecognizeCodex` 不存在，按钮仍显示“检查 Codex”，路径与“可用”状态未展示。

- [ ] **步骤 3：扩展查询类型并重命名 mutation**

在 `frontend/src/lib/queries/knowledge-runtime.ts`：

```typescript
export interface CodexRuntimeStatus {
  installed: boolean;
  compatible: boolean;
  authenticated: boolean;
  ready: boolean;
  path: string;
  version: string;
  message: string;
}
```

将 `useTestCodex` 重命名为 `useRecognizeCodex`，请求仍使用兼容端点，成功后刷新总状态：

```typescript
export function useRecognizeCodex() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/knowledge-runtime/codex/test", {
          throwHttpErrors: false,
        })
        .json<OkResponse<CodexRuntimeStatus> | ErrorResponse>(),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.knowledgeRuntime() });
    },
  });
}
```

- [ ] **步骤 4：更新设置组件的状态映射**

在 `frontend/src/components/settings/knowledge-runtime-section.tsx` 增加纯函数：

```typescript
function codexStatusLabel(status: CodexRuntimeStatus | undefined): string {
  if (!status?.installed) return "未安装";
  if (!status.compatible) return "版本过低";
  if (!status.authenticated) return "未登录";
  if (!status.ready) return "无法启动";
  return "可用";
}
```

导入 `CodexRuntimeStatus` 类型与 `useRecognizeCodex`。将变量改为：

```typescript
const recognizeCodex = useRecognizeCodex();
```

Codex 卡片状态和内容改为：

```tsx
<StatusPill ready={runtime?.codex.ready === true}>
  {status.isError ? "状态不可用" : codexStatusLabel(runtime?.codex)}
</StatusPill>

<div className="grid gap-2 sm:grid-cols-2">
  <div className="rounded-md border border-white/8 bg-black/20 px-3 py-2 text-xs">
    <div className="text-muted-foreground">版本</div>
    <div className="mt-1 font-mono">{runtime?.codex.version || "—"}</div>
  </div>
  <div className="rounded-md border border-white/8 bg-black/20 px-3 py-2 text-xs">
    <div className="text-muted-foreground">启动路径</div>
    <div className="mt-1 break-all font-mono">{runtime?.codex.path || "—"}</div>
  </div>
</div>
{runtime?.codex.message ? (
  <p className="text-xs text-muted-foreground">{runtime.codex.message}</p>
) : null}
<div className="flex justify-end">
  <Button
    type="button"
    variant="outline"
    size="sm"
    onClick={() => recognizeCodex.mutate()}
    disabled={recognizeCodex.isPending}
  >
    {recognizeCodex.isPending ? (
      <Loader2 className="size-3.5 animate-spin" />
    ) : (
      <RefreshCw className="size-3.5" />
    )}
    重新识别
  </Button>
</div>
```

- [ ] **步骤 5：增加版本过低与未登录组件用例**

把测试导入改为 `import { afterEach, expect, it, vi } from "vitest";`。在 `vi.mock("@/lib/queries/knowledge-runtime", ...)` 之前增加可安全用于 hoisted mock 的状态容器：

```typescript
const { readyCodex, runtimeMock } = vi.hoisted(() => {
  const readyCodex = {
    installed: true,
    compatible: true,
    authenticated: true,
    ready: true,
    path: "E:/npm-global/codex.cmd",
    version: "codex-cli 0.146.0",
    message: "Logged in using ChatGPT",
  };
  return { readyCodex, runtimeMock: { codex: readyCodex } };
});

afterEach(() => {
  runtimeMock.codex = readyCodex;
});
```

把知识运行时 query mock 中原有的 `codex: {...}` 替换为 `codex: runtimeMock.codex`，然后新增两个测试：

```typescript
it.each([
  [
    {
      ...readyCodex,
      compatible: false,
      authenticated: false,
      ready: false,
      message: "Codex CLI 版本过低，需要 >= 0.100.0",
    },
    "版本过低",
  ],
  [
    {
      ...readyCodex,
      authenticated: false,
      ready: false,
      message: "Not logged in",
    },
    "未登录",
  ],
])("shows the Codex recognition state", (codex, label) => {
  runtimeMock.codex = codex;
  render(<KnowledgeRuntimeSection open />);
  expect(screen.getByText(label)).toBeInTheDocument();
});
```

`afterEach` 会把 `runtimeMock.codex` 恢复为 `readyCodex`，避免用例间污染。

- [ ] **步骤 6：运行前端测试和类型构建**

运行：

```powershell
pnpm exec vitest run src/__tests__/lib/queries/knowledge-runtime.test.tsx src/__tests__/components/settings/knowledge-runtime-section.test.tsx
pnpm exec tsc -b --pretty false
```

工作目录：`frontend`。

预期：两组 Vitest 全部通过，TypeScript 构建退出码为 0。

- [ ] **步骤 7：提交任务 4**

```powershell
git add -- frontend/src/lib/queries/knowledge-runtime.ts frontend/src/components/settings/knowledge-runtime-section.tsx frontend/src/__tests__/lib/queries/knowledge-runtime.test.tsx frontend/src/__tests__/components/settings/knowledge-runtime-section.test.tsx
git diff --cached --check
git commit -m "Hermes: 更新 Codex Runtime 重新识别界面"
```

### 任务 5：回归验证与当前机器真实验收

**文件：**
- 验证：`src/novelvideo/knowledge_runtime/codex.py`
- 验证：`src/novelvideo/knowledge_runtime/codex_process.py`
- 验证：`src/novelvideo/text_task_runtime/runtime.py`
- 验证：`src/novelvideo/api/routes/knowledge_runtime.py`
- 验证：`frontend/src/lib/queries/knowledge-runtime.ts`
- 验证：`frontend/src/components/settings/knowledge-runtime-section.tsx`

- [ ] **步骤 1：运行后端完整定向回归**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_codex_process.py tests\test_knowledge_runtime_codex.py tests\test_api_knowledge_runtime.py tests\test_text_task_runtime_routing.py -q -p no:cacheprovider --basetemp '.codex-runtime-final-backend'
```

预期：全部通过；无真实模型调用。

- [ ] **步骤 2：运行前端完整定向回归**

```powershell
pnpm exec vitest run src/__tests__/lib/queries/knowledge-runtime.test.tsx src/__tests__/components/settings/knowledge-runtime-section.test.tsx
pnpm exec tsc -b --pretty false
```

工作目录：`frontend`。

预期：Vitest 全部通过，TypeScript 构建退出码为 0。

- [ ] **步骤 3：在当前机器验证轻量识别**

在仓库根目录运行：

```powershell
& '.\.venv\Scripts\python.exe' -c "import asyncio,json; from dataclasses import asdict; from novelvideo.knowledge_runtime import get_codex_cli_status; print(json.dumps(asdict(asyncio.run(get_codex_cli_status())), ensure_ascii=False))"
```

预期 JSON 至少满足：

```json
{
  "installed": true,
  "compatible": true,
  "authenticated": true,
  "ready": true,
  "path": "E:\\npm-global\\codex.cmd",
  "version": "codex-cli 0.146.0"
}
```

此命令只执行 `codex --version` 和 `codex login status`，不调用模型。

- [ ] **步骤 4：通过生产后端执行一次真实 UTF-8 烟雾任务**

在仓库根目录运行：


```powershell
$env:CODEX_EXEC_TIMEOUT_SECONDS='180'
& '.\.venv\Scripts\python.exe' -c "import asyncio; from novelvideo.knowledge_runtime import CodexCliStructuredBackend; print(asyncio.run(CodexCliStructuredBackend().acreate_structured_output('只返回 RUNTIME_OK_中文','不得调用工具，不得解释。',str)))"
Remove-Item Env:CODEX_EXEC_TIMEOUT_SECONDS
```

预期标准输出最后一行严格为 `RUNTIME_OK_中文`。如果 Codex CLI 内部发生 WebSocket 重试并回退 HTTPS，项目不得额外启动第二个 `codex exec`。

- [ ] **步骤 5：检查差异质量和提交边界**

```powershell
git diff --check
git status --short
git log -5 --oneline
```

预期：

- `git diff --check` 无输出。
- 本任务文件没有未提交修改。
- 其他既有脏文件保持原样。
- 最近提交包含四个以 `Hermes:` 开头的本计划提交。

- [ ] **步骤 6：记录验收证据**

最终交付消息必须列出：

1. 后端定向测试的通过数量与耗时。
2. 前端 Vitest 与 TypeScript 构建结果。
3. 轻量识别 JSON 中的 `path`、`version`、`ready`。
4. 真实生产后端烟雾输出。
5. 若出现 HTTPS 回退，注明由 Codex CLI 内部完成，项目未外层重跑。
6. 精确提交哈希。
7. 未触碰 RunningHub MiniMax H3 与其他媒体能力。
