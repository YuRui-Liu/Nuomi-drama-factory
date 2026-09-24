"""Structured text tasks through WorkBuddy's bundled CLI, without agent tools."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile

from novelvideo.knowledge_runtime.codex import build_codex_process_env, _process_group_kwargs
from novelvideo.knowledge_runtime.codex_process import terminate_process_tree
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError


def workbuddy_command():
    explicit = os.getenv('WORKBUDDY_BIN')
    candidates = [explicit] if explicit else [
        shutil.which('workbuddy'),
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
        if images:
            raise KnowledgeRuntimeError('此 WorkBuddy 文本运行时不支持图片输入；视觉检查请使用 Codex 或模型 API。', code='WORKBUDDY_IMAGES_UNSUPPORTED')
        schema = None if output_type is str else output_type.model_json_schema()
        argv = [workbuddy_command(), '--print', '--output-format', 'json', '--tools', '',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--setting-sources', 'user', '--max-turns', '3', '--model', self.snapshot.model]
        # The bundled CLI implements --json-schema through a StructuredOutput
        # agent tool. Keep tools disabled and validate prompted JSON ourselves.
        if self.snapshot.reasoning_effort and self.snapshot.reasoning_effort != 'none':
            argv.extend(['--effort', self.snapshot.reasoning_effort])
        request = f'{system_prompt}\n\n{prompt}'
        if schema:
            request += '\nReturn only JSON matching this schema:\n' + json.dumps(schema, ensure_ascii=False)
        timeout = int(os.getenv('WORKBUDDY_EXEC_TIMEOUT_SECONDS', '600'))
        if timeout <= 0:
            raise ValueError('WorkBuddy timeout must be positive')
        with tempfile.TemporaryDirectory(prefix='nuomi-workbuddy-') as cwd:
            try:
                process = await asyncio.create_subprocess_exec(*argv, cwd=cwd,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=build_codex_process_env(), **_process_group_kwargs())
            except OSError:
                raise KnowledgeRuntimeError('WorkBuddy 无法启动，请检查 CLI 和 Node.js 安装。', code='WORKBUDDY_START_FAILED') from None
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(request.encode()), timeout=timeout)
            except BaseException:
                await terminate_process_tree(process)
                raise
        if process.returncode != 0:
            raise KnowledgeRuntimeError('WorkBuddy 执行失败，请检查其登录状态、模型权限及余额。', code='WORKBUDDY_EXEC_FAILED')
        try:
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
                value = value.strip()
                if value.startswith('```') and value.endswith('```'):
                    value = value.split('\n', 1)[1].rsplit('```', 1)[0]
                return output_type.model_validate_json(value, context=validation_context)
            return output_type.model_validate(value, context=validation_context)
        except (ValueError, TypeError):
            raise KnowledgeRuntimeError('WorkBuddy 未返回符合要求的结构化结果。', code='WORKBUDDY_OUTPUT_INVALID') from None
