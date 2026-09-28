
import pytest

from novelvideo.episode_source_store import EpisodeSource
from novelvideo.screenplay_semantics.extractor import SceneBeatDraft
from novelvideo.screenplay_semantics.service import ScreenplaySemanticService
from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore
from tests.screenplay_semantics.test_extractor import make_draft


def source(content: str, revision: int = 1) -> EpisodeSource:
    return EpisodeSource(
        episode_number=1, title="第一集", content=content, content_hash=f"source-{revision}",
        source_filename="E001.md", source_revision=revision, downstream_stale=False,
        imported_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00",
    )


SCRIPT = """1-1 广播站 深夜 内
人物：林默
△林默撞门。
1-2 天台 黄昏 外
人物：林默
△林默扶住栏杆。
"""


@pytest.mark.asyncio
async def test_unrecognized_script_cannot_succeed_with_zero_scenes(tmp_path):
    async def extractor(scenes, *, concurrency):
        return ()

    service = ScreenplaySemanticService(ScreenplaySemanticStore(tmp_path), extractor=extractor)
    with pytest.raises(ValueError, match="SCREENPLAY_SCENES_NOT_FOUND"):
        await service.build(source("没有场头的正文。"))


@pytest.mark.asyncio
async def test_unchanged_scene_is_reused_and_only_changed_scene_is_extracted(tmp_path):
    calls: list[str] = []

    async def extractor(scenes, *, concurrency):
        calls.extend(item.id for item in scenes)
        return tuple(
            SceneBeatDraft(
                scene_id=item.id,
                beats=(make_draft(item).model_copy(update={
                    "script_facts": (item.blocks[0].text.lstrip("△"),),
                    "must_show": (item.blocks[0].text.lstrip("△"),),
                }),),
            )
            for item in scenes
        )

    store = ScreenplaySemanticStore(tmp_path)
    service = ScreenplaySemanticService(store, extractor=extractor)
    first = await service.build(source(SCRIPT), concurrency=5)
    store.activate(1, first.revision_id, expected_source_revision=1)
    calls.clear()

    changed = SCRIPT.replace("△林默扶住栏杆。", "△林默猛地扶住栏杆。")
    second = await service.build(source(changed, revision=2), concurrency=5)

    assert len(calls) == 1
    assert second.scenes[0].status == "reused"
    assert second.scenes[1].status == "validated"
    assert all(not item.stale for item in second.beats_for(second.scenes[0].id))


@pytest.mark.asyncio
async def test_selected_scene_retry_forces_extraction_even_when_source_is_unchanged(tmp_path):
    calls: list[str] = []

    async def extractor(scenes, *, concurrency):
        calls.extend(item.id for item in scenes)
        return tuple(
            SceneBeatDraft(
                scene_id=item.id,
                beats=(make_draft(item).model_copy(update={
                    "script_facts": (item.blocks[0].text.lstrip("△"),),
                    "must_show": (item.blocks[0].text.lstrip("△"),),
                }),),
            )
            for item in scenes
        )

    store = ScreenplaySemanticStore(tmp_path)
    service = ScreenplaySemanticService(store, extractor=extractor)
    first = await service.build(source(SCRIPT))
    store.activate(1, first.revision_id, expected_source_revision=1)
    calls.clear()

    selected_scene_id = first.scenes[0].id
    retried = await service.build(
        source(SCRIPT), selected_scene_ids={selected_scene_id}, concurrency=1
    )

    assert calls == [selected_scene_id]
    assert retried.scenes[0].status == "validated"
    assert retried.scenes[1].status == "reused"


@pytest.mark.asyncio
async def test_build_does_not_trigger_paid_media_and_returns_reviewable_failure(tmp_path):
    async def extractor(scenes, *, concurrency):
        from novelvideo.screenplay_semantics.extractor import SceneExtractionFailure
        return tuple(SceneExtractionFailure(scene_id=item.id, error="model failed") for item in scenes)

    result = await ScreenplaySemanticService(
        ScreenplaySemanticStore(tmp_path), extractor=extractor
    ).build(source(SCRIPT))

    assert result.status == "review_required"
    assert {issue.code for issue in result.validation_report.issues} == {"scene_extraction_failed"}


@pytest.mark.asyncio
async def test_selected_reference_context_reaches_scene_extractor(tmp_path):
    seen = []
    context = {"documents": [{"document_id": "people-1", "markdown": "林默过去曾受伤"}],
               "entities": []}

    async def extractor(scenes, *, concurrency, reference_context):
        seen.append(reference_context)
        return ()

    service = ScreenplaySemanticService(ScreenplaySemanticStore(tmp_path), extractor=extractor)
    await service.build(source(SCRIPT), reference_context=context)
    assert seen == [context]


@pytest.mark.asyncio
async def test_source_overwrite_reuses_unchanged_scene_from_archived_active_revision(tmp_path):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore

    sqlite = SQLiteStore("semantic-reuse", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    sources = EpisodeSourceStore(sqlite)
    calls = []

    async def extractor(scenes, *, concurrency):
        calls.extend(item.id for item in scenes)
        return tuple(SceneBeatDraft(
            scene_id=item.id,
            beats=(make_draft(item).model_copy(update={
                "script_facts": (item.blocks[0].text.lstrip("△"),),
                "must_show": (item.blocks[0].text.lstrip("△"),),
            }),),
        ) for item in scenes)

    try:
        await sources.upsert_sources([build_episode_candidate("E01.md", SCRIPT)], expected_revision=0)
        first_source = (await sources.list_sources())[0]
        store = ScreenplaySemanticStore(tmp_path / "project")
        service = ScreenplaySemanticService(store, extractor=extractor)
        first = await service.build(first_source)
        store.activate(1, first.revision_id, expected_source_revision=1)
        calls.clear()
        changed = SCRIPT.replace("△林默扶住栏杆。", "△林默猛地扶住栏杆。")
        await sources.upsert_sources([build_episode_candidate("E01.md", changed)], expected_revision=1)
        assert store.load_active(1) is None
        second_source = (await sources.list_sources())[0]
        second = await service.build(second_source)
        assert second.parent_revision_id == first.revision_id
        assert second.scenes[0].status == "reused"
        assert calls == [second.scenes[1].id]
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_build_awaits_async_guarded_save_callback(tmp_path):
    saved = []

    async def extractor(scenes, *, concurrency):
        return ()

    async def save_revision(revision):
        saved.append(revision.revision_id)
        return revision

    service = ScreenplaySemanticService(ScreenplaySemanticStore(tmp_path), extractor=extractor)
    result = await service.build(source(SCRIPT), save_revision=save_revision)
    assert saved == [result.revision_id]


@pytest.mark.asyncio
async def test_unchanged_text_reextracts_when_frozen_design_context_changes(tmp_path):
    calls = []

    async def extractor(scenes, *, concurrency, reference_context):
        calls.append((tuple(scene.id for scene in scenes), reference_context))
        return tuple(SceneBeatDraft(scene_id=scene.id, beats=(make_draft(scene).model_copy(update={"script_facts": (scene.blocks[0].text.lstrip("△"),), "must_show": (scene.blocks[0].text.lstrip("△"),)}),)) for scene in scenes)

    store = ScreenplaySemanticStore(tmp_path)
    service = ScreenplaySemanticService(store, extractor=extractor)
    design_a = {"documents": [{"document_id": "people", "revision_id": "design-a", "markdown": "旧设计"}], "entities": []}
    design_b = {"documents": [{"document_id": "people", "revision_id": "design-b", "markdown": "新设计"}], "entities": []}
    first = await service.build(source(SCRIPT), reference_context=design_a)
    store.activate(1, first.revision_id, expected_source_revision=1)
    calls.clear()

    second = await service.build(source(SCRIPT, revision=2), reference_context=design_b)

    assert calls == [(tuple(scene.id for scene in second.scenes), design_b)]
    assert all(scene.status == "validated" for scene in second.scenes)
    assert second.reference_context_hash != first.reference_context_hash


@pytest.mark.asyncio
async def test_changed_unselected_scene_retains_old_beats_with_explicit_old_source_evidence(tmp_path):
    async def extractor(scenes, *, concurrency):
        return tuple(SceneBeatDraft(scene_id=scene.id, beats=(make_draft(scene).model_copy(update={"script_facts": (scene.blocks[0].text.lstrip("△"),), "must_show": (scene.blocks[0].text.lstrip("△"),)}),)) for scene in scenes)

    store = ScreenplaySemanticStore(tmp_path)
    service = ScreenplaySemanticService(store, extractor=extractor)
    first = await service.build(source(SCRIPT))
    store.activate(1, first.revision_id, expected_source_revision=1)
    changed = SCRIPT.replace("△林默扶住栏杆。", "△林默猛地扶住栏杆。")
    second = await service.build(source(changed, revision=2), selected_scene_ids={first.scenes[0].id})

    stale_scene = second.scenes[1]
    old_beats = first.beats_for(first.scenes[1].id)
    retained = second.beats_for(stale_scene.id)
    assert stale_scene.status == "stale"
    assert len(retained) == len(old_beats) > 0
    assert retained[0].stale is True
    assert retained[0].stale_reason
    assert retained[0].evidence_source_revision == first.source_revision
    assert retained[0].evidence_source_hash == first.source_hash
    assert retained[0].script_facts == old_beats[0].script_facts
    assert retained[0].script_facts != ("林默猛地扶住栏杆。",)
    assert second.status == "review_required"


@pytest.mark.asyncio
async def test_unchanged_scene_with_shifted_evidence_coordinates_is_reextracted(tmp_path):
    from novelvideo.screenplay_semantics.models import SourceRange

    calls = []

    async def extractor(scenes, *, concurrency):
        calls.extend(scene.id for scene in scenes)
        return tuple(SceneBeatDraft(
            scene_id=scene.id,
            beats=(make_draft(scene).model_copy(update={
                "source_ranges": (SourceRange(
                    start_line=scene.blocks[0].source_range.start_line,
                    end_line=scene.blocks[-1].source_range.end_line),),
                "script_facts": (scene.blocks[0].text.lstrip("△"),),
                "must_show": (scene.blocks[0].text.lstrip("△"),),
                "dialogue_source_ids": tuple(block.id for block in scene.blocks if block.kind == "dialogue"),
            }),),
        ) for scene in scenes)

    content = SCRIPT + "林默：我看见了。\n"
    store = ScreenplaySemanticStore(tmp_path)
    service = ScreenplaySemanticService(store, extractor=extractor)
    first = await service.build(source(content))
    assert first.validation_report.passed
    store.activate(1, first.revision_id, expected_source_revision=1)
    calls.clear()
    shifted = content.replace("△林默撞门。", "△林默撞门。\n△又传来脚步。\n△门外有人。\n△灯突然熄灭。")

    second = await service.build(source(shifted, revision=2))

    assert first.scenes[1].id == second.scenes[1].id
    assert first.scenes[1].source_range != second.scenes[1].source_range
    assert calls == [scene.id for scene in second.scenes]
    assert second.scenes[1].status == "validated"
    beat = second.beats_for(second.scenes[1].id)[0]
    assert all(second.scenes[1].source_range.start_line <= item.start_line <= item.end_line <=
               second.scenes[1].source_range.end_line for item in beat.source_ranges)
    assert beat.dialogue_source_ids != first.beats_for(first.scenes[1].id)[0].dialogue_source_ids
    assert beat.dialogue_source_ids == tuple(
        block.id for block in second.scenes[1].blocks if block.kind == "dialogue")


@pytest.mark.asyncio
async def test_selected_fresh_scene_leaves_unselected_unarchived_scene_stale_without_failure(tmp_path):
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document

    selected_id = parse_screenplay_document(SCRIPT).scenes[0].id
    calls = []

    async def extractor(scenes, *, concurrency):
        calls.extend(scene.id for scene in scenes)
        return tuple(SceneBeatDraft(scene_id=scene.id, beats=(make_draft(scene),)) for scene in scenes)

    revision = await ScreenplaySemanticService(
        ScreenplaySemanticStore(tmp_path), extractor=extractor,
    ).build(source(SCRIPT), selected_scene_ids={selected_id})

    assert calls == [selected_id]
    assert [scene.status for scene in revision.scenes] == ["validated", "stale"]
    assert revision.beats_for(revision.scenes[1].id) == ()
    issue_codes = {issue.code for issue in revision.validation_report.issues}
    assert "scene_requires_reparse" in issue_codes
    assert "scene_not_processed" not in issue_codes
    assert revision.status == "review_required"


@pytest.mark.asyncio
async def test_selected_scene_without_extractor_result_still_reports_processing_failure(tmp_path):
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document

    selected_id = parse_screenplay_document(SCRIPT).scenes[0].id

    async def extractor(scenes, *, concurrency):
        assert [scene.id for scene in scenes] == [selected_id]
        return ()

    revision = await ScreenplaySemanticService(
        ScreenplaySemanticStore(tmp_path), extractor=extractor,
    ).build(source(SCRIPT), selected_scene_ids={selected_id})

    issues = {(issue.code, issue.scene_id) for issue in revision.validation_report.issues}
    assert ("scene_not_processed", selected_id) in issues
    assert ("scene_requires_reparse", revision.scenes[1].id) in issues
