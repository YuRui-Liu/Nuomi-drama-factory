"""Build and validate versioned screenplay semantics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from novelvideo.episode_source_store import EpisodeSource
from novelvideo.screenplay_semantics.extractor import (
    SceneBeatDraft,
    SceneExtractionFailure,
    SceneExtractionResult,
    extract_scene_beats,
)
from novelvideo.screenplay_semantics.models import (
    DramaticBeat,
    Scene,
    ScreenplaySemanticRevision,
    SemanticValidationIssue,
    SemanticValidationReport,
)
from novelvideo.screenplay_semantics.parser import parse_screenplay_document
from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore
from novelvideo.screenplay_semantics.validation import validate_scene_beats

SceneExtractor = Callable[..., Awaitable[tuple[SceneExtractionResult, ...]]]


class ScreenplaySemanticService:
    def __init__(
        self,
        store: ScreenplaySemanticStore,
        *,
        extractor: SceneExtractor = extract_scene_beats,
    ) -> None:
        self.store = store
        self.extractor = extractor

    async def build(
        self,
        source: EpisodeSource,
        *,
        concurrency: int = 5,
        selected_scene_ids: set[str] | None = None,
    ) -> ScreenplaySemanticRevision:
        parsed = parse_screenplay_document(source.content)
        previous = self.store.load_active(source.episode_number)
        previous_by_hash = {
            item.content_hash: item for item in previous.scenes
        } if previous else {}
        reusable: dict[str, tuple[DramaticBeat, ...]] = {}
        scenes: list[Scene] = []
        targets: list[Scene] = []

        for parsed_scene in parsed.scenes:
            old_scene = previous_by_hash.get(parsed_scene.content_hash)
            force_selected_retry = (
                selected_scene_ids is not None and parsed_scene.id in selected_scene_ids
            )
            if old_scene is not None and previous is not None and not force_selected_retry:
                old_beats = previous.beats_for(old_scene.id)
                reusable[parsed_scene.id] = tuple(
                    beat.model_copy(
                        update={"scene_id": parsed_scene.id, "stale": False, "stale_reason": None}
                    )
                    for beat in old_beats
                )
                scenes.append(parsed_scene.model_copy(update={"status": "reused"}))
            elif selected_scene_ids is not None and parsed_scene.id not in selected_scene_ids:
                scenes.append(parsed_scene.model_copy(update={"status": "stale"}))
            else:
                scenes.append(parsed_scene)
                targets.append(parsed_scene)

        extraction = await self.extractor(tuple(targets), concurrency=concurrency)
        extracted_by_id = {item.scene_id: item for item in extraction}
        beats: list[DramaticBeat] = []
        issues: list[SemanticValidationIssue] = []
        final_scenes: list[Scene] = []

        for scene in scenes:
            if scene.id in reusable:
                beats.extend(reusable[scene.id])
                final_scenes.append(scene)
                continue
            result = extracted_by_id.get(scene.id)
            if isinstance(result, SceneBeatDraft):
                report = validate_scene_beats(scene, result.beats)
                issues.extend(report.issues)
                for ordinal, draft in enumerate(result.beats, start=1):
                    beats.append(DramaticBeat(
                        id=f"beat-{scene.id}-{ordinal:02d}", ordinal=ordinal,
                        scene_id=scene.id, **draft.model_dump(),
                    ))
                final_scenes.append(scene.model_copy(update={"status": "validated"}))
            elif isinstance(result, SceneExtractionFailure):
                issues.append(SemanticValidationIssue(
                    code="scene_extraction_failed", message=result.error,
                    scene_id=scene.id,
                ))
                final_scenes.append(scene.model_copy(update={"status": "failed"}))
            else:
                issues.append(SemanticValidationIssue(
                    code="scene_not_processed", message="场次尚未生成戏剧节拍",
                    scene_id=scene.id,
                ))
                final_scenes.append(scene.model_copy(update={"status": "stale"}))

        report = SemanticValidationReport.from_issues(tuple(issues))
        revision = ScreenplaySemanticRevision.new(
            episode=source.episode_number,
            source_revision=source.source_revision,
            source_hash=source.content_hash,
            scenes=tuple(final_scenes), beats=tuple(beats),
            metadata_blocks=parsed.metadata_blocks,
            validation_report=report,
            parent_revision_id=previous.revision_id if previous else None,
        ).model_copy(update={"status": "draft" if report.passed else "review_required"})
        return self.store.save(revision)


__all__ = ["ScreenplaySemanticService"]
