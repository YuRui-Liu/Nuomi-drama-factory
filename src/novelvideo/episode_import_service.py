"""Transactional orchestration for versioned episode imports.

The repository owns candidate source files and the canonical ``novel.txt``.
This module deliberately knows only its small protocol so the episode source
store can evolve independently.  Overwrites are built in an isolated Cognee
runtime and only become active after every graph pipeline stage succeeds.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4

import portalocker


logger = logging.getLogger(__name__)


class EpisodeImportGraphError(RuntimeError):
    """An explicitly identified failure in the graph transaction boundary."""


@dataclass(frozen=True, slots=True)
class PreparedEpisodeImport:
    target_revision: int
    canonical_novel: str
    contains_overwrite: bool
    repository_token: object
    has_changes: bool = True
    changed_contents: tuple[str, ...] = ()


class EpisodeImportRepository(Protocol):
    async def prepare_import(self, batch: object) -> PreparedEpisodeImport: ...

    async def commit_prepared(self, prepared: PreparedEpisodeImport) -> None: ...

    async def discard_prepared(self, prepared: PreparedEpisodeImport) -> None: ...


class EpisodeImportGraph(Protocol):
    async def add_incremental(
        self,
        *,
        target_revision: int,
        canonical_novel: str,
        changed_contents: tuple[str, ...],
    ) -> object: ...

    async def build_shadow(
        self, *, target_revision: int, canonical_novel: str
    ) -> object: ...

    async def activate_shadow(self, shadow: object) -> object: ...

    async def restore_active(self, activation: object) -> None: ...

    async def discard_shadow(self, shadow: object) -> None: ...


class EpisodeImportService:
    def __init__(
        self, *, repository: EpisodeImportRepository, graph: EpisodeImportGraph
    ) -> None:
        self._repository = repository
        self._graph = graph

    async def commit(self, batch: object) -> PreparedEpisodeImport:
        async with self._project_commit_lock():
            return await self._commit_locked(batch)

    async def _commit_locked(self, batch: object) -> PreparedEpisodeImport:
        await self.recover()
        prepared = await self._repository.prepare_import(batch)
        shadow: object | None = None
        activation: object | None = None
        try:
            if not prepared.has_changes:
                await self._repository.commit_prepared(prepared)
                return prepared
            if not prepared.contains_overwrite:
                try:
                    activation = await self._graph.add_incremental(
                        target_revision=prepared.target_revision,
                        canonical_novel=prepared.canonical_novel,
                        changed_contents=prepared.changed_contents,
                    )
                except Exception as exc:
                    raise EpisodeImportGraphError(str(exc)) from exc
                try:
                    await self._repository.commit_prepared(prepared)
                except BaseException:
                    await self._graph.restore_active(activation)
                    raise
                await self._finalize_graph_activation(activation)
                return prepared

            try:
                shadow = await self._graph.build_shadow(
                    target_revision=prepared.target_revision,
                    canonical_novel=prepared.canonical_novel,
                )
                activation = await self._graph.activate_shadow(shadow)
            except Exception as exc:
                raise EpisodeImportGraphError(str(exc)) from exc
            try:
                await self._repository.commit_prepared(prepared)
            except BaseException:
                await self._graph.restore_active(activation)
                raise
            await self._finalize_graph_activation(activation)
            return prepared
        except BaseException:
            await self._repository.discard_prepared(prepared)
            if shadow is not None:
                await self._graph.discard_shadow(shadow)
            raise

    @asynccontextmanager
    async def _project_commit_lock(self):
        lock_path = getattr(self._graph, "commit_lock_path", None)
        if lock_path is None:
            yield
            return
        path = Path(lock_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(str(path), mode="a", timeout=None)
        await asyncio.to_thread(lock.acquire)
        try:
            yield
        finally:
            await asyncio.to_thread(lock.release)

    async def recover(self) -> None:
        recover = getattr(self._graph, "recover_pending", None)
        current_revision = getattr(self._repository, "current_revision", None)
        if recover is not None and current_revision is not None:
            await recover(await current_revision())

    async def _finalize_graph_activation(self, activation: object) -> None:
        finalize = getattr(self._graph, "finalize_activation", None)
        if finalize is not None:
            try:
                await finalize(activation)
            except Exception:
                logger.exception(
                    "graph activation committed but journal cleanup failed; "
                    "retaining journal for recovery"
                )


@dataclass(frozen=True, slots=True)
class CogneeShadowBuild:
    revision: int
    build_dir: Path
    runtime_dir: Path


@dataclass(frozen=True, slots=True)
class CogneeActivation:
    previous_pointer: bytes | None
    new_pointer: bytes
    previous_revision: int
    target_revision: int


class CogneeShadowGraph:
    """Build Cognee in revision-scoped directories and atomically switch a pointer."""

    def __init__(
        self,
        *,
        project_name: str,
        project_dir: str | Path,
        state_dir: str | Path,
        store_factory: Callable[..., Any] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self.project_name = project_name
        self.project_dir = Path(project_dir)
        self.state_dir = Path(state_dir)
        self._store_factory = store_factory
        self._on_progress = on_progress
        self._on_log = on_log

    @property
    def active_pointer_path(self) -> Path:
        return self.state_dir / "cognee_active.json"

    @property
    def pointer_journal_path(self) -> Path:
        return self.state_dir / "cognee_pointer_pending_commit.json"

    @property
    def commit_lock_path(self) -> Path:
        return self.state_dir / "episode_import.lock"

    async def build_shadow(
        self, *, target_revision: int, canonical_novel: str
    ) -> CogneeShadowBuild:
        build_dir = (
            self.state_dir / "cognee_builds" / f"{target_revision}-{uuid4().hex}"
        )
        runtime_dir = build_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        novel_path = build_dir / "novel.txt"
        novel_path.write_text(canonical_novel, encoding="utf-8")
        await self._ingest_candidate(runtime_dir=runtime_dir, novel_path=novel_path)
        return CogneeShadowBuild(
            revision=target_revision,
            build_dir=build_dir,
            runtime_dir=runtime_dir,
        )

    async def add_incremental(
        self,
        *,
        target_revision: int,
        canonical_novel: str,
        changed_contents: tuple[str, ...] = (),
    ) -> CogneeActivation:
        """Prepare additions off-line and atomically activate them.

        The canonical novel is rebuilt in a unique candidate runtime so a failed
        add never mutates the active graph.  The returned activation token makes
        repository commit failure reversible through ``restore_active``.
        """
        build_dir = (
            self.state_dir / "cognee_builds" / f"{target_revision}-{uuid4().hex}"
        )
        runtime_dir = build_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        active_runtime = resolve_active_cognee_runtime(
            project_dir=self.project_dir, state_dir=self.state_dir
        )
        self._clone_runtime(active_runtime, runtime_dir)
        novel_path = build_dir / "incremental.txt"
        delta = "\n\n".join(content.rstrip("\n") for content in changed_contents) + "\n"
        novel_path.write_text(delta, encoding="utf-8")
        await self._ingest_candidate(runtime_dir=runtime_dir, novel_path=novel_path)
        shadow = CogneeShadowBuild(target_revision, build_dir, runtime_dir)
        return await self.activate_shadow(shadow)

    async def _ingest_candidate(self, *, runtime_dir: Path, novel_path: Path) -> None:
        factory = self._store_factory
        if factory is None:
            from novelvideo.cognee.store import CogneeStore

            factory = CogneeStore
        store = factory(
            project_name=self.project_name,
            output_dir=str(self.project_dir),
            state_dir=str(self.state_dir),
            cognee_runtime_dir=str(runtime_dir),
            resolve_active_runtime=False,
        )
        try:
            await store.initialize()
            callbacks: dict[str, object] = {}
            if self._on_progress is not None:
                callbacks["on_progress"] = self._on_progress
            if self._on_log is not None:
                callbacks["on_log"] = self._on_log
            await store.ingest_novel_fast(
                str(novel_path),
                rebuild=False,
                persist_novel_content=False,
                **callbacks,
            )
        finally:
            result = store.close()
            if inspect.isawaitable(result):
                await result

    @staticmethod
    def _clone_runtime(source: Path, destination: Path) -> None:
        for name in ("cognee_system", "cognee_data"):
            source_path = source / name
            if source_path.is_dir():
                shutil.copytree(source_path, destination / name, dirs_exist_ok=True)

    async def activate_shadow(self, shadow: object) -> CogneeActivation:
        if not isinstance(shadow, CogneeShadowBuild):
            raise TypeError("shadow must be a CogneeShadowBuild")
        pointer = self.active_pointer_path
        pointer.parent.mkdir(parents=True, exist_ok=True)
        previous = pointer.read_bytes() if pointer.exists() else None
        payload = json.dumps(
            {
                "revision": shadow.revision,
                "build": str(shadow.build_dir),
                "runtime_dir": str(shadow.runtime_dir),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        previous_revision = shadow.revision - 1
        self._replace_json(
            self.pointer_journal_path,
            {
                "old_revision": previous_revision,
                "new_revision": shadow.revision,
                "old_pointer": previous.decode("utf-8") if previous else None,
                "new_pointer": payload.decode("utf-8"),
            },
        )
        self._replace_pointer(payload)
        return CogneeActivation(previous, payload, previous_revision, shadow.revision)

    async def restore_active(self, activation: object) -> None:
        if not isinstance(activation, CogneeActivation):
            raise TypeError("activation must be a CogneeActivation")
        if activation.previous_pointer is None:
            self.active_pointer_path.unlink(missing_ok=True)
        else:
            self._replace_pointer(activation.previous_pointer)
        self.pointer_journal_path.unlink(missing_ok=True)

    async def finalize_activation(self, activation: object) -> None:
        self.pointer_journal_path.unlink(missing_ok=True)

    async def recover_pending(self, database_revision: int) -> None:
        if not self.pointer_journal_path.is_file():
            return
        journal = json.loads(self.pointer_journal_path.read_text(encoding="utf-8"))
        if database_revision == int(journal["old_revision"]):
            payload = journal.get("old_pointer")
        elif database_revision == int(journal["new_revision"]):
            payload = journal["new_pointer"]
        else:
            raise RuntimeError("pending graph pointer does not match database revision")
        if payload is None:
            self.active_pointer_path.unlink(missing_ok=True)
        else:
            self._replace_pointer(payload.encode("utf-8"))
        self.pointer_journal_path.unlink(missing_ok=True)

    async def discard_shadow(self, shadow: object) -> None:
        # Candidate builds are intentionally retained for diagnostics.  In
        # particular, this method never deletes or prunes the active graph.
        return None

    def _replace_pointer(self, payload: bytes) -> None:
        pointer = self.active_pointer_path
        temporary = pointer.with_name(f"{pointer.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, pointer)
        finally:
            temporary.unlink(missing_ok=True)

    def _replace_json(self, path: Path, payload: dict[str, object]) -> None:
        self._replace_file(path, json.dumps(payload, separators=(",", ":")).encode())

    @staticmethod
    def _replace_file(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def resolve_active_cognee_runtime(
    *, project_dir: str | Path, state_dir: str | Path
) -> Path:
    """Resolve the runtime selected by the durable active pointer.

    Missing or invalid pointers preserve backwards compatibility by returning
    the canonical project paths.
    """
    canonical_state = Path(state_dir)
    pointer = canonical_state / "cognee_active.json"
    if not pointer.is_file():
        return canonical_state
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        active_runtime = Path(payload["runtime_dir"]).resolve()
    except (OSError, ValueError, TypeError, KeyError):
        return canonical_state
    build_root = (canonical_state / "cognee_builds").resolve()
    if not active_runtime.is_dir() or not active_runtime.is_relative_to(build_root):
        return canonical_state
    return active_runtime
