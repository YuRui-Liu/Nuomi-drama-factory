"""Structured text tasks through the DeepSeek Harness CLI, headless profile.

``dsh --profile headless "<task>"`` is the only supported entry point: stdout is
the final assistant text, stderr is the reasoning trace, and exit code 0 means
the run completed. Headless accepts exactly one option (``task``): it has no
model flag, no reasoning-effort flag, no image channel and no JSON output mode.
Model and reasoning effort therefore travel in ``$DSH_HOME/settings.yaml``, and
structured output is requested by appending a JSON Schema to the prompt and
parsing stdout ourselves -- the same shape ``workbuddy.py`` uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
import re
import shutil
import tempfile

from pydantic_ai import Agent, BinaryContent, PromptedOutput

from novelvideo.knowledge_runtime.codex import (
    build_codex_process_env,
    validate_structured_images,
    _process_group_kwargs,
)
from novelvideo.knowledge_runtime.codex_process import terminate_process_tree
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
from novelvideo.text_task_runtime.settings import load_global_routes, runtime_preset_for

# The provider route is named ``deepseek-official`` (not ``deepseek``): the
# value must match dsh's built-in route name verbatim or headless cannot start.
DSH_PROVIDER = "deepseek-official"
DSH_RUNTIME = "deepseek_harness"

# dsh refuses to start when the route's reasoning effort is not implemented by the
# selected model (``dsh: UNSUPPORTED_REASONING_EFFORT: ...``). The effort is an
# optimisation, so the runtime retries once without it.
_UNSUPPORTED_EFFORT_CODE = "UNSUPPORTED_REASONING_EFFORT"

_LOG = logging.getLogger(__name__)

# dsh 的结构化错误行：``dsh: MISSING_CREDENTIAL: no API key for ...``。
# 只认大写错误码，因为 stderr 同时承载推理流（``dsh: reasoning: ...``）等自由文本。
_DSH_ERROR_LINE = re.compile(r"^dsh:\s+([A-Z][A-Z0-9_]*):\s*(.+)$", re.MULTILINE)

# 单条错误消息进入异常前的上限：dsh 偶尔会吐一整行很长的上下文。
_DSH_ERROR_MESSAGE_LIMIT = 300


def _dsh_error_from_stderr(stderr: bytes | str | None) -> tuple[str, str] | None:
    """提取 dsh 自己报出的最后一条结构化错误，返回 ``(code, message)``。

    stderr 上不只跑错误：dsh 把模型的**推理流**也流式写在那里
    （``dsh: reasoning:`` 标题下），推理流可能很长，也可能引用用户的小说内容。
    因此这里只接受 ``dsh: <大写错误码>: <message>`` 形态的行，其余字节一律丢弃，
    异常消息里永远不会出现原始 stdout/stderr 或推理流。

    取**最后**一条：一个进程可能先后报多个错误，最后一条才是退出原因。
    消息经空白折叠与长度截断，避免多行内容撑爆日志。
    """

    if not stderr:
        return None
    text = (
        stderr.decode("utf-8", errors="replace")
        if isinstance(stderr, (bytes, bytearray))
        else stderr
    )
    matches = _DSH_ERROR_LINE.finditer(text)
    last: tuple[str, str] | None = None
    for match in matches:
        last = (match.group(1), match.group(2))
    if last is None:
        return None
    code, raw_message = last
    # 折叠内部连续空白/换行，去掉首尾空白：多行错误会被压成单行。
    message = " ".join(raw_message.split())
    if len(message) > _DSH_ERROR_MESSAGE_LIMIT:
        message = message[: _DSH_ERROR_MESSAGE_LIMIT - 1].rstrip() + "…"
    return code, message


def dsh_command() -> str:
    """定位 dsh 可执行文件。优先 DSH_BIN，其次 PATH。"""

    candidates = [os.getenv("DSH_BIN"), shutil.which("dsh")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise KnowledgeRuntimeError(
        "未找到 DeepSeek Harness CLI（dsh），请安装 DeepSeek Harness 或设置 DSH_BIN。",
        code="DSH_NOT_INSTALLED",
    )


def harness_home() -> Path:
    """Nuomi 专属 harness home。

    优先 NOVELVIDEO_DSH_HOME，否则 config.STATE_DIR / "dsh"。

    Deliberately never ``~/.dsh``: that directory belongs to the user's own
    interactive dsh sessions, and rewriting its ``settings.yaml`` would silently
    switch the model of their foreground session.
    """

    explicit = str(os.getenv("NOVELVIDEO_DSH_HOME") or "").strip()
    if explicit:
        # docker env-file / compose ``environment:`` 不会像 shell 那样展开 ``~``，
        # 不展开就会在当前进程 cwd 下创建名为 ``~`` 的目录。
        return Path(explicit).expanduser()
    from novelvideo import config

    return Path(config.STATE_DIR) / "dsh"


def _settings_scalar(value: str) -> str:
    """Render one YAML scalar, quoting only when a plain scalar would be unsafe.

    防御性处理：本函数的输入已被上游约束 —— ``model`` 只有通过
    ``validate_text_task_model_name``（首字符字母数字，其余仅限字母数字、
    ``._:/-`` 与空格）才可能到达，``reasoning_effort`` 取自 ``Literal``。
    因此 ``#``、换行、空串、首尾空白等会破坏 YAML 文档的值在调用路径上不可达。
    这里的引号分支只为防止未来出现未经校验的新调用方。
    """

    if value != value.strip() or re.search(r":\s", value):
        return json.dumps(value, ensure_ascii=False)
    return value


# dsh boots a *coding agent*: the headless profile mounts bash/fs/subagent tools
# and a coding persona. Nuomi only ever asks it for structured JSON, so those
# tools are pure risk — the H3 episode prompt optimizer spent 52 bash, 26 grep
# and 22 read calls over 598s exploring the repository instead of answering, was
# killed by DSH_EXEC_TIMEOUT_SECONDS, and the group then failed with "produced no
# plan for segment(s) ...". WorkBuddy already passes `--tools ''` for the same
# reason; this patch is the dsh equivalent.
DSH_TOOL_FREE_PATCH = """\
# Written by Nuomi. Disables every model-facing tool so a structured text task
# cannot turn into an agentic exploration loop. Regenerated on each call.
- id: tool-bash
  disabled: true
- id: tool-pwsh
  disabled: true
- id: tool-jobs
  disabled: true
- id: tool-fs
  disabled: true
- id: tool-fs-search
  disabled: true
- id: tool-skill
  disabled: true
- id: tool-subagent
  disabled: true
- id: tool-subagent-fork
  disabled: true
- id: tool-subagent-list-agents
  disabled: true
- id: tool-subagent-control
  disabled: true
- id: tool-workflow
  disabled: true
- id: tool-todo
  disabled: true
- id: tool-goal
  disabled: true
- id: tool-ralph
  disabled: true
- id: tool-web
  disabled: true
"""

DSH_TOOL_FREE_PATCH_NAME = "nuomi-structured-tools-off.patch.yml"


def _write_tool_free_patch(home: Path) -> Path:
    """Atomically materialise the tool-free overlay consumed by ``--patch``."""

    home.mkdir(parents=True, exist_ok=True)
    target = home / DSH_TOOL_FREE_PATCH_NAME
    if target.is_file():
        try:
            if target.read_text(encoding="utf-8") == DSH_TOOL_FREE_PATCH:
                return target
        except OSError:
            pass
    handle, temporary = tempfile.mkstemp(
        prefix="tools-off-", suffix=".yaml.tmp", dir=str(home)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(DSH_TOOL_FREE_PATCH)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def _write_settings_document(home: Path, *, model: str, effort: str | None) -> Path:
    """原子写入 ``$DSH_HOME/settings.yaml``，声明 agent 默认模型。

    dsh 的 settings watcher 会实时读取这个文件；先写临时文件再 ``os.replace``
    可以保证它永远看不到写了一半的文档。
    """

    lines = [
        "agent-default-model:",
        f"  provider: {DSH_PROVIDER}",
        f"  model: {_settings_scalar(model)}",
    ]
    if effort is not None:
        lines.append(f"  reasoningEffort: {_settings_scalar(effort)}")
    document = "\n".join(lines) + "\n"
    home.mkdir(parents=True, exist_ok=True)
    target = home / "settings.yaml"
    handle, temporary = tempfile.mkstemp(
        prefix="settings-", suffix=".yaml.tmp", dir=str(home)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def _resolve_default_model(snapshot: object) -> tuple[str, str | None]:
    """按「调用时」的运行时级 preset 决定 model / reasoningEffort。

    入队时的 clamp 只保证「同一批快照」的统一。若任务 A 在 preset=X 时入队、
    用户把 preset 改成 Y 后任务 B 才入队，两者快照不同；此时若按
    ``snapshot.model`` 写 settings.yaml，并发运行的两个调用会互相覆盖同一个
    文件，重新引入模型串用。改为调用时取当前 preset，则消除的是「入队时点差异」
    导致的竞争（preset 恰好在两次 ``load_global_routes()`` 之间变更的极小窗口
    除外）。``except ValueError`` 用于容纳存储异常时回落到快照值（快照本身已在
    入队时被 clamp）。
    """

    try:
        preset = runtime_preset_for(load_global_routes(), DSH_RUNTIME)
    except ValueError:
        preset = None
    if preset is None:
        return snapshot.model, snapshot.reasoning_effort  # type: ignore[attr-defined]
    return preset.model, preset.reasoning_effort


def _vision_model(model_name: str, *, timeout_seconds: int):
    """Use the selected DeepSeek model's official image API for visual tasks.

    Headless accepts a text task only; its stdout contract cannot confirm that
    an agent opened every image. A direct image request keeps the model choice
    while making the supplied image bytes part of the user message.
    """
    from novelvideo import config

    api_key = str(os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise KnowledgeRuntimeError(
            "DeepSeek 图片任务需要配置 DEEPSEEK_API_KEY。",
            code="DSH_VISION_KEY_MISSING",
        )
    return config._newapi_text_openai_model(
        model_name,
        api_key=api_key,
        base_url="https://api.deepseek.com",
        timeout_seconds=timeout_seconds,
        profile=None,
    )


class DeepSeekHarnessStructuredRuntime:
    """Use DSH for text and the selected DeepSeek model's API for images."""

    def __init__(self, snapshot):
        if snapshot.runtime != DSH_RUNTIME:
            raise ValueError("DeepSeek Harness runtime requires a deepseek_harness route")
        if snapshot.skill_id or snapshot.skill_version:
            raise ValueError("DeepSeek Harness routes do not support skill overrides")
        self.snapshot = snapshot

    async def run_structured(
        self,
        *,
        prompt,
        output_type,
        system_prompt="",
        validation_context=None,
        images=None,
    ):
        timeout = int(os.getenv("DSH_EXEC_TIMEOUT_SECONDS", "600"))
        if timeout <= 0:
            raise ValueError("DeepSeek Harness timeout must be positive")
        if images:
            validate_structured_images(images)
            model, effort = _resolve_default_model(self.snapshot)
            agent_kwargs = {
                "model": _vision_model(model, timeout_seconds=timeout),
                "output_type": PromptedOutput(output_type) if output_type is not str else str,
                "system_prompt": system_prompt,
            }
            if effort in {"none", "low", "high", "max"}:
                agent_kwargs["model_settings"] = {"openai_reasoning_effort": effort}
            if validation_context is not None:
                agent_kwargs["validation_context"] = validation_context
            user_prompt = [
                prompt,
                *[BinaryContent(data=image.data, media_type=image.media_type)
                  for image in images],
            ]
            result = await Agent(**agent_kwargs).run(user_prompt)
            output = getattr(result, "output", result)
            return output if isinstance(output, output_type) else output_type.model_validate(output)
        model, effort = _resolve_default_model(self.snapshot)
        home = harness_home()
        try:
            return await self._run_once(
                home=home,
                model=model,
                effort=effort,
                timeout=timeout,
                prompt=prompt,
                output_type=output_type,
                system_prompt=system_prompt,
                validation_context=validation_context,
            )
        except KnowledgeRuntimeError as exc:
            # A reasoning effort the routed model does not implement is a
            # configuration mistake, not a broken task: dsh exits before doing
            # any work. Retry once without the effort so one unsupported preset
            # cannot take down every text task in the pipeline.
            if (
                getattr(exc, "dsh_code", "") != _UNSUPPORTED_EFFORT_CODE
                or effort is None
            ):
                raise
            _LOG.warning(
                "dsh rejected reasoning effort %r for model %r; retrying with the "
                "model default",
                effort,
                model,
            )
            return await self._run_once(
                home=home,
                model=model,
                effort=None,
                timeout=timeout,
                prompt=prompt,
                output_type=output_type,
                system_prompt=system_prompt,
                validation_context=validation_context,
            )

    async def _run_once(
        self,
        *,
        home: Path,
        model: str,
        effort: str | None,
        timeout: int,
        prompt,
        output_type,
        system_prompt: str,
        validation_context,
    ):
        try:
            _write_settings_document(home, model=model, effort=effort)
            tool_free_patch = _write_tool_free_patch(home)
        except OSError:
            raise KnowledgeRuntimeError(
                "DeepSeek Harness 无法准备 DSH_HOME，请检查目录写入权限。",
                code="DSH_START_FAILED",
            ) from None
        schema = None if output_type is str else output_type.model_json_schema()
        request = f"{system_prompt}\n\n{prompt}"
        if schema:
            request += "\nReturn only JSON matching this schema:\n" + json.dumps(
                schema, ensure_ascii=False
            )
        argv = [
            dsh_command(),
            "--profile",
            "headless",
            "--patch",
            str(tool_free_patch),
            request,
        ]
        env = build_codex_process_env(environ={**os.environ, "DSH_HOME": str(home)})
        with tempfile.TemporaryDirectory(prefix="nuomi-dsh-") as cwd:
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=cwd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    **_process_group_kwargs(),
                )
            except OSError:
                raise KnowledgeRuntimeError(
                    "DeepSeek Harness 无法启动，请检查 dsh 安装与运行环境。",
                    code="DSH_START_FAILED",
                ) from None
            try:
                # headless 把任务放在 argv 里，stdin 只被关闭（communicate 无输入）
                # 以避免继承到交互式终端的输入。
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except TimeoutError:
                # Keep the TimeoutError type: callers classify OSError (which
                # TimeoutError subclasses) as a non-blocking quality failure on
                # purpose. Carry the budget in the message so the downstream
                # "optimizer failure: ..." diagnostic names the real limit.
                await terminate_process_tree(process)
                raise TimeoutError(
                    f"DeepSeek Harness 调用超时（{timeout} 秒），模型未在预算内返回；"
                    "如属正常的长任务请调大 DSH_EXEC_TIMEOUT_SECONDS。"
                ) from None
            except BaseException:
                await terminate_process_tree(process)
                raise
        if process.returncode != 0:
            # 失败时唯一的诊断证据就在 stderr 上；丢掉它会让用户被含糊的提示
            # 引向「余额」之类的错误方向。这里只带出 dsh 自己的结构化错误行，
            # 绝不放原始输出或推理流。
            extracted = _dsh_error_from_stderr(stderr)
            if extracted is None:
                detail = "（未能从 dsh 输出中解析出具体原因）"
                dsh_code = ""
            else:
                dsh_code, message = extracted
                detail = f"（dsh 报错 {dsh_code}: {message}）"
            error = KnowledgeRuntimeError(
                f"DeepSeek Harness 执行失败，退出码 {process.returncode}{detail}。"
                "请确认后端进程可访问凭据：设置 DEEPSEEK_API_KEY，"
                "或将其写入 Nuomi 专属 $DSH_HOME 的 .credentials.yaml。",
                code="DSH_EXEC_FAILED",
            )
            # The caller needs dsh's own code to distinguish a rejected option
            # (retryable without that option) from a real execution failure.
            error.dsh_code = dsh_code
            raise error
        try:
            text = stdout.decode("utf-8").strip()
            if output_type is str:
                # 纯文本是模型的最终答案，可能本身就是一个有意的 markdown 围栏，
                # 不能在这里剥离（workbuddy.py 同样只在结构化分支剥离）。
                return text
            if text.startswith("```") and text.endswith("```") and "\n" in text:
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return output_type.model_validate_json(text, context=validation_context)
        except (ValueError, TypeError) as exc:
            detail = _validation_detail(exc)
            raise KnowledgeRuntimeError(
                "DeepSeek Harness 未返回符合要求的结构化结果。"
                + (f"（{detail}）" if detail else ""),
                code="DSH_OUTPUT_INVALID",
            ) from None


def _validation_detail(exc: BaseException) -> str:
    """Structural schema detail (field + rule) for a rejected payload.

    Without it the operator sees only "未返回符合要求的结构化结果" and the caller's
    retry can tell the model nothing actionable, so a payload that is valid JSON
    but violates a cross-field rule (``ref2va requires reference_summary``) can
    never be corrected. Only ``loc``/``msg`` are reported: pydantic's ``input``
    and the JSON decoder's snippet echo the model's raw output, which this
    runtime never exposes.
    """

    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return ""
    try:
        items = errors()
    except Exception:
        return ""
    parts: list[str] = []
    for item in items[:5]:
        if not isinstance(item, Mapping):
            continue
        location = ".".join(str(part) for part in item.get("loc") or ())
        message = " ".join(str(item.get("msg") or "").split())
        parts.append(f"{location}: {message}".strip(": ") if location else message)
    return "；".join(part for part in parts if part)[:400]
