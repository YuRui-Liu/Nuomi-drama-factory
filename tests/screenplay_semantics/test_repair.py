from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior

from novelvideo.screenplay_semantics import (
    DramaticBeat,
    DramaticBeatDraft,
    Scene,
    SceneRepairDraft,
    ScreenplaySemanticRepairService,
    ScreenplaySemanticRepairRuntimeError,
    ScreenplaySemanticRevision,
    ScreenplaySemanticStore,
    SemanticValidationIssue,
    SemanticValidationReport,
    SourceBlock,
    SourceRange,
)


def _scene(scene_id: str, ordinal: int, start: int) -> Scene:
    return Scene(
        id=scene_id,
        ordinal=ordinal,
        source_range=SourceRange(start_line=start, end_line=start + 1),
        heading=f"{ordinal}. 测试场景",
        characters=("阿远",),
        content_hash=f"hash-{scene_id}",
        blocks=(
            SourceBlock(
                id=f"{scene_id}-line-1",
                ordinal=1,
                kind="action",
                text="阿远推门。",
                source_range=SourceRange(start_line=start, end_line=start),
            ),
            SourceBlock(
                id=f"{scene_id}-line-2",
                ordinal=2,
                kind="action",
                text="门完全打开。",
                source_range=SourceRange(start_line=start + 1, end_line=start + 1),
            ),
        ),
    )


def _draft(start: int, *, cover_all: bool = True, fact: str = "阿远推门") -> DramaticBeatDraft:
    return DramaticBeatDraft(
        source_ranges=(
            SourceRange(start_line=start, end_line=start + (1 if cover_all else 0)),
        ),
        characters=("阿远",),
        goal="打开门",
        obstacle="门很沉",
        action="阿远推门",
        reaction="门开始移动",
        turn="门锁松开",
        result="门完全打开",
        emotional_shift="紧张转为放松",
        estimated_duration_seconds=4,
        must_show=("阿远推门",),
        script_facts=(fact,),
    )


def _beat(scene_id: str, ordinal: int, start: int) -> DramaticBeat:
    return DramaticBeat(
        id=f"beat-{scene_id}-{ordinal:02d}",
        ordinal=ordinal,
        scene_id=scene_id,
        **_draft(start).model_dump(),
    )


def _base_revision(*, failed_scene_ids: tuple[str, ...] = ("scene-1", "scene-2")) -> ScreenplaySemanticRevision:
    scenes = (_scene("scene-1", 1, 10), _scene("scene-2", 2, 20), _scene("scene-3", 3, 30))
    issues = tuple(
        SemanticValidationIssue(
            code="needs_repair",
            message="需要修复",
            scene_id=scene_id,
        )
        for scene_id in failed_scene_ids
    )
    return ScreenplaySemanticRevision(
        revision_id="sem-base",
        episode=1,
        source_revision=7,
        source_hash="source-hash",
        status="review_required",
        scenes=scenes,
        beats=tuple(_beat(scene.id, 1, scene.source_range.start_line) for scene in scenes),
        validation_report=SemanticValidationReport.from_issues(issues),
        created_at=datetime.now(timezone.utc),
    )


class CountingStore(ScreenplaySemanticStore):
    def __init__(self, output_dir) -> None:
        super().__init__(output_dir)
        self.saved: list[ScreenplaySemanticRevision] = []

    def save(self, revision: ScreenplaySemanticRevision) -> ScreenplaySemanticRevision:
        self.saved.append(revision)
        return super().save(revision)


@pytest.mark.asyncio
async def test_second_round_only_retries_remaining_failed_scenes(tmp_path):
    calls: list[tuple[int, str]] = []

    async def invoke(scene: Scene, prompt: str) -> SceneRepairDraft:
        repair_round = json.loads(prompt)["repair_round"]
        calls.append((repair_round, scene.id))
        cover_all = scene.id == "scene-1" or repair_round == 2
        return SceneRepairDraft(
            scene_id=scene.id,
            beats=(_draft(scene.source_range.start_line, cover_all=cover_all),),
        )

    service = ScreenplaySemanticRepairService(CountingStore(tmp_path), invoke=invoke)
    child = await service.repair(_base_revision(), max_rounds=2, concurrency=3)

    assert calls == [(1, "scene-1"), (1, "scene-2"), (2, "scene-2")]
    assert child.parent_revision_id == "sem-base"
    assert child.status == "review_required"
    assert child.validation_report.passed is True


@pytest.mark.asyncio
async def test_passed_scene_is_byte_semantically_unchanged_and_store_is_called_once(tmp_path):
    store = CountingStore(tmp_path)
    base = _base_revision(failed_scene_ids=("scene-1",))

    async def invoke(scene: Scene, _prompt: str) -> SceneRepairDraft:
        return SceneRepairDraft(scene_id=scene.id, beats=(_draft(10),))

    child = await ScreenplaySemanticRepairService(store, invoke=invoke).repair(base)

    assert child.beats_for("scene-3") == base.beats_for("scene-3")
    assert len(store.saved) == 1


@pytest.mark.asyncio
async def test_out_of_order_runtime_results_merge_by_scene_ordinal(tmp_path):
    async def invoke(scene: Scene, _prompt: str) -> SceneRepairDraft:
        if scene.id == "scene-1":
            await asyncio.sleep(0.02)
        draft = _draft(scene.source_range.start_line).model_copy(
            update={"action": f"修复-{scene.id}"}
        )
        return SceneRepairDraft(scene_id=scene.id, beats=(draft,))

    child = await ScreenplaySemanticRepairService(
        CountingStore(tmp_path), invoke=invoke
    ).repair(_base_revision(), max_rounds=1)

    assert [(beat.scene_id, beat.action) for beat in child.beats[:2]] == [
        ("scene-1", "修复-scene-1"),
        ("scene-2", "修复-scene-2"),
    ]


@pytest.mark.asyncio
async def test_single_scene_failure_keeps_original_beats_and_reports_failure(tmp_path):
    base = _base_revision(failed_scene_ids=("scene-1",))

    async def invoke(_scene: Scene, _prompt: str) -> SceneRepairDraft:
        raise ValueError("scene-specific refusal")

    child = await ScreenplaySemanticRepairService(
        CountingStore(tmp_path), invoke=invoke
    ).repair(base, max_rounds=1)

    assert child.beats_for("scene-1") == base.beats_for("scene-1")
    assert "scene_repair_failed" in {issue.code for issue in child.validation_report.issues}


@pytest.mark.asyncio
async def test_global_runtime_transport_failure_saves_no_child(tmp_path):
    store = CountingStore(tmp_path)

    async def invoke(_scene: Scene, _prompt: str) -> SceneRepairDraft:
        raise ConnectionError("runtime unavailable")

    with pytest.raises(ScreenplaySemanticRepairRuntimeError):
        await ScreenplaySemanticRepairService(store, invoke=invoke).repair(
            _base_revision(failed_scene_ids=("scene-1",)), max_rounds=1
        )

    assert store.saved == []


@pytest.mark.asyncio
async def test_global_runtime_structured_parse_failure_saves_no_child(tmp_path):
    store = CountingStore(tmp_path)

    async def invoke(_scene: Scene, _prompt: str) -> SceneRepairDraft:
        raise UnexpectedModelBehavior("invalid structured response")

    with pytest.raises(ScreenplaySemanticRepairRuntimeError):
        await ScreenplaySemanticRepairService(store, invoke=invoke).repair(
            _base_revision(failed_scene_ids=("scene-1",)), max_rounds=1
        )

    assert store.saved == []


@pytest.mark.asyncio
async def test_unsupported_fact_is_rejected_as_contract_violation(tmp_path):
    base = _base_revision(failed_scene_ids=("scene-1",))

    async def invoke(scene: Scene, _prompt: str) -> SceneRepairDraft:
        return SceneRepairDraft(
            scene_id=scene.id,
            beats=(_draft(10, fact="阿远引爆炸弹"),),
        )

    child = await ScreenplaySemanticRepairService(
        CountingStore(tmp_path), invoke=invoke
    ).repair(base, max_rounds=1)

    assert child.beats_for("scene-1") == base.beats_for("scene-1")
    violations = [
        issue for issue in child.validation_report.issues
        if issue.code == "repair_contract_violation"
    ]
    assert len(violations) == 1
    assert violations[0].scene_id == "scene-1"


@pytest.mark.asyncio
async def test_repair_never_runs_more_than_two_rounds(tmp_path):
    rounds: list[int] = []

    async def invoke(scene: Scene, prompt: str) -> SceneRepairDraft:
        rounds.append(json.loads(prompt)["repair_round"])
        return SceneRepairDraft(
            scene_id=scene.id,
            beats=(_draft(scene.source_range.start_line, cover_all=False),),
        )

    await ScreenplaySemanticRepairService(
        CountingStore(tmp_path), invoke=invoke
    ).repair(_base_revision(failed_scene_ids=("scene-1",)), max_rounds=9)

    assert rounds == [1, 2]
