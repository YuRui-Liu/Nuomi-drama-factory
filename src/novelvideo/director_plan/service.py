from __future__ import annotations

from collections.abc import Callable
from typing import Any
from contextlib import nullcontext
import hashlib
import json
from uuid import UUID, uuid4

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
        if input.creative_contract_version == 1 and not input.author_chain_id:
            input = input.model_copy(update={"author_chain_id":
                getattr(self._planner, "author_chain_id", None) or str(uuid4())})
        expected_session = None
        if input.creative_contract_version == 1:
            saved = self._store.load_creative_checkpoint(input.episode, input.author_chain_id)
            if saved and saved.get("history"):
                from .revision_feedback import FeedbackHistory
                expected_session = self._preflight_author(input, FeedbackHistory.model_validate(saved["history"]))
        guard = getattr(self._planner, "author_chain_guard", None)
        with guard(input) if guard is not None else nullcontext():
            self._verify_author(input, expected_session)
            if input.creative_contract_version == 1:
                with self._store.creative_chain_guard(input.episode, input.author_chain_id):
                    return await self._create_creative(input, on_stage=on_stage,
                        old_plan=old_plan, assets=assets)
            return await self._create_draft(input, on_stage=on_stage,
                                           old_plan=old_plan, assets=assets)

    async def review_revision(self, input: DirectorPlanInput,
                              candidate: DirectorPlanRevision, *, on_stage=None,
                              old_plan=None, assets=()):
        """Re-review an existing candidate, retaining the original author session.

        The task supplies a stable input.author_chain_id for retries. A new child
        holds the new QC; the supplied revision and its historic findings stay intact.
        """
        if candidate.creative_contract_version != 1:
            raise ValueError("creative review requires a version 1 candidate")
        if (input.episode != candidate.episode
                or input.source_script_hash != candidate.source_script_hash
                or input.semantic_revision_id != candidate.semantic_revision_id):
            raise ValueError("creative review source does not match candidate")
        self._store.require_current(candidate)
        checkpoint_chain = "review-" + (input.author_chain_id or str(uuid4()))
        chain = candidate.author_chain_id or input.author_chain_id or str(uuid4())
        input = input.model_copy(update={"author_chain_id": chain,
            "creative_contract_version": 1, "prior_creative_feedback": candidate.creative_feedback})
        expected_session = self._preflight_author(input, candidate.creative_feedback)
        saved_review = self._store.load_creative_checkpoint(input.episode, checkpoint_chain)
        if saved_review and saved_review.get("history"):
            from .revision_feedback import FeedbackHistory
            expected_session = self._preflight_author(input,
                FeedbackHistory.model_validate(saved_review["history"])) or expected_session
        guard = getattr(self._planner, "author_chain_guard", None)
        with guard(input) if guard else nullcontext():
            self._verify_author(input, expected_session)
            with self._store.creative_chain_guard(input.episode, chain):
                return await self._create_creative(input, on_stage=on_stage,
                    old_plan=old_plan, assets=assets, seed_revision=candidate,
                    checkpoint_chain_id=checkpoint_chain)

    def _preflight_author(self, input, history):
        if history is None:
            return None
        try:
            expected = str(UUID(history.author_session_id))
        except ValueError:
            return None
        if expected == input.author_chain_id:
            return None  # Non-native runtimes use the chain itself as identity.
        check = getattr(self._planner, "require_author_session", None)
        try:
            if check is None:
                raise ValueError("native author resume verification is unavailable")
            check(input, expected)
        except Exception as exc:
            raise DirectorPlanPlanningError("session_resume_unavailable", str(exc)) from exc
        return expected

    def _verify_author(self, input, expected):
        if expected is None:
            return
        try:
            self._planner.require_author_session(input, expected)
            if getattr(self._planner, "author_session_id", None) != expected:
                raise ValueError("native author identity changed")
        except Exception as exc:
            raise DirectorPlanPlanningError("session_resume_unavailable", str(exc)) from exc

    async def _create_creative(self, input, *, on_stage=None, old_plan=None,
                               assets=(), seed_revision=None, checkpoint_chain_id=None):
        from .creative_review import RULES_VERSION, bind_qc, validate_repair, CreativeRepairDraft
        from .revision_feedback import FeedbackHistory, AuthorRevisionRound
        model = str(getattr(self._planner, "model_name", "") or "").strip()
        if model:
            input = input.model_copy(update={"director_model": model})
        chain = input.author_chain_id or getattr(self._planner, "author_chain_id", None) or str(uuid4())
        checkpoint_chain = checkpoint_chain_id or chain
        input = input.model_copy(update={"author_chain_id": chain})
        digest = hashlib.sha256(json.dumps({"input": input.model_dump(mode="json"),
            "seed_revision": seed_revision.model_dump(mode="json") if seed_revision else None},
            sort_keys=True).encode()).hexdigest()
        saved = self._store.load_creative_checkpoint(input.episode, checkpoint_chain)
        candidate = None
        history = None
        rounds = list(saved.get("rounds", ())) if saved else []
        phase = "initial"
        prior_author_rounds = (len(seed_revision.creative_feedback.author_rounds)
            if seed_revision is not None and seed_revision.creative_feedback is not None else 0)
        if saved:
            if saved["input_hash"] != digest:
                raise DirectorPlanPlanningError("creative_input_changed", "Use a new author chain for changed inputs")
            phase = saved["phase"]
            if phase in {"author_pending", "plan_pending"}:
                raise DirectorPlanPlanningError("creative_author_outcome_unknown", "Author call was interrupted; explicit recovery required")
            plan_hash = hashlib.sha256(json.dumps(saved["candidate"], sort_keys=True).encode()).hexdigest()
            if saved.get("candidate_hash") != plan_hash:
                raise DirectorPlanPlanningError("creative_checkpoint_corrupt", "Candidate checksum mismatch")
            candidate = DirectorPlanRevision.model_validate(saved["candidate"])
            history = FeedbackHistory.model_validate(saved["history"])
            self._store.require_current(candidate)
            if phase == "complete":
                try:
                    existing = self._store.load(candidate.episode, candidate.revision_id)
                except FileNotFoundError:
                    self._store.save(candidate)
                    return candidate
                immutable = ("groups", "source_script_hash", "semantic_revision_id",
                    "creative_contract_version", "creative_feedback", "author_chain_id")
                if any(getattr(existing, field) != getattr(candidate, field) for field in immutable):
                    raise DirectorPlanPlanningError("creative_checkpoint_conflict", "Stored revision content changed")
                return existing

        def checkpoint(next_phase):
            payload = candidate.model_dump(mode="json") if candidate else None
            historical = history.model_dump(mode="json") if history else None
            event = {"phase": next_phase, "candidate": payload, "history": historical}
            if not rounds or rounds[-1] != event:
                rounds.append(event)
            self._store.save_creative_checkpoint(input.episode, checkpoint_chain, {
                "input_hash": digest, "phase": next_phase,
                "candidate": payload,
                "candidate_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                "history": historical, "rounds": rounds})

        if candidate is None:
            if seed_revision is None:
                checkpoint("plan_pending")
                draft = DirectorPlanDraft.model_validate(await self._planner.plan_episode(input))
                candidate = self._make_revision(input, draft.groups).model_copy(update={"author_chain_id": chain})
            else:
                candidate = self._make_revision(input, seed_revision.groups,
                    parent_revision_id=seed_revision.revision_id).model_copy(update={"author_chain_id": chain})
            history = FeedbackHistory.start(candidate.revision_id, RULES_VERSION,
                getattr(self._planner, "author_session_id", None) or chain)
            if seed_revision is not None and seed_revision.creative_feedback is not None:
                # Historical conclusions retain their old revision binding; all
                # require explicit recheck before this new candidate can clear.
                history = seed_revision.creative_feedback.model_copy(update={
                    "current_revision_id": candidate.revision_id,
                    "rules_version": RULES_VERSION,
                    "author_session_id": history.author_session_id})
            checkpoint("validate")

        while True:
            report = validate_director_plan(candidate, input.source_spans, input.dramatic_beats)
            candidate = candidate.model_copy(update={"validation_report": report})
            if not report.passed:
                # Structural failures are explicit repair findings, then independently
                # rechecked on the next candidate before any creative clearance.
                from .creative_review import structural_feedback_round
                history = history.apply_qc(structural_feedback_round(candidate, history, report))
                checkpoint("reviewed")
            elif not history.qc_rounds or history.qc_rounds[-1].revision_id != candidate.revision_id:
                checkpoint("qc_pending")
                try:
                    result = await self._planner.review_creative(input, candidate, history)
                    history = history.apply_qc(bind_qc(result, candidate, history))
                except Exception as exc:
                    report = report.model_copy(update={"issues": (*report.issues,
                        ValidationIssue(code="creative_qc_unavailable", message=str(exc),
                            location="creative_feedback", severity="warning"))})
                    candidate = candidate.model_copy(update={"validation_report": report})
                    break
                checkpoint("reviewed")
                if on_stage:
                    on_stage("creative_reviewed")
            if (report.passed and history.is_clear) or len(history.author_rounds) - prior_author_rounds >= input.creative_repair_budget:
                break
            checkpoint("author_pending")
            try:
                repair = CreativeRepairDraft.model_validate(await self._planner.repair_creative(input, candidate, history))
                replacement = self._make_revision(input, repair.groups,
                    parent_revision_id=candidate.revision_id).model_copy(update={"author_chain_id": chain})
                validate_repair(candidate, replacement, repair.responses)
                if not history.author_rounds:
                    session_id = getattr(self._planner, "author_session_id", None)
                    if session_id:
                        history = history.model_copy(update={"author_session_id": session_id})
                history = history.submit_revision(AuthorRevisionRound(round_id=str(uuid4()),
                    base_revision_id=candidate.revision_id, revision_id=replacement.revision_id,
                    rules_version=RULES_VERSION, author_session_id=history.author_session_id, responses=repair.responses))
            except Exception as exc:
                report = report.model_copy(update={"issues": (*report.issues,
                    ValidationIssue(code="creative_repair_invalid", message=str(exc),
                        location="creative_feedback", severity="warning"))})
                candidate = candidate.model_copy(update={"validation_report": report})
                break
            candidate = replacement
            checkpoint("validate")
        candidate = candidate.model_copy(update={"creative_feedback": history,
            "status": "review_required" if report.passed else "failed"})
        if report.passed and old_plan is not None:
            migration = match_assets(old_plan=old_plan, new_plan=candidate, assets=assets)
            candidate = candidate.model_copy(update={"migration_report": AssetMigrationReport(
                items=tuple(item.model_dump(mode="json") for item in migration.items))})
        checkpoint("complete")
        self._store.save(candidate)
        author_store = getattr(self._planner, "_author_store", None)
        if author_store is not None:
            from .creative_memory import CreativeMemoryStore
            CreativeMemoryStore(author_store.root.parent.parent,
                project_id=author_store.project_id, task_kind="director_plan").record(
                    history, source_hash=input.source_script_hash)
        return candidate

    async def _create_draft(
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
            report = validate_director_plan(validating, input.source_spans, input.dramatic_beats)
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
                    report = validate_director_plan(candidate, input.source_spans, input.dramatic_beats)
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
        if input.project_style_snapshot is not None:
            groups = tuple(
                group.model_copy(
                    update={"style_snapshot_id": input.project_style_snapshot_id}
                )
                for group in groups
            )
        return DirectorPlanRevision.new(
            creative_contract_version=input.creative_contract_version,
            episode=input.episode,
            source_script_hash=input.source_script_hash,
            director_model=input.director_model,
            prompt_version=input.prompt_version,
            project_style_snapshot_id=input.project_style_snapshot_id,
            project_style_snapshot=input.project_style_snapshot,
            semantic_revision_id=input.semantic_revision_id,
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
