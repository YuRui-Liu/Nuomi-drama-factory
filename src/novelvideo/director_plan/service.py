from __future__ import annotations

from typing import Any

from .models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ValidationIssue,
    ValidationReport,
)
from .planner import DirectorPlanDraft, DirectorPlanInput, GroupRepairInput
from .validation import validate_director_plan


class DirectorPlanPlanningError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class DirectorPlanService:
    def __init__(self, store: Any, planner: Any) -> None:
        self._store = store
        self._planner = planner

    async def create_draft(self, input: DirectorPlanInput) -> DirectorPlanRevision:
        validating: DirectorPlanRevision | None = None
        try:
            draft = DirectorPlanDraft.model_validate(
                await self._planner.plan_episode(input)
            )
            validating = self._make_revision(input, draft.groups).model_copy(
                update={"status": "validating"}
            )
            self._store.save(validating)

            groups = validating.groups
            report = validate_director_plan(validating, input.source_spans)
            for group_id in self._failed_group_ids(groups, report):
                for _attempt in range(2):
                    index = next(
                        (i for i, group in enumerate(groups) if group.id == group_id),
                        None,
                    )
                    if index is None or not self._group_has_errors(index, report):
                        break
                    repair_input = self._repair_input(input, groups, index, report)
                    replacement = await self._planner.repair_group(repair_input)
                    if replacement.id != group_id:
                        raise ValueError(
                            "group repair cannot replace a different group id"
                        )
                    groups = groups[:index] + (replacement,) + groups[index + 1 :]
                    candidate = validating.model_copy(update={"groups": groups})
                    report = validate_director_plan(candidate, input.source_spans)

            final_status = "review_required" if report.passed else "failed"
            terminal = self._make_revision(
                input, groups, parent_revision_id=validating.revision_id
            ).model_copy(update={"status": final_status, "validation_report": report})
            self._store.save(terminal)
            return terminal
        except Exception as exc:
            if isinstance(exc, DirectorPlanPlanningError):
                raise
            failed = self._failed_revision(input, validating, exc)
            self._store.save(failed)
            raise DirectorPlanPlanningError(
                "director_plan_provider_error", str(exc)
            ) from exc

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

    def _failed_revision(
        self,
        input: DirectorPlanInput,
        validating: DirectorPlanRevision | None,
        exc: Exception,
    ) -> DirectorPlanRevision:
        issue = ValidationIssue(
            code="director_plan_provider_error",
            message=str(exc),
            location="planner",
        )
        return self._make_revision(
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
