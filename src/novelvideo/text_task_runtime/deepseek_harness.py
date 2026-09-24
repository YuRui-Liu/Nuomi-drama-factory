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
import os
from pathlib import Path
import re
import shutil
import tempfile

from novelvideo.knowledge_runtime.codex import (
    build_codex_process_env,
    _process_group_kwargs,
)
from novelvideo.knowledge_runtime.codex_process import terminate_process_tree
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
from novelvideo.text_task_runtime.settings import load_global_routes, runtime_preset_for

# The provider route is named ``deepseek-official`` (not ``deepseek``): the
# value must match dsh's built-in route name verbatim or headless cannot start.
DSH_PROVIDER = "deepseek-official"
DSH_RUNTIME = "deepseek_harness"


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


class DeepSeekHarnessStructuredRuntime:
    """Run one structured task through ``dsh --profile headless``."""

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
        if images:
            raise KnowledgeRuntimeError(
                "此 DeepSeek Harness 文本运行时不支持图片输入；视觉检查请使用 Codex 或模型 API。",
                code="DSH_IMAGES_UNSUPPORTED",
            )
        timeout = int(os.getenv("DSH_EXEC_TIMEOUT_SECONDS", "600"))
        if timeout <= 0:
            raise ValueError("DeepSeek Harness timeout must be positive")
        model, effort = _resolve_default_model(self.snapshot)
        home = harness_home()
        try:
            _write_settings_document(home, model=model, effort=effort)
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
        argv = [dsh_command(), "--profile", "headless", request]
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
                stdout, _stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except BaseException:
                await terminate_process_tree(process)
                raise
        if process.returncode != 0:
            raise KnowledgeRuntimeError(
                "DeepSeek Harness 执行失败，请检查其登录状态、模型权限及余额。",
                code="DSH_EXEC_FAILED",
            )
        try:
            text = stdout.decode("utf-8").strip()
            if output_type is str:
                # 纯文本是模型的最终答案，可能本身就是一个有意的 markdown 围栏，
                # 不能在这里剥离（workbuddy.py 同样只在结构化分支剥离）。
                return text
            if text.startswith("```") and text.endswith("```") and "\n" in text:
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return output_type.model_validate_json(text, context=validation_context)
        except (ValueError, TypeError):
            raise KnowledgeRuntimeError(
                "DeepSeek Harness 未返回符合要求的结构化结果。",
                code="DSH_OUTPUT_INVALID",
            ) from None
