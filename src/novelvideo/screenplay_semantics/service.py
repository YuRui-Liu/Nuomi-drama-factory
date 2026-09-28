"""Build and validate versioned screenplay semantics."""

from __future__ import annotations

from inspect import isawaitable
import hashlib
import json

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
        save_revision: Callable[[ScreenplaySemanticRevision], ScreenplaySemanticRevision] | None = None,
        reference_context: dict | None = None,
    ) -> ScreenplaySemanticRevision:
        parsed = parse_screenplay_document(source.content)
        if not parsed.scenes:
            raise ValueError(
                "SCREENPLAY_SCENES_NOT_FOUND: 未识别到场次，请检查场头格式，例如 1-1 海边灯塔 外 夜"
            )
        context_hash = (hashlib.sha256(json.dumps(
            reference_context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest() if reference_context is not None else None)
        previous = (self.store.load_active(source.episode_number)
                    or self.store.load_last_active_revision(source.episode_number))
        previous_by_hash = {
            item.content_hash: item for item in previous.scenes
        } if previous else {}
        previous_by_ordinal = {item.ordinal: item for item in previous.scenes} if previous else {}
        context_compatible = previous is not None and previous.reference_context_hash == context_hash
        reusable: dict[str, tuple[DramaticBeat, ...]] = {}
        stale_archival: dict[str, tuple[DramaticBeat, ...]] = {}
        scenes: list[Scene] = []
        targets: list[Scene] = []
        issues: list[SemanticValidationIssue] = []

        for parsed_scene in parsed.scenes:
            old_scene = previous_by_hash.get(parsed_scene.content_hash)
            old_beats = previous.beats_for(old_scene.id) if previous and old_scene else ()
            force_selected_retry = (
                selected_scene_ids is not None and parsed_scene.id in selected_scene_ids
            )
            if (context_compatible and old_scene is not None
                    and old_scene.status in {"validated", "reused"}
                    and old_beats and all(not beat.stale for beat in old_beats)
                    and not force_selected_retry):
                reusable[parsed_scene.id] = tuple(
                    beat.model_copy(update={"scene_id": parsed_scene.id}) for beat in old_beats
                )
                scenes.append(parsed_scene.model_copy(update={"status": "reused"}))
            elif selected_scene_ids is not None and parsed_scene.id not in selected_scene_ids:
                # Scene ids include their content hash. Only a same-position, same-heading
                # predecessor can be shown as archival context for changed unselected text.
                archival_scene = previous_by_ordinal.get(parsed_scene.ordinal)
                if archival_scene and archival_scene.heading == parsed_scene.heading and previous:
                    stale_archival[parsed_scene.id] = tuple(
                        beat.model_copy(update={
                            "scene_id": parsed_scene.id, "stale": True,
                            "stale_reason": "source or selected design changed; scene not rechecked",
                            "evidence_source_revision": beat.evidence_source_revision or previous.source_revision,
                            "evidence_source_hash": beat.evidence_source_hash or previous.source_hash,
                            "evidence_scene_id": beat.evidence_scene_id or archival_scene.id,
                        })
                        for beat in previous.beats_for(archival_scene.id)
                    )
                issues.append(SemanticValidationIssue(
                    code="scene_requires_reparse", message="场次正文或设计已变化，未选中重新校对",
                    scene_id=parsed_scene.id,
                ))
                scenes.append(parsed_scene.model_copy(update={"status": "stale"}))
            else:
                scenes.append(parsed_scene)
                targets.append(parsed_scene)

        extraction_options = {"reference_context": reference_context} if reference_context is not None else {}
        extraction = await self.extractor(tuple(targets), concurrency=concurrency,
                                          **extraction_options)
        extracted_by_id = {item.scene_id: item for item in extraction}
        beats: list[DramaticBeat] = []
        final_scenes: list[Scene] = []

        for scene in scenes:
            if scene.id in reusable:
                beats.extend(reusable[scene.id])
                final_scenes.append(scene)
                continue
            if scene.id in stale_archival:
                beats.extend(stale_archival[scene.id])
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
            reference_context_hash=context_hash,
            scenes=tuple(final_scenes), beats=tuple(beats),
            metadata_blocks=parsed.metadata_blocks,
            validation_report=report,
            parent_revision_id=previous.revision_id if previous else None,
        ).model_copy(update={"status": "draft" if report.passed else "review_required"})
        saved = (save_revision or self.store.save)(revision)
        return await saved if isawaitable(saved) else saved


__all__ = ["ScreenplaySemanticService"]
