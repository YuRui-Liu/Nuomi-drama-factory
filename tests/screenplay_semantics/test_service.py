from dataclasses import replace

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
async def test_build_does_not_trigger_paid_media_and_returns_reviewable_failure(tmp_path):
    async def extractor(scenes, *, concurrency):
        from novelvideo.screenplay_semantics.extractor import SceneExtractionFailure
        return tuple(SceneExtractionFailure(scene_id=item.id, error="model failed") for item in scenes)

    result = await ScreenplaySemanticService(
        ScreenplaySemanticStore(tmp_path), extractor=extractor
    ).build(source(SCRIPT))

    assert result.status == "review_required"
    assert {issue.code for issue in result.validation_report.issues} == {"scene_extraction_failed"}
