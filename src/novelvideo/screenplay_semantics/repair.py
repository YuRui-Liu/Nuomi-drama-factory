"""Bounded per-scene Runtime repair for screenplay semantic revisions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
import inspect
import json
from datetime import datetime, timezone

from pydantic import Field, ValidationError
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior
from ulid import ULID

from novelvideo.screenplay_semantics.extractor import DramaticBeatDraft
from novelvideo.screenplay_semantics.models import (
    DramaticBeat,
    FrozenModel,
    Scene,
    ScreenplaySemanticRevision,
    SemanticValidationIssue,
)
from novelvideo.screenplay_semantics.repair_prompts import (
    REPAIR_SYSTEM_PROMPT,
    build_scene_repair_prompt,
)
from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore
from novelvideo.screenplay_semantics.validation import (
    validate_revision_beats,
    validate_scene_beats,
)
from novelvideo.text_task_runtime.runtime import current_text_task_runtime


class SceneRepairDraft(FrozenModel):
    scene_id: str = Field(min_length=1)
    beats: tuple[DramaticBeatDraft, ...]


class RepairProgress(FrozenModel):
    repair_round: int = Field(gt=0)
    max_rounds: int = Field(gt=0)
    pending_scene_ids: tuple[str, ...]
    completed_scene_ids: tuple[str, ...]


SceneRepairInvoker = Callable[[Scene, str], Awaitable[SceneRepairDraft]]
ProgressCallback = Callable[[RepairProgress], None]

_CONTRACT_CODES = {
    "source_range_outside_scene",
    "unknown_character",
    "invalid_dialogue_source",
    "unsupported_script_fact",
}


class ScreenplaySemanticRepairRuntimeError(RuntimeError):
    """The shared Runtime transport failed, so no child revision is persisted."""


def _is_runtime_transport_failure(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            ConnectionError,
            TimeoutError,
            json.JSONDecodeError,
            ModelAPIError,
            UnexpectedModelBehavior,
            ValidationError,
        ),
    ) or (
        isinstance(exc, RuntimeError)
        and "frozen text-task runtime" in str(exc)
    )


async def _runtime_invoke(scene: Scene, prompt: str) -> SceneRepairDraft:
    runtime = current_text_task_runtime()
    if runtime is None:
        raise RuntimeError("semantic repair requires a frozen text-task runtime")
    output = await runtime.run_structured(
        prompt=prompt,
        output_type=SceneRepairDraft,
        system_prompt=REPAIR_SYSTEM_PROMPT,
    )
    return output if isinstance(output, SceneRepairDraft) else SceneRepairDraft.model_validate(output)


def _issue_scene_ids(issues: Sequence[SemanticValidationIssue]) -> set[str]:
    return {
        issue.scene_id for issue in issues
        if issue.severity == "error" and issue.scene_id is not None
    }


def _materialize_beats(scene: Scene, draft: SceneRepairDraft) -> tuple[DramaticBeat, ...]:
    return tuple(
        DramaticBeat(
            id=f"beat-repair-{scene.id}-{ordinal:02d}",
            ordinal=ordinal,
            scene_id=scene.id,
            **item.model_dump(),
        )
        for ordinal, item in enumerate(draft.beats, start=1)
    )


class ScreenplaySemanticRepairService:
    def __init__(
        self,
        store: ScreenplaySemanticStore,
        *,
        invoke: SceneRepairInvoker | None = None,
    ) -> None:
        self.store = store
        self.invoke = invoke or _runtime_invoke

    async def repair(
        self,
        base: ScreenplaySemanticRevision,
        *,
        max_rounds: int = 2,
        concurrency: int = 3,
        before_commit: Callable[[], None | Awaitable[None]] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> ScreenplaySemanticRevision:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        round_limit = min(max_rounds, 2)
        beats_by_scene = {scene.id: base.beats_for(scene.id) for scene in base.scenes}
        report = base.validation_report
        operational_issues: dict[str, SemanticValidationIssue] = {}
        target_ids = _issue_scene_ids(report.issues)
        repaired_scene_ids: set[str] = set()

        for repair_round in range(1, round_limit + 1):
            ordered_targets = tuple(
                scene for scene in sorted(base.scenes, key=lambda item: item.ordinal)
                if scene.id in target_ids
            )
            if not ordered_targets:
                break
            semaphore = asyncio.Semaphore(concurrency)

            async def invoke_one(scene: Scene) -> tuple[Scene, SceneRepairDraft | Exception]:
                async with semaphore:
                    scene_issues = tuple(
                        issue for issue in report.issues if issue.scene_id == scene.id
                    )
                    try:
                        draft = await self.invoke(
                            scene,
                            build_scene_repair_prompt(
                                scene,
                                beats_by_scene[scene.id],
                                scene_issues,
                                repair_round=repair_round,
                            ),
                        )
                        return scene, draft
                    except Exception as exc:
                        return scene, exc

            results = await asyncio.gather(*(invoke_one(scene) for scene in ordered_targets))
            transport_failures = [
                result for _, result in results
                if isinstance(result, Exception) and _is_runtime_transport_failure(result)
            ]
            if transport_failures and len(transport_failures) == len(results):
                raise ScreenplaySemanticRepairRuntimeError(
                    f"Runtime transport failed for every target scene: {transport_failures[0]}"
                ) from transport_failures[0]
            completed: list[str] = []
            for scene, result in results:
                completed.append(scene.id)
                if isinstance(result, Exception):
                    operational_issues[scene.id] = SemanticValidationIssue(
                        code="scene_repair_failed",
                        message=str(result) or type(result).__name__,
                        scene_id=scene.id,
                    )
                    continue
                if result.scene_id != scene.id:
                    operational_issues[scene.id] = SemanticValidationIssue(
                        code="repair_contract_violation",
                        message=f"场次 ID 越权：{result.scene_id}",
                        scene_id=scene.id,
                    )
                    continue
                proposal_report = validate_scene_beats(scene, result.beats)
                violations = tuple(
                    issue for issue in proposal_report.issues
                    if issue.code in _CONTRACT_CODES
                )
                if violations:
                    operational_issues[scene.id] = SemanticValidationIssue(
                        code="repair_contract_violation",
                        message="；".join(issue.message for issue in violations),
                        scene_id=scene.id,
                    )
                    continue
                beats_by_scene[scene.id] = _materialize_beats(scene, result)
                repaired_scene_ids.add(scene.id)
                operational_issues.pop(scene.id, None)

            merged = tuple(
                beat
                for scene in sorted(base.scenes, key=lambda item: item.ordinal)
                for beat in beats_by_scene[scene.id]
            )
            report = validate_revision_beats(
                base.scenes,
                merged,
                extra_issues=tuple(operational_issues.values()),
            )
            target_ids = _issue_scene_ids(report.issues)
            if on_progress is not None:
                on_progress(RepairProgress(
                    repair_round=repair_round,
                    max_rounds=round_limit,
                    pending_scene_ids=tuple(
                        scene.id for scene in sorted(base.scenes, key=lambda item: item.ordinal)
                        if scene.id in target_ids
                    ),
                    completed_scene_ids=tuple(completed),
                ))

        final_beats = tuple(
            beat
            for scene in sorted(base.scenes, key=lambda item: item.ordinal)
            for beat in beats_by_scene[scene.id]
        )
        child = base.model_copy(update={
            "revision_id": str(ULID()),
            "parent_revision_id": base.revision_id,
            "version": base.version + 1,
            "status": "review_required",
            "beats": final_beats,
            "invalidated_beat_ids": tuple(
                beat.id
                for scene in sorted(base.scenes, key=lambda item: item.ordinal)
                if scene.id in repaired_scene_ids
                for beat in base.beats_for(scene.id)
            ),
            "validation_report": report,
            "created_at": datetime.now(timezone.utc),
            "activated_at": None,
        })
        if before_commit is not None:
            guarded = before_commit()
            if inspect.isawaitable(guarded):
                await guarded
        return self.store.save(child)


__all__ = [
    "RepairProgress",
    "SceneRepairDraft",
    "SceneRepairInvoker",
    "ScreenplaySemanticRepairService",
    "ScreenplaySemanticRepairRuntimeError",
]
