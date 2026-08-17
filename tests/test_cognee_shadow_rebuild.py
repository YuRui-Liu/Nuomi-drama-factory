from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.episode_import_service import (
    CogneeShadowGraph,
    resolve_active_cognee_runtime,
)


class RecordingStore:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls: list[tuple[str, object]] = []

    async def initialize(self) -> None:
        self.calls.append(("initialize", None))

    async def ingest_novel_fast(
        self, novel_path: str, *, rebuild: bool, persist_novel_content: bool
    ) -> None:
        self.calls.append(
            ("ingest", (Path(novel_path), rebuild, persist_novel_content))
        )
        raise RuntimeError("memify failed")

    async def close(self) -> None:
        self.calls.append(("close", None))


@pytest.mark.asyncio
async def test_shadow_memify_failure_does_not_create_or_change_active_pointer(
    tmp_path,
) -> None:
    active_pointer = tmp_path / "state" / "cognee_active.json"
    active_pointer.parent.mkdir(parents=True)
    active_pointer.write_text('{"revision":4,"build":"active-v4"}', encoding="utf-8")
    stores: list[RecordingStore] = []

    def factory(**kwargs) -> RecordingStore:
        store = RecordingStore(**kwargs)
        stores.append(store)
        return store

    graph = CogneeShadowGraph(
        project_name="demo/project",
        project_dir=tmp_path,
        state_dir=tmp_path / "state",
        store_factory=factory,
    )

    with pytest.raises(RuntimeError, match="memify"):
        await graph.build_shadow(target_revision=5, canonical_novel="第一集新版")

    assert (
        active_pointer.read_text(encoding="utf-8")
        == '{"revision":4,"build":"active-v4"}'
    )
    assert len(stores) == 1
    store = stores[0]
    builds_dir = tmp_path / "state" / "cognee_builds"
    assert Path(store.kwargs["output_dir"]) == tmp_path
    assert Path(store.kwargs["state_dir"]) == tmp_path / "state"
    assert Path(store.kwargs["cognee_runtime_dir"]).is_relative_to(builds_dir)
    assert (
        Path(store.kwargs["cognee_runtime_dir"])
        .relative_to(builds_dir)
        .parts[0]
        .startswith("5-")
    )
    ingest_call = next(call for call in store.calls if call[0] == "ingest")
    assert ingest_call[1][2] is False
    assert store.calls[-1] == ("close", None)


@pytest.mark.asyncio
async def test_successful_shadow_switch_is_consumed_by_runtime_resolver(
    tmp_path,
) -> None:
    class SuccessfulStore(RecordingStore):
        async def ingest_novel_fast(
            self, novel_path: str, *, rebuild: bool, persist_novel_content: bool
        ) -> None:
            self.calls.append(
                ("ingest", (Path(novel_path), rebuild, persist_novel_content))
            )

    graph = CogneeShadowGraph(
        project_name="demo/project",
        project_dir=tmp_path / "project",
        state_dir=tmp_path / "state",
        store_factory=lambda **kwargs: SuccessfulStore(**kwargs),
    )
    shadow = await graph.build_shadow(target_revision=5, canonical_novel="第一集新版")
    await graph.activate_shadow(shadow)

    runtime_dir = resolve_active_cognee_runtime(
        project_dir=tmp_path / "project", state_dir=tmp_path / "state"
    )

    assert runtime_dir == shadow.runtime_dir


@pytest.mark.asyncio
async def test_each_shadow_attempt_uses_a_unique_build_directory(tmp_path) -> None:
    class SuccessfulStore(RecordingStore):
        async def ingest_novel_fast(
            self, novel_path: str, *, rebuild: bool, persist_novel_content: bool
        ) -> None:
            return None

    graph = CogneeShadowGraph(
        project_name="demo/project",
        project_dir=tmp_path / "project",
        state_dir=tmp_path / "state",
        store_factory=lambda **kwargs: SuccessfulStore(**kwargs),
    )

    first = await graph.build_shadow(target_revision=5, canonical_novel="第一集")
    second = await graph.build_shadow(target_revision=5, canonical_novel="第一集")

    assert first.build_dir != second.build_dir


@pytest.mark.asyncio
async def test_incremental_clones_active_runtime_and_ingests_only_delta(
    tmp_path,
) -> None:
    class SuccessfulStore(RecordingStore):
        async def ingest_novel_fast(
            self, novel_path: str, *, rebuild: bool, persist_novel_content: bool
        ) -> None:
            self.calls.append(("content", Path(novel_path).read_text(encoding="utf-8")))

    state_dir = tmp_path / "state"
    active_runtime = state_dir / "cognee_builds" / "4-active" / "runtime"
    (active_runtime / "cognee_system").mkdir(parents=True)
    (active_runtime / "cognee_system" / "graph.marker").write_text(
        "active-v4", encoding="utf-8"
    )
    pointer = state_dir / "cognee_active.json"
    pointer.write_text(
        '{"revision":4,"runtime_dir":"'
        + str(active_runtime).replace("\\", "\\\\")
        + '"}',
        encoding="utf-8",
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "novel.txt").write_text("第一集旧版\n", encoding="utf-8")
    stores: list[SuccessfulStore] = []

    def factory(**kwargs):
        store = SuccessfulStore(**kwargs)
        stores.append(store)
        return store

    graph = CogneeShadowGraph(
        project_name="demo/project",
        project_dir=project_dir,
        state_dir=state_dir,
        store_factory=factory,
    )
    activation = await graph.add_incremental(
        target_revision=5,
        canonical_novel="第一集旧版\n第二集新增\n",
        changed_contents=("第二集新增",),
    )

    selected = resolve_active_cognee_runtime(
        project_dir=project_dir, state_dir=state_dir
    )
    assert selected != active_runtime
    assert (selected / "cognee_system" / "graph.marker").read_text(
        encoding="utf-8"
    ) == "active-v4"
    assert ("content", "第二集新增\n") in stores[0].calls
    await graph.restore_active(activation)
    assert (
        resolve_active_cognee_runtime(project_dir=project_dir, state_dir=state_dir)
        == active_runtime
    )


@pytest.mark.asyncio
async def test_incremental_middle_insert_ingests_prepared_new_source_only(
    tmp_path,
) -> None:
    class SuccessfulStore(RecordingStore):
        async def ingest_novel_fast(
            self, novel_path: str, *, rebuild: bool, persist_novel_content: bool
        ) -> None:
            self.calls.append(("content", Path(novel_path).read_text(encoding="utf-8")))

    state_dir = tmp_path / "state"
    active_runtime = state_dir / "cognee_builds" / "4-active" / "runtime"
    active_runtime.mkdir(parents=True)
    (state_dir / "cognee_active.json").write_text(
        '{"revision":4,"runtime_dir":"'
        + str(active_runtime).replace("\\", "\\\\")
        + '"}',
        encoding="utf-8",
    )
    stores = []
    graph = CogneeShadowGraph(
        project_name="demo/project",
        project_dir=tmp_path / "project",
        state_dir=state_dir,
        store_factory=lambda **kwargs: (
            stores.append(SuccessfulStore(**kwargs)) or stores[-1]
        ),
    )

    await graph.add_incremental(
        target_revision=5,
        canonical_novel="第1集\n旧\n\n第2集\n新增\n\n第3集\n旧\n",
        changed_contents=("第2集\n新增",),
    )

    assert ("content", "第2集\n新增\n") in stores[0].calls


@pytest.mark.asyncio
async def test_concrete_restore_and_discard_never_remove_active_runtime(
    tmp_path,
) -> None:
    class SuccessfulStore(RecordingStore):
        async def ingest_novel_fast(
            self, novel_path: str, *, rebuild: bool, persist_novel_content: bool
        ) -> None:
            return None

    state_dir = tmp_path / "state"
    old_runtime = state_dir / "cognee_builds" / "4-old" / "runtime"
    old_runtime.mkdir(parents=True)
    graph = CogneeShadowGraph(
        project_name="demo/project",
        project_dir=tmp_path / "project",
        state_dir=state_dir,
        store_factory=lambda **kwargs: SuccessfulStore(**kwargs),
    )
    pointer = state_dir / "cognee_active.json"
    pointer.write_text(
        '{"revision":4,"runtime_dir":"' + str(old_runtime).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )
    candidate = await graph.build_shadow(target_revision=5, canonical_novel="新版")
    activation = await graph.activate_shadow(candidate)

    await graph.restore_active(activation)
    await graph.discard_shadow(candidate)

    assert (
        resolve_active_cognee_runtime(
            project_dir=tmp_path / "project", state_dir=state_dir
        )
        == old_runtime
    )
    assert old_runtime.is_dir()


def test_active_pointer_cannot_escape_cognee_build_root(tmp_path) -> None:
    state_dir = tmp_path / "state"
    external = tmp_path / "external-runtime"
    external.mkdir()
    state_dir.mkdir()
    (state_dir / "cognee_active.json").write_text(
        '{"runtime_dir":"' + str(external).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )

    assert (
        resolve_active_cognee_runtime(
            project_dir=tmp_path / "project", state_dir=state_dir
        )
        == state_dir
    )
