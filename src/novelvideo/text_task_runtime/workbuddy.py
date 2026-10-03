"""Structured text tasks through WorkBuddy's bundled CLI, without agent tools."""
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from novelvideo.knowledge_runtime.codex import build_codex_process_env, validate_structured_images, _process_group_kwargs
from novelvideo.knowledge_runtime.codex_process import terminate_process_tree
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError


_FAILURE_HINTS = (
    ('credits exhausted',
     'WorkBuddy 账号额度已耗尽（429 Credits exhausted），请充值或改用其他文本任务运行时。'),
    ('service info not found',
     'WorkBuddy 拒绝了该模型 ID（400 service info not found）。该 ID 不在当前 CLI 账号的可用列表中；'
     'CLI 账号与 WorkBuddy 桌面端不是同一份模型清单，请改用 default-model 或在 ~/.codebuddy/models.json '
     '写入账号真实可用的 availableModels。'),
    ('reasoning effort',
     '该模型不支持当前推理强度（--effort），请调低推理强度或改用其他模型。'),
    ('too many requests',
     'WorkBuddy 限流（429），请稍后重试。'),
)


def _cli_failure_hint(stderr):
    """Classify the CLI's stderr into an actionable hint without echoing it wholesale."""

    text = (stderr or b'').decode('utf-8', 'replace').lower()
    for needle, hint in _FAILURE_HINTS:
        if needle in text:
            return hint
    return ''


def _json_payload(value):
    """Return the JSON payload from a reply that may wrap it in prose.

    The CLI's models often explain themselves first and only then emit a fenced
    ```json block, so validating the whole message as JSON fails even though a
    perfectly good payload is present.
    """

    text = value.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    candidates = [text]
    candidates.extend(block.strip() for block in re.findall(r'```(?:json)?\s*(.*?)```', value, re.S))
    span = _last_balanced_object(text)
    if span:
        candidates.append(span)
    for candidate in candidates:
        try:
            json.loads(candidate)
            return candidate
        except (ValueError, TypeError):
            continue
    return text


def _last_balanced_object(text):
    """Best-effort extraction of the last top-level ``{...}`` span."""

    end = text.rfind('}')
    if end < 0:
        return None
    depth = 0
    for index in range(end, -1, -1):
        char = text[index]
        if char == '}':
            depth += 1
        elif char == '{':
            depth -= 1
            if depth == 0:
                return text[index:end + 1]
    return None


def workbuddy_command():
    explicit = os.getenv('WORKBUDDY_BIN')
    candidates = [explicit] if explicit else [
        shutil.which('workbuddy'),
        shutil.which('codebuddy'),
        '/Applications/WorkBuddy AI.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy',
        str(Path.home() / 'Applications/WorkBuddy AI.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy'),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise KnowledgeRuntimeError('未找到 WorkBuddy CLI，请安装 WorkBuddy 或设置 WORKBUDDY_BIN。', code='WORKBUDDY_NOT_INSTALLED')


class WorkBuddyStructuredRuntime:
    def __init__(self, snapshot):
        if snapshot.runtime != 'workbuddy':
            raise ValueError('WorkBuddy runtime requires a workbuddy route')
        if snapshot.skill_id or snapshot.skill_version:
            raise ValueError('WorkBuddy routes do not support skill overrides')
        self.snapshot = snapshot

    async def run_structured(self, *, prompt, output_type, system_prompt='', validation_context=None, images=None):
        images = list(images or [])
        validate_structured_images(images)
        schema = None if output_type is str else output_type.model_json_schema()
        argv = [workbuddy_command(), '--print', '--output-format', 'stream-json' if images else 'json', '--tools', '',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--setting-sources', 'user', '--max-turns', '3', '--model', self.snapshot.model]
        if images:
            argv.extend(['--input-format', 'stream-json'])
        # The bundled CLI implements --json-schema through a StructuredOutput
        # agent tool. Keep tools disabled and validate prompted JSON ourselves.
        if self.snapshot.reasoning_effort and self.snapshot.reasoning_effort != 'none':
            argv.extend(['--effort', self.snapshot.reasoning_effort])
        request = f'{system_prompt}\n\n{prompt}'
        if schema:
            request += '\nReturn only JSON matching this schema:\n' + json.dumps(schema, ensure_ascii=False)
        if images:
            content = [{'type': 'text', 'text': request}]
            content.extend({
                'type': 'image',
                'source': {'type': 'base64', 'media_type': image.media_type,
                           'data': base64.b64encode(image.data).decode('ascii')},
            } for image in images)
            request = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content}},
                                 ensure_ascii=False) + '\n'
        timeout = int(os.getenv('WORKBUDDY_EXEC_TIMEOUT_SECONDS', '600'))
        if timeout <= 0:
            raise ValueError('WorkBuddy timeout must be positive')
        with tempfile.TemporaryDirectory(prefix='nuomi-workbuddy-') as cwd:
            from novelvideo.shared.runtime_env import edition
            env = build_codex_process_env()
            if edition() == 'team':
                from novelvideo.team.execution import prepare_cli
                argv, env = prepare_cli(argv, Path(cwd), runtime='workbuddy')
            # The Node CLI calls process.exit() while stdout writes to a *pipe*
            # are still pending, so anything past the 64 KiB pipe buffer is lost
            # and a large JSON envelope arrives truncated (json.loads then fails
            # and the error is misreported as an invalid structured result).
            # Pointing stdout at a file makes the CLI's writes synchronous, so
            # the payload is emitted in full. Verified: same request yields
            # 65536 truncated bytes through a pipe vs 81910 complete bytes via file.
            stdout_path = Path(cwd) / 'workbuddy-stdout.json'
            with stdout_path.open('wb') as stdout_sink:
                try:
                    process = await asyncio.create_subprocess_exec(*argv, cwd=cwd,
                        stdin=asyncio.subprocess.PIPE, stdout=stdout_sink,
                        stderr=asyncio.subprocess.PIPE, env=env,
                        **_process_group_kwargs())
                except OSError:
                    raise KnowledgeRuntimeError('WorkBuddy 无法启动，请检查 CLI 和 Node.js 安装。', code='WORKBUDDY_START_FAILED') from None
                try:
                    communicated_stdout, _stderr = await asyncio.wait_for(
                        process.communicate(request.encode()), timeout=timeout)
                except BaseException:
                    await terminate_process_tree(process)
                    raise
            stdout = stdout_path.read_bytes() or communicated_stdout
        if process.returncode != 0:
            raise KnowledgeRuntimeError('WorkBuddy 执行失败，请检查其登录状态、模型权限及余额。', code='WORKBUDDY_EXEC_FAILED')
        # The bundled CLI exits 0 even when the cloud rejects the request (for
        # example `400 model [Hy3] service info not found`), printing the error
        # to stderr and nothing to stdout. Surface that as a distinct, actionable
        # failure instead of mislabeling it as a schema/format problem.
        if not stdout or not stdout.strip():
            hint = _cli_failure_hint(_stderr)
            raise KnowledgeRuntimeError(
                'WorkBuddy 进程正常退出但未返回任何输出。这通常不是结构化输出解析问题，'
                '而是模型 ID、账号状态或额度问题；请到“文本任务路由”面板检查后重试。'
                + (f'（{hint}）' if hint else ''),
                code='WORKBUDDY_OUTPUT_EMPTY',
            )
        try:
            if images:
                envelope = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            else:
                envelope = json.loads(stdout)
            if isinstance(envelope, list):
                envelope = next((item for item in reversed(envelope)
                                 if isinstance(item, dict) and item.get('type') == 'result'), None)
            if not isinstance(envelope, dict) or envelope.get('is_error'):
                raise ValueError('WorkBuddy returned an error')
            value = envelope.get('structured_output', envelope.get('result'))
            if output_type is str:
                if not isinstance(value, str):
                    raise ValueError('Expected text')
                return value
            if isinstance(value, str):
                return output_type.model_validate_json(_json_payload(value), context=validation_context)
            return output_type.model_validate(value, context=validation_context)
        except (ValueError, TypeError):
            raise KnowledgeRuntimeError('WorkBuddy 未返回符合要求的结构化结果。', code='WORKBUDDY_OUTPUT_INVALID') from None
