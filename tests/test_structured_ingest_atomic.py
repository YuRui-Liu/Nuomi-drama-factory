from __future__ import annotations

import json
import sys
from pathlib import Path

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
    from novelvideo.structured_builders import build_characters_structured

    store = structured_store
    store.save_novel_content(SOURCE)
    monkeypatch.setattr(
        structured_extraction,
        "extract_characters_from_chunks",
        lambda *_args, **_kwargs: _fake_character_result(),
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
