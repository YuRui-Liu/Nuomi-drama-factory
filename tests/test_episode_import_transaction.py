from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest

from novelvideo.episode_import_service import (
    EpisodeImportService,
    PreparedEpisodeImport,
)


@dataclass
class FakeRepository:
    prepared: PreparedEpisodeImport
    active_revision: int = 4
    active_novel: str = "第一集旧版"
    commits: int = 0
    discards: int = 0

    async def prepare_import(self, batch: object) -> PreparedEpisodeImport:
        return self.prepared

    async def commit_prepared(self, prepared: PreparedEpisodeImport) -> None:
        self.commits += 1
        self.active_revision = prepared.target_revision
        self.active_novel = prepared.canonical_novel

    async def discard_prepared(self, prepared: PreparedEpisodeImport) -> None:
        self.discards += 1


class FailingGraph:
    active_marker = "active-v4"
    activations = 0

    async def build_shadow(self, *, target_revision: int, canonical_novel: str):
        raise RuntimeError("memify failed")

    async def activate_shadow(self, shadow: object):
        self.activations += 1

    async def restore_active(self, activation: object) -> None:
        raise AssertionError("failed builds must never activate")

    async def discard_shadow(self, shadow: object) -> None:
        raise AssertionError("there is no completed shadow to discard")


@pytest.mark.asyncio
async def test_overwrite_graph_failure_keeps_active_content_and_revision() -> None:
    prepared = PreparedEpisodeImport(
        target_revision=5,
        canonical_novel="第一集新版",
        contains_overwrite=True,
        repository_token="candidate-v5",
    )
    repository = FakeRepository(prepared)
    graph = FailingGraph()
    service = EpisodeImportService(repository=repository, graph=graph)

    with pytest.raises(RuntimeError, match="memify"):
        await service.commit(object())

    assert repository.active_revision == 4
    assert repository.active_novel == "第一集旧版"
    assert repository.commits == 0
    assert repository.discards == 1
    assert graph.active_marker == "active-v4"
    assert graph.activations == 0


class RecordingTransactionalGraph:
    def __init__(self, *, incremental_error: Exception | None = None) -> None:
        self.active_marker = "active-v4"
        self.incremental_error = incremental_error
        self.restored: list[object] = []

    async def add_incremental(
        self, *, target_revision: int, canonical_novel: str, changed_contents=()
    ):
        if self.incremental_error is not None:
            raise self.incremental_error
        previous = self.active_marker
        self.active_marker = f"active-v{target_revision}"
        return previous

    async def build_shadow(self, *, target_revision: int, canonical_novel: str):
        return f"shadow-v{target_revision}"

    async def activate_shadow(self, shadow: object):
        previous = self.active_marker
        self.active_marker = str(shadow)
        return previous

    async def restore_active(self, activation: object) -> None:
        self.restored.append(activation)
        self.active_marker = str(activation)

    async def discard_shadow(self, shadow: object) -> None:
        return None


@pytest.mark.asyncio
async def test_all_skipped_prepared_import_consumes_preview_without_graph_or_revision_bump(
    tmp_path,
) -> None:
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore

    sqlite = SQLiteStore(
        "test/skip-noop", str(tmp_path / "project"), str(tmp_path / "state")
    )
    await sqlite.initialize()
    repository = EpisodeSourceStore(sqlite)
    try:
        await repository.upsert_sources(
            [build_episode_candidate("E01.md", "第1集\n旧")], expected_revision=0
        )
        preview = await repository.save_preview(
            base_revision=1,
            items=[build_episode_candidate("E01-new.md", "第1集\n新")],
        )
        graph = RecordingTransactionalGraph()

        prepared = await EpisodeImportService(
            repository=repository, graph=graph
        ).commit(
            {
                "preview_id": preview.id,
                "expected_revision": 1,
                "resolutions": {1: "skip"},
            }
        )

        assert prepared.target_revision == 1
        assert await repository.current_revision() == 1
        assert (await repository.list_sources())[0].content == "第1集\n旧"
        assert await repository.get_preview(preview.id) is None
        assert graph.active_marker == "active-v4"
        assert graph.restored == []
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_repository_failure_restores_active_pointer_after_overwrite() -> None:
    prepared = PreparedEpisodeImport(5, "新版", True, "candidate-v5")

    class FailingRepository(FakeRepository):
        async def commit_prepared(self, prepared: PreparedEpisodeImport) -> None:
            raise RuntimeError("repository failed")

    class Graph(RecordingTransactionalGraph):
        async def build_shadow(self, **kwargs):
            return "shadow-v5"

        async def activate_shadow(self, shadow: object):
            previous = self.active_marker
            self.active_marker = str(shadow)
            return previous

        async def discard_shadow(self, shadow: object) -> None:
            return None

    repository = FailingRepository(prepared)
    graph = Graph()

    with pytest.raises(RuntimeError, match="repository failed"):
        await EpisodeImportService(repository=repository, graph=graph).commit(object())

    assert graph.active_marker == "active-v4"
    assert graph.restored == ["active-v4"]


@pytest.mark.asyncio
async def test_incremental_success_commits_after_graph_activation() -> None:
    prepared = PreparedEpisodeImport(5, "新增第五集", False, "candidate-v5")
    repository = FakeRepository(prepared)
    graph = RecordingTransactionalGraph()

    await EpisodeImportService(repository=repository, graph=graph).commit(object())

    assert repository.active_revision == 5
    assert graph.active_marker == "active-v5"
    assert graph.restored == []


@pytest.mark.asyncio
async def test_incremental_repository_failure_rolls_back_graph() -> None:
    prepared = PreparedEpisodeImport(5, "新增第五集", False, "candidate-v5")

    class FailingRepository(FakeRepository):
        async def commit_prepared(self, prepared: PreparedEpisodeImport) -> None:
            raise RuntimeError("repository failed")

    repository = FailingRepository(prepared)
    graph = RecordingTransactionalGraph()

    with pytest.raises(RuntimeError, match="repository failed"):
        await EpisodeImportService(repository=repository, graph=graph).commit(object())

    assert graph.active_marker == "active-v4"
    assert graph.restored == ["active-v4"]
    assert repository.discards == 1


@pytest.mark.asyncio
async def test_incremental_graph_failure_leaves_repository_uncommitted() -> None:
    prepared = PreparedEpisodeImport(5, "新增第五集", False, "candidate-v5")
    repository = FakeRepository(prepared)
    graph = RecordingTransactionalGraph(incremental_error=RuntimeError("memify failed"))

    with pytest.raises(RuntimeError, match="memify failed"):
        await EpisodeImportService(repository=repository, graph=graph).commit(object())

    assert repository.active_revision == 4
    assert repository.commits == 0
    assert repository.discards == 1
    assert graph.active_marker == "active-v4"


@pytest.mark.asyncio
async def test_project_lock_revalidates_second_service_before_pointer_activation(
    tmp_path,
) -> None:
    lock_path = tmp_path / "state" / "episode_import.lock"
    first_activated = asyncio.Event()
    release_first = asyncio.Event()
    revision = 4

    class Repository:
        async def current_revision(self):
            return revision

        async def prepare_import(self, batch):
            if revision != 4:
                raise RuntimeError("revision conflict")
            return PreparedEpisodeImport(5, str(batch), True, batch)

        async def commit_prepared(self, prepared):
            nonlocal revision
            if prepared.repository_token == "first":
                first_activated.set()
                await release_first.wait()
            revision = 5

        async def discard_prepared(self, prepared):
            return None

    class Graph(RecordingTransactionalGraph):
        commit_lock_path = lock_path

        def __init__(self):
            super().__init__()
            self.activations = 0

        async def activate_shadow(self, shadow):
            self.activations += 1
            return await super().activate_shadow(shadow)

    first_graph, second_graph = Graph(), Graph()
    first = asyncio.create_task(
        EpisodeImportService(repository=Repository(), graph=first_graph).commit("first")
    )
    await first_activated.wait()
    second = asyncio.create_task(
        EpisodeImportService(repository=Repository(), graph=second_graph).commit("second")
    )
    await asyncio.sleep(0.05)
    assert second_graph.activations == 0

    release_first.set()
    await first
    with pytest.raises(RuntimeError, match="revision conflict"):
        await second
    assert second_graph.activations == 0


@pytest.mark.asyncio
async def test_incremental_success_persists_full_canonical_novel(tmp_path) -> None:
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore

    project = tmp_path / "project"
    sqlite = SQLiteStore("test/full-canonical", str(project), str(tmp_path / "state"))
    await sqlite.initialize()
    repository = EpisodeSourceStore(sqlite)
    try:
        await repository.upsert_sources(
            [build_episode_candidate("E01.md", "第1集\n旧")], expected_revision=0
        )
        (project / "novel.txt").write_text("第1集\n旧\n", encoding="utf-8")
        preview = await repository.save_preview(
            base_revision=1,
            items=[build_episode_candidate("E02.md", "第2集\n新增")],
        )

        await EpisodeImportService(
            repository=repository, graph=RecordingTransactionalGraph()
        ).commit(
            {
                "preview_id": preview.id,
                "expected_revision": 1,
                "resolutions": {},
            }
        )

        assert (project / "novel.txt").read_text(encoding="utf-8") == (
            "第1集\n旧\n\n第2集\n新增\n"
        )
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_overwrite_repository_commit_failure_restores_old_novel(tmp_path) -> None:
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore

    project = tmp_path / "project"
    sqlite = SQLiteStore("test/restore-novel", str(project), str(tmp_path / "state"))
    await sqlite.initialize()
    repository = EpisodeSourceStore(sqlite)
    try:
        await repository.upsert_sources(
            [build_episode_candidate("E01.md", "第1集\n旧")], expected_revision=0
        )
        old_novel = b"old canonical bytes\r\n"
        (project / "novel.txt").write_bytes(old_novel)
        preview = await repository.save_preview(
            base_revision=1,
            items=[build_episode_candidate("E01-new.md", "第1集\n新")],
        )
        db = await sqlite._ensure_db()

        async def fail_commit() -> None:
            raise RuntimeError("sqlite commit failed")

        db.commit = fail_commit
        graph = RecordingTransactionalGraph()

        with pytest.raises(RuntimeError, match="sqlite commit failed"):
            await EpisodeImportService(repository=repository, graph=graph).commit(
                {
                    "preview_id": preview.id,
                    "expected_revision": 1,
                    "resolutions": {1: "overwrite"},
                }
            )

        assert (project / "novel.txt").read_bytes() == old_novel
        assert graph.active_marker == "active-v4"
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_episode_journal_recovers_one_access_denied_without_rewrite(
    tmp_path, monkeypatch
) -> None:
    from novelvideo import episode_source_store as source_store_module
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore

    project = tmp_path / "project"
    state = tmp_path / "state"
    sqlite = SQLiteStore("test/journal-access-denied", str(project), str(state))
    await sqlite.initialize()
    repository = EpisodeSourceStore(sqlite)
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\n旧")], expected_revision=0
    )
    journal = state / "episode_source_pending_commit.json"
    real_replace = source_store_module.os.replace
    journal_attempts = 0

    class WindowsAccessDenied(PermissionError):
        winerror = 5

    def guarded_replace(source, target) -> None:
        nonlocal journal_attempts
        if target == journal:
            journal_attempts += 1
            if journal_attempts == 1:
                raise WindowsAccessDenied(13, "Access is denied", str(target))
            if journal.exists():
                raise AssertionError("journal must not be rewritten after publication")
        real_replace(source, target)

    monkeypatch.setattr(source_store_module.os, "replace", guarded_replace)
    try:
        await repository.upsert_sources(
            [build_episode_candidate("E02.md", "第2集\n新")],
            expected_revision=1,
            canonical_novel="第1集\n旧\n\n第2集\n新\n",
        )

        assert await repository.current_revision() == 2
        assert journal_attempts == 2
        assert not journal.exists()
        assert not list(state.glob("episode_source_pending_commit.*.novel"))
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_new_store_recovers_novel_after_process_interrupt_between_replace_and_commit(
    tmp_path,
) -> None:
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore

    project = tmp_path / "project"
    state = tmp_path / "state"
    sqlite = SQLiteStore("test/crash-recovery", str(project), str(state))
    await sqlite.initialize()
    repository = EpisodeSourceStore(sqlite)
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\n旧")], expected_revision=0
    )
    old_novel = b"old canonical bytes\r\n"
    (project / "novel.txt").write_bytes(old_novel)

    db = await sqlite._ensure_db()

    async def interrupt_commit() -> None:
        raise KeyboardInterrupt("simulated process death")

    db.commit = interrupt_commit
    with pytest.raises(KeyboardInterrupt, match="simulated process death"):
        await repository.upsert_sources(
            [build_episode_candidate("E01-new.md", "第1集\n新")],
            expected_revision=1,
            canonical_novel="第1集\n新\n",
        )
    assert (state / "episode_source_pending_commit.json").is_file()
    await sqlite.close()

    reopened = SQLiteStore("test/crash-recovery", str(project), str(state))
    recovered = EpisodeSourceStore(reopened)
    try:
        assert await recovered.current_revision() == 1
        assert (project / "novel.txt").read_bytes() == old_novel
        assert not (state / "episode_source_pending_commit.json").exists()
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_novel_recovery_rejects_backup_path_outside_state_without_touching_it(
    tmp_path,
) -> None:
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.sqlite_store import SQLiteStore

    project, state = tmp_path / "project", tmp_path / "state"
    external = tmp_path / "outside.novel"
    external.write_bytes(b"do not touch")
    state.mkdir()
    (state / "episode_source_pending_commit.json").write_text(
        json.dumps(
            {
                "old_revision": 0,
                "new_revision": 1,
                "old_present": True,
                "old_backup": str(external),
                "new_backup": "safe.new.novel",
                "old_hash": __import__("hashlib").sha256(b"do not touch").hexdigest(),
                "new_hash": __import__("hashlib").sha256(b"new").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    sqlite = SQLiteStore("test/unsafe-journal", str(project), str(state))
    store = EpisodeSourceStore(sqlite)
    with pytest.raises(RuntimeError, match="unsafe episode journal backup"):
        await store.current_revision()
    assert external.read_bytes() == b"do not touch"


@pytest.mark.asyncio
async def test_novel_recovery_rejects_hash_mismatch_without_replacing_novel(tmp_path) -> None:
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.sqlite_store import SQLiteStore

    project, state = tmp_path / "project", tmp_path / "state"
    project.mkdir()
    state.mkdir()
    (project / "novel.txt").write_bytes(b"canonical")
    backup = state / "episode_source_pending_commit.token.old.novel"
    backup.write_bytes(b"tampered")
    (state / "episode_source_pending_commit.json").write_text(
        json.dumps(
            {
                "old_revision": 0,
                "new_revision": 1,
                "old_present": True,
                "old_backup": backup.name,
                "new_backup": "episode_source_pending_commit.token.new.novel",
                "old_hash": __import__("hashlib").sha256(b"expected").hexdigest(),
                "new_hash": __import__("hashlib").sha256(b"new").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    sqlite = SQLiteStore("test/hash-journal", str(project), str(state))
    store = EpisodeSourceStore(sqlite)
    with pytest.raises(RuntimeError, match="hash mismatch"):
        await store.current_revision()
    assert (project / "novel.txt").read_bytes() == b"canonical"
    assert backup.read_bytes() == b"tampered"


@pytest.mark.asyncio
async def test_successful_episode_source_commit_leaves_no_pending_journal(
    tmp_path,
) -> None:
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore

    project = tmp_path / "project"
    state = tmp_path / "state"
    sqlite = SQLiteStore("test/no-journal", str(project), str(state))
    await sqlite.initialize()
    repository = EpisodeSourceStore(sqlite)
    try:
        await repository.upsert_sources(
            [build_episode_candidate("E01.md", "第1集\n新")],
            expected_revision=0,
            canonical_novel="第1集\n新\n",
        )
        assert not (state / "episode_source_pending_commit.json").exists()
        assert not list(state.glob("episode_source_pending_commit.*.novel"))
    finally:
        await sqlite.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("database_revision", [1, 2])
async def test_new_service_recovers_graph_pointer_from_database_revision(
    tmp_path, database_revision
) -> None:
    from novelvideo.episode_import_service import CogneeShadowGraph
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.sqlite_store import SQLiteStore

    project = tmp_path / "project"
    state = tmp_path / "state"
    old_runtime = state / "cognee_builds" / "1-old" / "runtime"
    new_runtime = state / "cognee_builds" / "2-new" / "runtime"
    old_runtime.mkdir(parents=True)
    new_runtime.mkdir(parents=True)
    pointer = state / "cognee_active.json"
    old_payload = (
        '{"revision":1,"runtime_dir":"' + str(old_runtime).replace("\\", "\\\\") + '"}'
    ).encode()
    new_payload = (
        '{"revision":2,"runtime_dir":"' + str(new_runtime).replace("\\", "\\\\") + '"}'
    ).encode()
    pointer.write_bytes(new_payload)
    (state / "cognee_pointer_pending_commit.json").write_text(
        __import__("json").dumps(
            {
                "old_revision": 1,
                "new_revision": 2,
                "old_pointer": old_payload.decode(),
                "new_pointer": new_payload.decode(),
            }
        ),
        encoding="utf-8",
    )
    sqlite = SQLiteStore("test/pointer-recovery", str(project), str(state))
    await sqlite.initialize()
    db = await sqlite._ensure_db()
    await db.execute(
        "INSERT INTO episode_source_state(id, project_revision) VALUES (1, ?)",
        (database_revision,),
    )
    await db.commit()
    repository = EpisodeSourceStore(sqlite)
    graph = CogneeShadowGraph(
        project_name="demo/project", project_dir=project, state_dir=state
    )

    await EpisodeImportService(repository=repository, graph=graph).recover()

    assert pointer.read_bytes() == (
        old_payload if database_revision == 1 else new_payload
    )
    assert not (state / "cognee_pointer_pending_commit.json").exists()
    await sqlite.close()


def test_direct_cognee_store_uses_active_graph_but_canonical_sqlite(
    tmp_path, monkeypatch
):
    from novelvideo.cognee import store as store_module

    state_dir = tmp_path / "state"
    runtime_dir = state_dir / "cognee_builds" / "5-build" / "runtime"
    runtime_dir.mkdir(parents=True)
    (state_dir / "cognee_active.json").write_text(
        '{"runtime_dir":"' + str(runtime_dir).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )
    contexts = []
    monkeypatch.setattr(
        store_module,
        "apply_cognee_project_storage_context",
        lambda path, _module: contexts.append(path) or (str(path), str(path)),
    )

    store = store_module.CogneeStore(
        "demo/project",
        output_dir=str(tmp_path / "project"),
        state_dir=str(state_dir),
    )

    assert store.project_dir == str(tmp_path / "project")
    assert store.state_dir == str(state_dir)
    assert store.sqlite_store.state_dir == str(state_dir)
    assert store.cognee_runtime_dir == str(runtime_dir)
    assert contexts[-1] == str(runtime_dir)


@pytest.mark.asyncio
async def test_production_factory_passes_canonical_project_and_state_dirs(
    tmp_path, monkeypatch
):
    from novelvideo.api import deps
    import novelvideo.cognee

    captured = {}

    class FakeStore:
        def __init__(self, project_name, **kwargs):
            captured.update(project_name=project_name, **kwargs)

        async def initialize(self):
            return None

    monkeypatch.setattr(deps, "OUTPUT_DIR", str(tmp_path / "output-root"))
    monkeypatch.setattr(deps, "STATE_DIR", str(tmp_path / "state-root"))
    monkeypatch.setattr(novelvideo.cognee, "CogneeStore", FakeStore)

    await deps.make_cognee_store("alice", "demo")

    assert captured["output_dir"] == str(tmp_path / "output-root" / "alice" / "demo")
    assert captured["state_dir"] == str(tmp_path / "state-root" / "alice" / "demo")
