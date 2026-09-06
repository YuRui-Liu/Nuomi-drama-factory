from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = """第一集

1-1 天台 夜 外
人物：林默
林默走上天台。
"""


@pytest.fixture
async def structured_store(tmp_path: Path):
    from novelvideo.sqlite_store import SQLiteStore

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "project_config.json").write_text(
        json.dumps(
            {
                "knowledge_pipeline": "structured_v1",
                "knowledge_pipeline_status": "structured_pending",
                "spine_template": "drama",
            }
        ),
        encoding="utf-8",
    )
    store = SQLiteStore(
        "user/structured-atomic",
        output_dir=str(project_dir),
        state_dir=str(state_dir),
    )
    await store.initialize()
    await store.load_graph_state()
    try:
        yield store
    finally:
        await store.close()


def _fake_character_result():
    from novelvideo.structured_extraction import MergedCharacter

    quote = "林默走上天台。"
    start = SOURCE.index(quote)
    return [
        MergedCharacter(
            name="林默",
            gender="男",
            role="主角",
            face="短发，眉眼清晰",
            build="清瘦高挑",
            description="模型生成的新描述",
            evidence=[
                {
                    "chunk_id": "scene-0000",
                    "source_start": start,
                    "source_end": start + len(quote),
                    "evidence_kind": "mention",
                    "evidence_text": quote,
                }
            ],
            chunk_ids={"scene-0000"},
        )
    ]


def test_fallback_episode_range_keeps_exact_original_offsets() -> None:
    from novelvideo.structured_publication import _episode_source_ranges

    source = "项目说明\n\n1-1 客厅 日 内\n    林默：你好\n"
    ranges = _episode_source_ranges(source)

    assert ranges == [(1, 0, len(source), "项目说明", source)]


@pytest.mark.asyncio
async def test_source_publication_maps_role_face_and_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.structured_publication import build_structured_publication_from_source

    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
    )

    publication = await build_structured_publication_from_source(SOURCE, "drama")
    character = publication.characters[0].model
    assert character.role == "主角"
    assert character.face_prompt == "短发，眉眼清晰"
    assert character.body_type == "清瘦高挑"


async def _seed_previous_formal_results(store) -> None:
    from novelvideo.models import NovelCharacter, NovelEpisode, NovelScene

    store.save_novel_content("上一版导入原文")
    await store.add_episode(
        NovelEpisode(number=1, title="上一版分集", raw_content="上一版正文")
    )
    await store.add_character(
        NovelCharacter(name="林默", role="用户设定", face_prompt="用户面容")
    )
    await store.add_scene(
        NovelScene(name="旧场景", environment_prompt="用户旧场景")
    )
    await store.load_graph_state()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_target", ["scene", "evidence"])
async def test_production_structured_ingest_rolls_back_all_formal_results_on_failure(
    structured_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_target: str,
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.structured_ingest import ingest_source_text_structured

    store = structured_store
    await _seed_previous_formal_results(store)
    source_path = tmp_path / "source.txt"
    source_path.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
    )
    db = await store._ensure_db()
    if failure_target == "scene":
        await db.execute(
            """CREATE TRIGGER fail_structured_scene BEFORE INSERT ON scenes
            WHEN NEW.name = '天台'
            BEGIN SELECT RAISE(ABORT, 'scene publish failed'); END"""
        )
    else:
        await db.execute(
            """CREATE TRIGGER fail_structured_evidence BEFORE INSERT ON entity_evidence
            BEGIN SELECT RAISE(ABORT, 'evidence publish failed'); END"""
        )
    await db.commit()
    transitions: list[str] = []

    with pytest.raises(Exception, match=f"{failure_target} publish failed"):
        await ingest_source_text_structured(
            store,
            str(source_path),
            spine_template="drama",
            transition=lambda _state, status, **_kwargs: transitions.append(status),
        )

    await store.load_graph_state()
    assert store.get_episode(1).title == "上一版分集"
    assert store.get_character("林默").role == "用户设定"
    assert store.get_character("林默").face_prompt == "用户面容"
    assert (await store.get_scene("旧场景")).environment_prompt == "用户旧场景"
    assert await store.get_scene("天台") is None
    assert (Path(store.project_dir) / "novel.txt").read_text(encoding="utf-8") == "上一版导入原文"
    assert transitions[-1] == "structured_failed"
    assert "structured_ready" not in transitions


@pytest.mark.asyncio
async def test_ready_transition_failure_rolls_back_publication_and_novel_marker(
    structured_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.knowledge_pipeline import transition_structured_pipeline
    from novelvideo.models import NovelCharacter, NovelEpisode, NovelScene
    from novelvideo.structured_builders import (
        PublishedCharacter,
        PublishedEpisode,
        PublishedScene,
        StructuredPublication,
        StructuredSourceRef,
    )
    from novelvideo.structured_ingest import ingest_source_text_structured

    store = structured_store
    await _seed_previous_formal_results(store)
    source_path = tmp_path / "source.txt"
    source_path.write_text(SOURCE, encoding="utf-8")
    quote = "林默走上天台。"
    source_ref = StructuredSourceRef(
        episode_number=1,
        source_start=SOURCE.index(quote),
        source_end=SOURCE.index(quote) + len(quote),
        quote=quote,
    )
    publication = StructuredPublication(
        episodes=(
            PublishedEpisode(
                logical_id="episode:0001",
                model=NovelEpisode(number=1, title="新版分集", raw_content=SOURCE),
            ),
        ),
        characters=(
            PublishedCharacter(
                logical_id="character:lin-mo",
                model=NovelCharacter(name="林默", role="模型设定"),
                source_refs=(source_ref,),
            ),
        ),
        scenes=(
            PublishedScene(
                logical_id="scene:tian-tai",
                location="天台",
                model=NovelScene(name="天台", environment_prompt="新版天台"),
                source_refs=(source_ref,),
            ),
        ),
    )

    async def fake_build_publication(*_args, **_kwargs):
        return publication

    monkeypatch.setattr(
        "novelvideo.structured_publication.build_structured_publication_from_source",
        fake_build_publication,
    )
    transitions: list[str] = []

    def transition(_state, status, **_kwargs):
        transitions.append(status)
        if status == "structured_ready":
            raise RuntimeError("ready transition failed")
        return transition_structured_pipeline(_state, status, **_kwargs)

    with pytest.raises(RuntimeError, match="ready transition failed"):
        await ingest_source_text_structured(
            store,
            str(source_path),
            spine_template="drama",
            transition=transition,
        )

    await store.load_graph_state()
    assert store.get_episode(1).title == "上一版分集"
    assert store.get_character("林默").role == "用户设定"
    assert (await store.get_scene("旧场景")).environment_prompt == "用户旧场景"
    assert await store.get_scene("天台") is None
    assert await store.list_entity_evidence("character", "林默") == []
    assert (Path(store.project_dir) / "novel.txt").read_text(encoding="utf-8") == "上一版导入原文"
    assert transitions[-1] == "structured_failed"


@pytest.mark.asyncio
async def test_ready_transition_partial_success_is_compensated_to_failed(
    structured_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.knowledge_pipeline import (
        knowledge_pipeline_state_from_state_dir,
        transition_structured_pipeline,
    )
    from novelvideo.structured_ingest import ingest_source_text_structured

    store = structured_store
    await _seed_previous_formal_results(store)
    source_path = tmp_path / "source.txt"
    source_path.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
    )

    def transition(state_dir, status, **kwargs):
        state = transition_structured_pipeline(state_dir, status, **kwargs)
        if status == "structured_ready":
            raise RuntimeError("ready persisted before transport failed")
        return state

    with pytest.raises(RuntimeError, match="ready persisted"):
        await ingest_source_text_structured(
            store,
            str(source_path),
            spine_template="drama",
            transition=transition,
        )

    state = knowledge_pipeline_state_from_state_dir(store.state_dir)
    assert state.status == "structured_failed"
    assert store.get_episode(1).title == "上一版分集"
    assert await store.get_scene("天台") is None
    assert (Path(store.project_dir) / "novel.txt").read_text(encoding="utf-8") == "上一版导入原文"


@pytest.mark.asyncio
async def test_after_commit_failure_marks_only_the_persisted_attempt_failed(
    structured_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.knowledge_pipeline import knowledge_pipeline_state_from_state_dir
    from novelvideo.structured_ingest import ingest_source_text_structured

    store = structured_store
    await _seed_previous_formal_results(store)
    source_path = tmp_path / "source.txt"
    source_path.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
    )
    original_load_graph_state = store.load_graph_state
    load_calls = 0

    async def fail_first_cache_refresh() -> None:
        nonlocal load_calls
        load_calls += 1
        if load_calls == 1:
            raise RuntimeError("cache refresh failed after commit")
        await original_load_graph_state()

    monkeypatch.setattr(store, "load_graph_state", fail_first_cache_refresh)

    with pytest.raises(RuntimeError, match="cache refresh failed after commit"):
        await ingest_source_text_structured(
            store,
            str(source_path),
            spine_template="drama",
        )

    state = knowledge_pipeline_state_from_state_dir(store.state_dir)
    assert state.status == "structured_failed"
    assert state.attempt_id
    await original_load_graph_state()
    assert store.get_episode(1).title != "上一版分集"


@pytest.mark.asyncio
async def test_ready_failure_never_overwrites_a_concurrent_unrelated_database_commit(
    structured_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiosqlite

    from novelvideo import structured_extraction
    from novelvideo.knowledge_pipeline import transition_structured_pipeline
    from novelvideo.structured_ingest import ingest_source_text_structured

    store = structured_store
    await _seed_previous_formal_results(store)
    source_path = tmp_path / "source.txt"
    source_path.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
    )
    writer_task: asyncio.Task[None] | None = None

    async def write_unrelated_episode() -> None:
        async with aiosqlite.connect(store.db_path, timeout=2) as connection:
            await connection.execute(
                "INSERT INTO episodes (number, title, raw_content) VALUES (?, ?, ?)",
                (99, "并发写入", "不能被旧导入回滚"),
            )
            await connection.commit()

    async def transition(state_dir, status, **kwargs):
        nonlocal writer_task
        state = transition_structured_pipeline(state_dir, status, **kwargs)
        if status == "structured_ready":
            writer_task = asyncio.create_task(write_unrelated_episode())
            await asyncio.sleep(0.05)
            raise RuntimeError("ready delivery failed")
        return state

    with pytest.raises(RuntimeError, match="ready delivery failed"):
        await ingest_source_text_structured(
            store,
            str(source_path),
            spine_template="drama",
            transition=transition,
        )

    assert writer_task is not None
    await writer_task
    await store.load_graph_state()
    assert store.get_episode(99).title == "并发写入"


@pytest.mark.asyncio
async def test_ready_failure_preserves_a_concurrent_novel_marker_replacement(
    structured_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.knowledge_pipeline import transition_structured_pipeline
    from novelvideo.structured_ingest import ingest_source_text_structured

    store = structured_store
    await _seed_previous_formal_results(store)
    source_path = tmp_path / "source.txt"
    source_path.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
    )

    def transition(state_dir, status, **kwargs):
        state = transition_structured_pipeline(state_dir, status, **kwargs)
        if status == "structured_ready":
            (Path(store.project_dir) / "novel.txt").write_text(
                "并发替换的原文",
                encoding="utf-8",
            )
            raise RuntimeError("ready response lost")
        return state

    with pytest.raises(RuntimeError, match="ready response lost"):
        await ingest_source_text_structured(
            store,
            str(source_path),
            spine_template="drama",
            transition=transition,
        )

    assert (
        (Path(store.project_dir) / "novel.txt").read_text(encoding="utf-8")
        == "并发替换的原文"
    )


@pytest.mark.asyncio
async def test_ready_failure_does_not_rollback_or_fail_a_new_running_run(
    structured_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.knowledge_pipeline import (
        knowledge_pipeline_state_from_state_dir,
        transition_structured_pipeline,
    )
    from novelvideo.structured_ingest import ingest_source_text_structured

    store = structured_store
    await _seed_previous_formal_results(store)
    source_path = tmp_path / "source.txt"
    source_path.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
    )
    first_attempt: str | None = None

    def transition(state_dir, status, **kwargs):
        nonlocal first_attempt
        state = transition_structured_pipeline(state_dir, status, **kwargs)
        if status == "structured_running" and first_attempt is None:
            first_attempt = kwargs["attempt_id"]
        if status == "structured_ready":
            transition_structured_pipeline(
                state_dir,
                "structured_running",
                run_identity=kwargs["run_identity"],
                attempt_id="newer-same-identity-attempt",
            )
            raise RuntimeError("old ready response lost")
        return state

    with pytest.raises(RuntimeError, match="old ready response lost"):
        await ingest_source_text_structured(
            store,
            str(source_path),
            spine_template="drama",
            transition=transition,
        )

    state = knowledge_pipeline_state_from_state_dir(store.state_dir)
    assert state.status == "structured_running"
    assert state.attempt_id == "newer-same-identity-attempt"
    assert state.attempt_id != first_attempt


@pytest.mark.asyncio
async def test_production_structured_ingest_publishes_complete_bundle_before_ready(
    structured_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import structured_extraction
    from novelvideo.structured_ingest import ingest_source_text_structured

    store = structured_store
    await _seed_previous_formal_results(store)
    source_path = tmp_path / "source.txt"
    source_path.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
    )
    transitions: list[str] = []

    result = await ingest_source_text_structured(
        store,
        str(source_path),
        spine_template="drama",
        transition=lambda _state, status, **_kwargs: transitions.append(status),
    )

    assert result["published"] == {"episodes": 1, "characters": 1, "scenes": 1}
    assert store.get_episode(1).raw_content.startswith("第一集")
    assert store.get_episode(1).character_names == ["林默"]
    assert (await store.get_scene("天台")).time_of_day == "夜"
    assert store.get_character("林默").role == "用户设定"
    assert store.get_character("林默").face_prompt == "用户面容"
    assert await store.list_entity_evidence("character", "林默")
    assert transitions[-1] == "structured_ready"
    assert (Path(store.state_dir) / "episode_import.lock").is_file()


@pytest.mark.asyncio
async def test_deterministic_episode_build_never_imports_cognee_package(
    structured_store,
) -> None:
    store = structured_store
    original_cognee_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "novelvideo.cognee" or name.startswith("novelvideo.cognee.")
    }
    try:
        for name in tuple(original_cognee_modules):
            sys.modules.pop(name, None)

        episodes = await store.build_episodes_from_chapters(
            "第一章 归来\n正文。\n\n第二章 再会\n后续。"
        )

        assert [item.number for item in episodes] == [1, 2]
        assert not any(
            name == "novelvideo.cognee" or name.startswith("novelvideo.cognee.")
            for name in sys.modules
        )
    finally:
        for name in tuple(sys.modules):
            if name == "novelvideo.cognee" or name.startswith("novelvideo.cognee."):
                sys.modules.pop(name, None)
        sys.modules.update(original_cognee_modules)
        parent_package = sys.modules.get("novelvideo")
        restored_package = original_cognee_modules.get("novelvideo.cognee")
        if parent_package is not None:
            if restored_package is not None:
                setattr(parent_package, "cognee", restored_package)
            elif hasattr(parent_package, "cognee"):
                delattr(parent_package, "cognee")


@pytest.mark.asyncio
async def test_character_analysis_rolls_back_character_and_evidence_together(
    structured_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo import structured_extraction
    from novelvideo import structured_builders
    from novelvideo.structured_builders import build_characters_structured

    store = structured_store
    store.save_novel_content(SOURCE)
    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
    )
    monkeypatch.setattr(
        structured_builders,
        "_visual_workspace_for_merged_character",
        lambda **kwargs: SimpleNamespace(
            character_id=kwargs["item"].name,
            design_proposals=[],
        ),
    )
    db = await store._ensure_db()
    await db.execute(
        """CREATE TRIGGER fail_character_evidence BEFORE INSERT ON entity_evidence
        WHEN NEW.entity_type = 'character'
        BEGIN SELECT RAISE(ABORT, 'character evidence failed'); END"""
    )
    await db.commit()

    with pytest.raises(Exception, match="character evidence failed"):
        await build_characters_structured(store)

    await store.load_graph_state()
    assert store.get_character("林默") is None
    assert await store.list_entity_evidence("character", "林默") == []
