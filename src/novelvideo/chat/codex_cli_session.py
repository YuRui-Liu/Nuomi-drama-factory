"""Native Codex CLI sessions for installations without the optional Python SDK."""
import asyncio
import json
import os
import shutil

from .backend_sdk import ChatBackendEvent
from .session_manager import assert_current
from novelvideo.knowledge_runtime.codex_process import terminate_process_tree


class CodexCliSession:
    def __init__(self, *, codex_bin, cwd, env, model, config_overrides=(), thread_id=None):
        self.binary = str(codex_bin or shutil.which('codex') or '')
        self.cwd, self.env, self.model = cwd, env, model
        self.overrides = config_overrides
        self.id = thread_id

    def argv(self):
        args = [self.binary, 'exec', '--sandbox', 'read-only', '--skip-git-repo-check']
        if self.id:
            args += ['resume', self.id]
        args += ['--json', '--model', self.model]
        for value in self.overrides:
            args += ['-c', value]
        return args + ['-']

    async def stream(self, prompt):
        assert_current()
        process = await asyncio.create_subprocess_exec(
            *self.argv(), cwd=self.cwd, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=os.name != 'nt', limit=4 * 1024 * 1024,
        )
        async def drain_errors():
            tail = b''
            while chunk := await process.stderr.read(8192):
                tail = (tail + chunk)[-8192:]
            return tail.decode(errors='replace')
        errors = asyncio.create_task(drain_errors())
        complete = False
        text = ''
        last_error = ''
        try:
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
            while line := await process.stdout.readline():
                assert_current()
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.get('type')
                if kind == 'thread.started':
                    reported = str(event.get('thread_id') or '')
                    if not reported or (self.id and self.id != reported):
                        raise RuntimeError('Codex 返回了不同会话；请重试恢复或手动清除会话。')
                    self.id = reported
                    yield ChatBackendEvent(type='thread_started', thread_id=self.id)
                elif kind == 'item.completed':
                    item = event.get('item') or {}
                    if item.get('type') == 'agent_message':
                        text += str(item.get('text') or '')
                        yield ChatBackendEvent(type='assistant_delta', thread_id=self.id, text=text)
                    elif item.get('type') in {'mcp_tool_call', 'command_execution'}:
                        yield ChatBackendEvent(type='tool_update', thread_id=self.id, text=str(item.get('tool') or item.get('command') or '工具执行完成'))
                elif kind == 'error':
                    # The CLI also emits this while retrying transport errors.
                    # Preserve the same process/session until it reports failure.
                    last_error = str(event.get('message') or '')
                elif kind == 'turn.failed':
                    error = event.get('error') or event
                    raise RuntimeError(str(error.get('message') or 'Codex 会话执行失败，请重试。'))
                elif kind == 'turn.completed':
                    complete = True
            code = await process.wait()
            diagnostic = await errors
            if code or not complete or not self.id:
                raise RuntimeError(f'Codex 会话执行失败（{code}）：{last_error or diagnostic[-1500:]}')
            yield ChatBackendEvent(type='complete', thread_id=self.id, text=text)
        finally:
            if process.returncode is None:
                async def drain_output():
                    while await process.stdout.read(8192):
                        pass
                drain = asyncio.create_task(drain_output())
                try:
                    await terminate_process_tree(process)
                finally:
                    await drain
            await errors
