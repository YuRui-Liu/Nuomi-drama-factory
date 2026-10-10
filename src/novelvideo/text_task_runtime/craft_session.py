"""A screenplay author's private conversation, independent of QC runtimes."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID
from contextlib import asynccontextmanager
import portalocker

from novelvideo.knowledge_runtime import codex
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
from .runtime import CodexStructuredRuntime, RoutedCodexCliStructuredBackend


def _session_uuid(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise KnowledgeRuntimeError(
            "Author session must be an exact UUID.", code="CODEX_AUTHOR_SESSION_INVALID"
        ) from exc


class _AuthorBackend(RoutedCodexCliStructuredBackend):
    def __init__(self, backend, session_id, on_session, session_dir=None):
        # Preserve the frozen route, executable and provider cost capture.
        self.__dict__.update(backend.__dict__)
        self.session_id = _session_uuid(session_id) if session_id else None
        self.on_session = on_session
        self._session_persisted = bool(session_id)
        self._scratch = None
        if session_dir is None:
            self._scratch = tempfile.TemporaryDirectory(prefix="dramaclaw-author-")
            self._session_dir = Path(self._scratch.name)
        else:
            # The orchestrator supplies a server-generated, project-scoped
            # directory. Never derive this filesystem location from a CLI UUID.
            self._session_dir = Path(session_dir)
            self._session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._session_dir.chmod(0o700)
            self._session_dir = self._session_dir.resolve()

    async def persist_session(self):
        if self.session_id and not self._session_persisted:
            if self.on_session:
                await self.on_session(self.session_id)
            self._session_persisted = True

    @asynccontextmanager
    async def locked(self):
        # Cross-worker lock; a cancelled waiter must not later acquire a lock
        # in a background thread and leak it.
        with (self._session_dir/'.author.lock').open('a+b') as handle:
            while True:
                try:
                    portalocker.lock(handle,portalocker.LOCK_EX|portalocker.LOCK_NB)
                    break
                except portalocker.exceptions.LockException:
                    await asyncio.sleep(.05)
            try:
                identity_path = self._session_dir/'.session.json'
                if identity_path.exists():
                    value = _session_uuid(json.loads(identity_path.read_text())['id'])
                    if self.session_id and self.session_id != value:
                        raise KnowledgeRuntimeError('Author directory belongs to a different session.',code='CODEX_AUTHOR_SESSION_MISMATCH')
                    self.session_id = value
                await self.persist_session()
                yield
            finally:
                portalocker.unlock(handle)

    def build_argv(self, *, cwd, output_path, schema_path):
        argv = super().build_argv(cwd=cwd, output_path=output_path, schema_path=schema_path)
        argv.remove("--ephemeral")
        argv[-1:-1] = ["--json"]
        if self.session_id:
            # sandbox, color and cwd belong to exec, not its resume subcommand.
            # Resume's model flag explicitly overrides a session's old model.
            resume = ["resume", self.session_id, "--model", self.model,
                      "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                      "--json", "--output-last-message", output_path]
            if schema_path:
                resume.extend(["--output-schema", schema_path])
            if self.reasoning_effort:
                resume.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
            argv[-1:-1] = resume
        return argv

    async def _record_session(self, stdout):
        found = None
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "thread.started":
                candidate = _session_uuid(event.get("thread_id"))
                if found is not None and candidate != found:
                    raise KnowledgeRuntimeError("Multiple author sessions reported.", code="CODEX_AUTHOR_SESSION_MISMATCH")
                found = candidate
        if found is None:
            raise KnowledgeRuntimeError("Codex did not identify the author session.", code="CODEX_AUTHOR_SESSION_UNAVAILABLE")
        if self.session_id is not None and found != self.session_id:
            raise KnowledgeRuntimeError("Codex resumed a different author session.", code="CODEX_AUTHOR_SESSION_MISMATCH")
        if self.session_id is None:
            self.session_id = found
        pending = self._session_dir/'.session.pending'
        pending.write_text(json.dumps({'id':found}),encoding='utf-8')
        pending.chmod(0o600)
        pending.replace(self._session_dir/'.session.json')
        await self.persist_session()

    async def _run_once(self, prompt, *, schema, images=None):
        timeout = codex.parse_exec_timeout_seconds(os.getenv("CODEX_EXEC_TIMEOUT_SECONDS"))
        # Keep the isolated CODEX_HOME alive across turns, without exposing the
        # administrator home or another author's session to team workers.
        root = self._session_dir
        with tempfile.TemporaryDirectory(prefix="turn-", dir=root) as raw_dir:
            temp_dir = Path(raw_dir)
            output_path = temp_dir / "result.txt"
            schema_path = None
            if schema is not None:
                schema_path = temp_dir / "schema.json"
                schema_path.write_text(json.dumps(codex.normalize_codex_output_schema(schema), ensure_ascii=False), encoding="utf-8")
            argv = self.build_argv(cwd=str(root), output_path=str(output_path), schema_path=str(schema_path) if schema_path else None)
            if images:
                codex.validate_structured_images(images)
                for index, image in enumerate(images):
                    path = temp_dir / f"image-{index}{codex._IMAGE_SUFFIXES[image.media_type]}"
                    path.write_bytes(image.data)
                    argv[-1:-1] = ["--image", str(path)]
            argv = codex.normalize_codex_process_argv(argv)
            options = {}
            from novelvideo.shared.runtime_env import edition
            if edition() == "team":
                from novelvideo.team.execution import prepare_cli
                argv, env = prepare_cli(argv, root, runtime="codex")
                options = {"env": env, "cwd": str(root)}
            attempt = self._cost_capture.prepare(model=self.model, media_type="text", fresh=True)
            try:
                process = await codex._create_codex_process(argv, stdin=asyncio.subprocess.PIPE, **options)
            except OSError as exc:
                if attempt is not None:
                    self._cost_capture._after_send(lambda: self._cost_capture.service.failed_before_send(attempt.attempt_id))
                raise KnowledgeRuntimeError(f"Codex CLI 无法启动: {exc}", code="CODEX_NOT_INSTALLED") from exc
            external_id = attempt.attempt_id if attempt is not None else ""
            self._cost_capture.accepted(attempt, external_id)
            try:
                outcome = await codex.supervise_codex_process(process, prompt, output_path, timeout)
            except BaseException:
                self._cost_capture.observe(external_id, "unknown")
                raise
            success = outcome.completed_from_final_message or outcome.returncode == 0
            self._cost_capture.observe(external_id, "succeeded" if success else "failed")
            if not success:
                if b'thread.started' in outcome.stdout:
                    await self._record_session(outcome.stdout)
                message = codex._codex_error_summary(outcome.stderr, outcome.stdout)
                lowered = message.lower()
                code = "CODEX_EXEC_FAILED"
                if "not logged in" in lowered:
                    code = "CODEX_NOT_AUTHENTICATED"
                elif "invalid_json_schema" in lowered:
                    code = "CODEX_SCHEMA_INVALID"
                elif self.session_id and any(word in lowered for word in ("session", "thread", "rollout")):
                    code = "CODEX_AUTHOR_SESSION_UNAVAILABLE"
                raise KnowledgeRuntimeError(message or "Codex 执行失败。", code=code)
            await self._record_session(outcome.stdout)
            return outcome.output


class AuthorRuntime:
    def __init__(self, runtime, backend=None):
        self._runtime = runtime
        self._backend = backend
        self.snapshot = getattr(runtime, "snapshot", None)
        self.mode = "native" if backend is not None else "replay"
        self._lock = asyncio.Lock()
        if hasattr(runtime, "method"):
            self.method = runtime.method

    @property
    def session_id(self):
        return self._backend.session_id if self._backend is not None else None

    async def run_structured(self, **kwargs):
        async with self._lock:
            if self._backend is not None:
                async with self._backend.locked():
                    return await self._runtime.run_structured(**kwargs)
            return await self._runtime.run_structured(**kwargs)


def author_runtime(runtime, session_id=None, on_session=None, session_dir=None):
    """Wrap Codex with native continuity; other runtimes use caller replay.

    ``on_session`` is an async persistence callback, called once before the
    first result is returned. A server-generated ``session_dir`` keeps the
    private team home available across workers; without one it lives only for
    this wrapper's lifetime. Lost sessions fail rather than selecting another.
    """
    from novelvideo.agent_teams.adapters import MethodRuntime
    method_wrapper = runtime if isinstance(runtime, MethodRuntime) else None
    base = method_wrapper._runtime if method_wrapper is not None else runtime
    if not isinstance(base, CodexStructuredRuntime) or not isinstance(base._backend, RoutedCodexCliStructuredBackend):
        return AuthorRuntime(runtime)
    backend = _AuthorBackend(base._backend, session_id, on_session, session_dir)
    wrapped = CodexStructuredRuntime(base.snapshot, backend=backend)
    if method_wrapper is not None:
        wrapped = MethodRuntime(wrapped, method_wrapper.method)
    return AuthorRuntime(wrapped, backend)
