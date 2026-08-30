from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import (
    AssetMigrationReport,
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ValidationIssue,
    ValidationReport,
)
from .migration import LegacyShotAsset, match_assets
from .planner import (
    DirectorPlanContractError,
    DirectorPlanDraft,
    DirectorPlanInput,
    GroupRepairInput,
)
from .validation import validate_director_plan


class DirectorPlanPlanningError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class DirectorPlanService:
    def __init__(self, store: Any, planner: Any) -> None:
        self._store = store
        self._planner = planner

    async def create_draft(
        self,
        input: DirectorPlanInput,
        *,
        on_stage: Callable[[str], None] | None = None,
        old_plan: DirectorPlanRevision | None = None,
        assets: tuple[LegacyShotAsset, ...] = (),
    ) -> DirectorPlanRevision:
        planner_model = str(getattr(self._planner, "model_name", "") or "").strip()
        if planner_model:
            input = input.model_copy(update={"director_model": planner_model})
        validating: DirectorPlanRevision | None = None
        try:
            raw_draft = await self._planner.plan_episode(input)
        except DirectorPlanContractError as exc:
            self._raise_planning_error(
                input, None, "director_plan_contract_error", "planner.output", exc
            )
        except Exception as exc:
            self._raise_planning_error(
                input, None, "director_plan_provider_error", "planner", exc
            )
        try:
            draft = DirectorPlanDraft.model_validate(raw_draft)
        except Exception as exc:
            self._raise_planning_error(
                input, None, "director_plan_contract_error", "planner.output", exc
            )
        if on_stage is not None:
            on_stage("episode_planned")

        validating = self._make_revision(input, draft.groups).model_copy(
            update={"status": "validating"}
        )
        self._store.save(validating)

        groups = validating.groups
        try:
            report = validate_director_plan(validating, input.source_spans)
        except Exception as exc:
            self._raise_planning_error(
                input, validating, "director_plan_validation_error", "validation", exc
            )
        for group_id in self._failed_group_ids(groups, report):
            for _attempt in range(2):
                index = next(
                    (i for i, group in enumerate(groups) if group.id == group_id),
                    None,
                )
                if index is None or not self._group_has_errors(index, report):
                    break
                repair_input = self._repair_input(input, groups, index, report)
                try:
                    replacement = await self._planner.repair_group(repair_input)
                except DirectorPlanContractError as exc:
                    self._raise_planning_error(
                        input,
                        validating,
                        "director_plan_contract_error",
                        f"groups.{index}.repair",
                        exc,
                    )
                except Exception as exc:
                    self._raise_planning_error(
                        input,
                        validating,
                        "director_plan_provider_error",
                        f"groups.{index}.repair",
                        exc,
                    )
                if replacement.id != group_id:
                    self._raise_planning_error(
                        input,
                        validating,
                        "director_plan_contract_error",
                        f"groups.{index}.repair",
                        ValueError("group repair cannot replace a different group id"),
                    )
                groups = groups[:index] + (replacement,) + groups[index + 1 :]
                candidate = validating.model_copy(update={"groups": groups})
                try:
                    report = validate_director_plan(candidate, input.source_spans)
                except Exception as exc:
                    self._raise_planning_error(
                        input,
                        validating,
                        "director_plan_validation_error",
                        "validation",
                        exc,
                    )

        if on_stage is not None:
            on_stage("validated")
        final_status = "review_required" if report.passed else "failed"
        terminal = self._make_revision(
            input, groups, parent_revision_id=validating.revision_id
        ).model_copy(update={"status": final_status, "validation_report": report})
        if report.passed and old_plan is not None:
            migration = match_assets(
                old_plan=old_plan,
                new_plan=terminal,
                assets=assets,
            )
            terminal = terminal.model_copy(
                update={
                    "migration_report": AssetMigrationReport(
                        items=tuple(
                            item.model_dump(mode="json") for item in migration.items
                        )
                    )
                }
            )
        self._store.save(terminal)
        return terminal

    @staticmethod
    def _make_revision(
        input: DirectorPlanInput,
        groups: tuple[NarrativeGroupPlan, ...],
        *,
        parent_revision_id: str | None = None,
    ) -> DirectorPlanRevision:
        return DirectorPlanRevision.new(
            episode=input.episode,
            source_script_hash=input.source_script_hash,
            director_model=input.director_model,
            prompt_version=input.prompt_version,
            project_style_snapshot_id=input.project_style_snapshot_id,
            groups=groups,
            parent_revision_id=parent_revision_id,
        )

    def _raise_planning_error(
        self,
        input: DirectorPlanInput,
        validating: DirectorPlanRevision | None,
        code: str,
        location: str,
        exc: Exception,
    ) -> None:
        issue = ValidationIssue(
            code=code,
            message=str(exc),
            location=location,
        )
        failed = self._make_revision(
            input,
            validating.groups if validating is not None else (),
            parent_revision_id=(
                validating.revision_id if validating is not None else None
            ),
        ).model_copy(
            update={
                "status": "failed",
                "validation_report": ValidationReport(issues=(issue,)),
            }
        )
        error = DirectorPlanPlanningError(code, str(exc))
        try:
            self._store.save(failed)
        except Exception as storage_error:
            error.add_note(f"failed to persist failure revision: {storage_error}")
        raise error from exc

    @staticmethod
    def _failed_group_ids(
        groups: tuple[NarrativeGroupPlan, ...], report: ValidationReport
    ) -> tuple[str, ...]:
        return tuple(
            group.id
            for index, group in enumerate(groups)
            if any(
                issue.severity == "error"
                and DirectorPlanService._issue_matches_group(issue.location, index)
                for issue in report.issues
            )
        )

    @staticmethod
    def _group_has_errors(index: int, report: ValidationReport) -> bool:
        return any(
            issue.severity == "error"
            and DirectorPlanService._issue_matches_group(issue.location, index)
            for issue in report.issues
        )

    @staticmethod
    def _issue_matches_group(location: str, index: int) -> bool:
        prefix = f"groups.{index}"
        return location == prefix or location.startswith(prefix + ".")

    @staticmethod
    def _repair_input(
        input: DirectorPlanInput,
        groups: tuple[NarrativeGroupPlan, ...],
        index: int,
        report: ValidationReport,
    ) -> GroupRepairInput:
        failed = groups[index]
        previous = groups[index - 1] if index else None
        following = groups[index + 1] if index + 1 < len(groups) else None
        relevant_ids = {
            span_id
            for group in (previous, failed, following)
            if group is not None
            for span_id in group.source_span_ids
        }
        issues = tuple(
            issue.model_dump(mode="json")
            for issue in report.issues
            if DirectorPlanService._issue_matches_group(issue.location, index)
        )
        return GroupRepairInput(
            episode=input,
            failed_group=failed,
            previous_group=previous,
            next_group=following,
            relevant_source_spans=tuple(
                span for span in input.source_spans if span.id in relevant_ids
            ),
            issues=issues,
        )
