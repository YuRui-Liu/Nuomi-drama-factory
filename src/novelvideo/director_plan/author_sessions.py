"""Project-scoped durable identities for a director author's revision chain."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from typing import Iterator
from uuid import UUID

import portalocker
from pydantic import BaseModel, ConfigDict

from .store import _atomic_write_json


class AuthorBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    task_kind: str
    chain_id: str
    route_hash: str
    session_id: str | None = None


class AuthorSessionStore:
    def __init__(self, project_dir: Path, *, project_id: str, task_kind: str):
        if not project_id.strip() or not task_kind.strip():
            raise ValueError("author_scope_required")
        self.root = Path(project_dir).resolve() / "director_plans" / "author_sessions"
        self.project_id, self.task_kind = project_id, task_kind

    def session_dir(self, binding: AuthorBinding) -> Path:
        if (binding.project_id, binding.task_kind) != (self.project_id, self.task_kind):
            raise ValueError("author_scope_mismatch")
        key = json.dumps([self.project_id, self.task_kind, binding.chain_id])
        return self.root / hashlib.sha256(key.encode()).hexdigest()

    @contextmanager
    def _guard(self, binding: AuthorBinding) -> Iterator[Path]:
        directory = self.session_dir(binding)
        directory.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(directory / ".binding.lock"), timeout=10):
            yield directory / "binding.json"

    @contextmanager
    def claim(self, binding: AuthorBinding) -> Iterator[None]:
        """Hold across the whole author/QC revision transaction, not one CLI turn."""
        directory = self.session_dir(binding)
        directory.mkdir(parents=True, exist_ok=True)
        handle = (directory / ".chain.lock").open("a+b")
        try:
            try:
                portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.exceptions.LockException as exc:
                raise ValueError("author_chain_busy") from exc
            try:
                yield
            finally:
                portalocker.unlock(handle)
        finally:
            handle.close()

    def bind(self, chain_id: str, *, route: dict) -> AuthorBinding:
        if not chain_id.strip():
            raise ValueError("author_chain_required")
        route_hash = hashlib.sha256(json.dumps(route, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        binding = AuthorBinding(project_id=self.project_id, task_kind=self.task_kind,
                                chain_id=chain_id, route_hash=route_hash)
        with self._guard(binding) as path:
            if path.exists():
                saved = AuthorBinding.model_validate_json(path.read_text(encoding="utf-8"))
                if saved.model_copy(update={"session_id": None}) != binding:
                    raise ValueError("author_scope_or_route_mismatch")
                return saved
            _atomic_write_json(path, binding.model_dump(mode="json"))
            return binding

    def record_session(self, binding: AuthorBinding, session_id: str) -> AuthorBinding:
        session_id = str(UUID(session_id))
        with self._guard(binding) as path:
            saved = AuthorBinding.model_validate_json(path.read_text(encoding="utf-8"))
            if saved.model_copy(update={"session_id": None}) != binding.model_copy(update={"session_id": None}):
                raise ValueError("author_scope_or_route_mismatch")
            if saved.session_id is not None and saved.session_id != session_id:
                raise ValueError("author_session_mismatch")
            updated = saved.model_copy(update={"session_id": session_id})
            _atomic_write_json(path, updated.model_dump(mode="json"))
            return updated

    def wrap_runtime(self, runtime, binding: AuthorBinding):
        """Use the shared native Codex wrapper; never reuse this for QC."""
        from novelvideo.text_task_runtime.craft_session import author_runtime

        async def checkpoint(session_id):
            self.record_session(binding, session_id)

        wrapped = author_runtime(runtime, session_id=binding.session_id,
                                 on_session=checkpoint, session_dir=self.session_dir(binding))
        if wrapped.mode != "native":
            raise ValueError("session_resume_unavailable")
        return wrapped
