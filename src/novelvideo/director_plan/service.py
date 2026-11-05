from __future__ import annotations

from collections.abc import Callable
from typing import Any
from contextlib import nullcontext
import asyncio
import hashlib
import json
from uuid import UUID, uuid4

from pydantic import ValidationError

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
from .professional_models import ProfessionalRunReceipt
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

    async def refresh_style_revision(
        self,
        input: DirectorPlanInput,
        candidate: DirectorPlanRevision,
        *,
        on_stage=None,
    ) -> DirectorPlanRevision:
        """Create a style-only child and review it without author-side repair."""
        from .creative_review import RULES_VERSION, bind_qc
        from .revision_feedback import FeedbackHistory

        if candidate.creative_contract_version != 1:
            raise ValueError("style refresh requires a version 1 candidate")
        if (input.episode != candidate.episode
                or input.source_script_hash != candidate.source_script_hash
                or input.semantic_revision_id != candidate.semantic_revision_id):
            raise ValueError("style refresh source does not match candidate")
        if (input.project_style_snapshot is None
                or input.project_style_snapshot.snapshot_id != input.project_style_snapshot_id):
            raise ValueError("style refresh requires a frozen current style snapshot")
        self._store.require_current(candidate)

        chain = candidate.author_chain_id or input.author_chain_id or str(uuid4())
        groups = tuple(group.model_copy(update={
            "style_snapshot_id": input.project_style_snapshot_id,
        }) for group in candidate.groups)
        child = DirectorPlanRevision.new(
            creative_contract_version=1,
            professional_status=candidate.professional_status,
            professional_decision=candidate.professional_decision,
            professional_runs=candidate.professional_runs,
            episode=candidate.episode,
            source_script_hash=candidate.source_script_hash,
            director_model=candidate.director_model,
            prompt_version=candidate.prompt_version,
            project_style_snapshot_id=input.project_style_snapshot_id,
            project_style_snapshot=input.project_style_snapshot,
            semantic_revision_id=candidate.semantic_revision_id,
            groups=groups,
            parent_revision_id=candidate.revision_id,
        ).model_copy(update={
            "status": "validating",
            "author_chain_id": chain,
            "scene_blocking_references": candidate.scene_blocking_references,
            "edit_source": candidate.edit_source,
            "migration_report": candidate.migration_report,
        })
        history = FeedbackHistory.start(
            child.revision_id, RULES_VERSION,
            (candidate.creative_feedback.author_session_id
             if candidate.creative_feedback is not None else chain),
        )
        if candidate.creative_feedback is not None:
            history = candidate.creative_feedback.model_copy(update={
                "current_revision_id": child.revision_id,
                "rules_version": RULES_VERSION,
                "author_session_id": history.author_session_id,
            })
        if on_stage is not None:
            on_stage("style_refresh_candidate")

        try:
            report = validate_director_plan(child, input.source_spans, input.dramatic_beats)
        except Exception as exc:
            report = ValidationReport(issues=(ValidationIssue(
                code="style_refresh_validation_failed",
                message=str(exc),
                location="groups",
            ),))
        child = child.model_copy(update={"validation_report": report})
        if report.passed:
            try:
                if on_stage is not None:
                    on_stage("style_refresh_qc")
                result = await self._planner.review_creative(input, child, history)
                history = history.apply_qc(bind_qc(result, child, history))
            except Exception as exc:
                report = report.model_copy(update={"issues": (*report.issues,
                    ValidationIssue(
                        code="creative_qc_unavailable",
                        message=str(exc),
                        location="creative_feedback",
                        severity="warning",
                    ))})
                child = child.model_copy(update={"validation_report": report})
        child = child.model_copy(update={
            "status": "review_required",
            "creative_feedback": history,
        })
        self._store.save(child)
        if on_stage is not None:
            on_stage("complete")
        return child

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
        professional_candidate_id = None
        rounds = list(saved.get("rounds", ())) if saved else []
        phase = "initial"
        if saved:
            professional_candidate_id = saved.get("professional_candidate_id")
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
            if professional_candidate_id:
                candidate = self._reconcile_professional_checkpoint(
                    candidate, professional_candidate_id
                )
            if phase == "complete":
                try:
                    existing = self._store.load(candidate.episode, candidate.revision_id)
                except FileNotFoundError:
                    return self._save_creative_terminal(
                        candidate, professional_candidate_id
                    )
                if professional_candidate_id:
                    return self._save_creative_terminal(
                        candidate, professional_candidate_id
                    )
                immutable = ("groups", "source_script_hash", "semantic_revision_id",
                    "creative_contract_version", "creative_feedback", "author_chain_id",
                    "professional_status", "professional_decision", "professional_runs")
                if any(getattr(existing, field) != getattr(candidate, field) for field in immutable):
                    raise DirectorPlanPlanningError("creative_checkpoint_conflict", "Stored revision content changed")
                return existing

        def checkpoint(next_phase, current_candidate=None):
            checkpoint_candidate = current_candidate or candidate
            payload = checkpoint_candidate.model_dump(mode="json") if checkpoint_candidate else None
            historical = history.model_dump(mode="json") if history else None
            event = {"phase": next_phase, "candidate": payload, "history": historical}
            if not rounds or rounds[-1] != event:
                rounds.append(event)
            self._store.save_creative_checkpoint(input.episode, checkpoint_chain, {
                "input_hash": digest, "phase": next_phase,
                "candidate": payload,
                "candidate_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                "history": historical, "rounds": rounds,
                "professional_candidate_id": professional_candidate_id})

        if phase == "professional_pending" and candidate is not None:
            from .professional_collaboration import professional_status
            # A call with a durable pending receipt may have reached the provider
            # before this worker stopped. Preserve it as unknown; never resend it.
            recovered = tuple(
                run.model_copy(update={
                    "status": "unknown",
                    "error_code": run.error_code or "professional_outcome_unknown",
                }) if run.status == "pending" else run
                for run in candidate.professional_runs
            )
            candidate = candidate.model_copy(update={
                "professional_runs": recovered,
                "professional_status": professional_status(recovered),
            })
            checkpoint("professional_recovered")
            self._save_professional_candidate(candidate)

        if candidate is None:
            if seed_revision is None:
                checkpoint("plan_pending")
                draft = DirectorPlanDraft.model_validate(await self._planner.plan_episode(input))
                from .professional_collaboration import (
                    ProfessionalAssignmentError,
                    prepare_professional_runs,
                    professional_status,
                )
                try:
                    professional_runs = prepare_professional_runs(
                        input, draft, model=model or input.director_model
                    )
                except ProfessionalAssignmentError as exc:
                    raise DirectorPlanPlanningError(
                        "professional_assignment_invalid", str(exc)
                    ) from exc
                decision = str(draft.professional_decision or "").strip()
                candidate = self._make_revision(
                    input,
                    draft.groups,
                    professional_status=professional_status(professional_runs),
                    professional_decision=decision or "No specialist contribution was requested.",
                    professional_runs=professional_runs,
                ).model_copy(update={"author_chain_id": chain})
            else:
                candidate = self._make_revision(input, seed_revision.groups,
                    parent_revision_id=seed_revision.revision_id,
                    professional_status=seed_revision.professional_status,
                    professional_decision=seed_revision.professional_decision,
                    professional_runs=seed_revision.professional_runs).model_copy(update={"author_chain_id": chain})
            history = FeedbackHistory.start(candidate.revision_id, RULES_VERSION,
                getattr(self._planner, "author_session_id", None) or chain)
            if seed_revision is not None and seed_revision.creative_feedback is not None:
                # Historical conclusions retain their old revision binding; all
                # require explicit recheck before this new candidate can clear.
                history = seed_revision.creative_feedback.model_copy(update={
                    "current_revision_id": candidate.revision_id,
                    "rules_version": RULES_VERSION,
                    "author_session_id": history.author_session_id})
            if seed_revision is None and candidate.professional_runs:
                if on_stage:
                    on_stage("professional_roles_started")
                candidate = candidate.model_copy(update={"status": "validating"})
                professional_candidate_id = candidate.revision_id
                checkpoint("professional_pending", candidate)
                self._save_professional_candidate(candidate)
                candidate = await self._run_professional_assignments(
                    input, draft, candidate, checkpoint
                )
                if on_stage:
                    on_stage("professional_roles_finished")
                checkpoint("professional_complete")
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
                    parent_revision_id=candidate.revision_id,
                    professional_status=candidate.professional_status,
                    professional_decision=candidate.professional_decision,
                    professional_runs=candidate.professional_runs).model_copy(update={"author_chain_id": chain})
                validate_repair(candidate, replacement, repair.responses)
                if replacement.professional_runs:
                    from .professional_collaboration import reapply_professional_outputs
                    replacement = replacement.model_copy(update={
                        "groups": reapply_professional_outputs(
                            replacement.groups, replacement.professional_runs
                        )
                    })
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
        candidate = self._save_creative_terminal(
            candidate, professional_candidate_id
        )
        author_store = getattr(self._planner, "_author_store", None)
        if author_store is not None:
            from .creative_memory import CreativeMemoryStore
            CreativeMemoryStore(author_store.root.parent.parent,
                project_id=author_store.project_id, task_kind="director_plan").record(
                    history, source_hash=input.source_script_hash)
        return candidate

    def _save_professional_candidate(self, candidate: DirectorPlanRevision) -> None:
        save = getattr(self._store, "save_professional_candidate", None)
        if save is not None:
            save(candidate)

    def _reconcile_professional_checkpoint(
        self, candidate: DirectorPlanRevision, provisional_revision_id: str
    ) -> DirectorPlanRevision:
        """Reconcile the two durable writes around each specialist receipt.

        The creative checkpoint is written before the revision projection. On
        restart, either side may therefore be one event ahead. Prefer a terminal
        stored receipt over a stale pending checkpoint receipt, and apply a newer
        checkpoint result to the validating projection before any recovery action.
        """
        load = getattr(self._store, "load", None)
        save = getattr(self._store, "save_professional_candidate", None)
        if load is None or save is None or not candidate.professional_runs:
            return candidate
        try:
            provisional = load(candidate.episode, provisional_revision_id)
        except FileNotFoundError:
            return candidate
        if provisional.status != "validating":
            return candidate
        if len(provisional.professional_runs) != len(candidate.professional_runs):
            raise DirectorPlanPlanningError(
                "professional_checkpoint_mismatch", "Receipt count changed during recovery"
            )

        identity_fields = (
            "run_id", "role_id", "profile_revision", "profile_name", "profile_source",
            "group_id", "shot_ids", "task", "reason", "model",
            "source_script_hash", "semantic_revision_id",
        )
        merged_runs = []
        newer_checkpoint_outputs = []
        for stored, checkpointed in zip(
            provisional.professional_runs, candidate.professional_runs, strict=True
        ):
            if any(
                getattr(stored, field) != getattr(checkpointed, field)
                for field in identity_fields
            ):
                raise DirectorPlanPlanningError(
                    "professional_checkpoint_mismatch", "Receipt identity changed during recovery"
                )
            if stored.status == "pending":
                current = checkpointed
                if checkpointed.status == "completed":
                    newer_checkpoint_outputs.append(checkpointed)
            elif checkpointed.status == "pending":
                current = stored
            elif checkpointed == stored:
                current = stored
            else:
                raise DirectorPlanPlanningError(
                    "professional_checkpoint_conflict", "Durable specialist receipts disagree"
                )
            merged_runs.append(current)

        from .professional_collaboration import (
            professional_status,
            reapply_professional_outputs,
        )
        from .professional_models import ProfessionalAssignment
        from .professional_collaboration import ROLE_OUTPUTS, merge_professional_output

        provisional_groups = provisional.groups
        for run in newer_checkpoint_outputs:
            assignment = ProfessionalAssignment(
                role_id=run.role_id, group_id=run.group_id, shot_ids=run.shot_ids,
                task=run.task, reason=run.reason,
            )
            output = ROLE_OUTPUTS[run.role_id].model_validate(run.output)
            provisional_groups, fields = merge_professional_output(
                provisional_groups, assignment, output
            )
            if fields != run.merged_fields:
                raise DirectorPlanPlanningError(
                    "professional_checkpoint_conflict", "Merged-field receipt changed"
                )

        merged_tuple = tuple(merged_runs)
        reconciled_provisional = provisional.model_copy(update={
            "groups": provisional_groups,
            "professional_runs": merged_tuple,
            "professional_status": professional_status(merged_tuple),
        })
        if reconciled_provisional != provisional:
            save(reconciled_provisional)

        groups = reapply_professional_outputs(candidate.groups, merged_tuple)
        return candidate.model_copy(update={
            "groups": groups,
            "professional_runs": merged_tuple,
            "professional_status": professional_status(merged_tuple),
        })

    def _save_creative_terminal(
        self, candidate: DirectorPlanRevision, provisional_revision_id: str | None
    ) -> DirectorPlanRevision:
        finalize = getattr(self._store, "finalize_professional_candidate", None)
        if provisional_revision_id and finalize is not None:
            return finalize(provisional_revision_id, candidate)
        self._store.save(candidate)
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
        professional_status: str = "not_requested",
        professional_decision: str = "No specialist contribution was requested.",
        professional_runs: tuple[ProfessionalRunReceipt, ...] = (),
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
            professional_status=professional_status,
            professional_decision=professional_decision,
            professional_runs=professional_runs,
        )

    async def _run_professional_assignments(
        self, input, draft, candidate, checkpoint
    ) -> DirectorPlanRevision:
        from .professional_collaboration import (
            ProfessionalOutputError,
            ROLE_OUTPUTS,
            merge_professional_output,
            professional_status,
        )
        from .professional_models import ProfessionalAssignment

        assignments = tuple(
            assignment if isinstance(assignment, ProfessionalAssignment)
            else ProfessionalAssignment.model_validate(assignment)
            for assignment in draft.professional_assignments
        )
        if not assignments:
            return candidate
        if len(assignments) != len(candidate.professional_runs):
            raise DirectorPlanPlanningError(
                "professional_checkpoint_mismatch", "Assignment receipt count changed"
            )
        try:
            from novelvideo.task_concurrency_settings import load_task_concurrency_settings

            concurrency = max(1, int(load_task_concurrency_settings().lanes["default"]))
        except Exception:
            # A failed settings read must not fan out without a bound.
            concurrency = 1
        semaphore = asyncio.Semaphore(concurrency)
        merge_lock = asyncio.Lock()
        base_groups = candidate.groups

        async def run_one(assignment, receipt):
            nonlocal candidate
            async with semaphore:
                try:
                    result = await self._planner.run_professional_assignment(
                        input, base_groups, assignment, receipt.run_id
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A provider exception may happen after a request was sent.
                    # Keep the status conservative and avoid leaking its payload.
                    await persist_receipt(
                        receipt, status="unknown",
                        error_code="professional_call_outcome_unknown",
                    )
                    return

                try:
                    if not isinstance(result, (tuple, list)) or not result:
                        raise ValueError("professional_output_envelope_invalid")
                    output = ROLE_OUTPUTS[assignment.role_id].model_validate(result[0])
                except Exception as exc:
                    await persist_receipt(
                        receipt, status="failed",
                        error_code=(
                            "professional_output_schema_invalid"
                            if isinstance(exc, ValidationError)
                            else "professional_output_envelope_invalid"
                        ),
                    )
                    return

                async with merge_lock:
                    try:
                        groups, merged_fields = merge_professional_output(
                            candidate.groups, assignment, output
                        )
                    except ProfessionalOutputError as exc:
                        updated = receipt.model_copy(update={
                            "status": "failed",
                            "error_code": exc.code,
                        })
                        runs = tuple(updated if item.run_id == receipt.run_id else item
                                      for item in candidate.professional_runs)
                        candidate = candidate.model_copy(update={
                            "professional_runs": runs,
                            "professional_status": professional_status(runs),
                        })
                        checkpoint("professional_pending", candidate)
                        self._save_professional_candidate(candidate)
                    except Exception:
                        updated = receipt.model_copy(update={
                            "status": "failed",
                            "error_code": "professional_merge_failed",
                        })
                        runs = tuple(updated if item.run_id == receipt.run_id else item
                                      for item in candidate.professional_runs)
                        candidate = candidate.model_copy(update={
                            "professional_runs": runs,
                            "professional_status": professional_status(runs),
                        })
                        checkpoint("professional_pending", candidate)
                        self._save_professional_candidate(candidate)
                    else:
                        updated = receipt.model_copy(update={
                            "status": "completed",
                            "summary": output.summary,
                            "output": output.model_dump(mode="json"),
                            "merged_fields": merged_fields,
                            "error_code": None,
                        })
                        runs = tuple(updated if item.run_id == receipt.run_id else item
                                      for item in candidate.professional_runs)
                        candidate = candidate.model_copy(update={
                            "groups": groups,
                            "professional_runs": runs,
                            "professional_status": professional_status(runs),
                        })
                        checkpoint("professional_pending", candidate)
                        self._save_professional_candidate(candidate)

        async def persist_receipt(receipt, *, status: str, error_code: str):
            nonlocal candidate
            async with merge_lock:
                updated = receipt.model_copy(update={
                    "status": status,
                    "error_code": error_code,
                })
                runs = tuple(updated if item.run_id == receipt.run_id else item
                              for item in candidate.professional_runs)
                candidate = candidate.model_copy(update={
                    "professional_runs": runs,
                    "professional_status": professional_status(runs),
                })
                checkpoint("professional_pending", candidate)
                self._save_professional_candidate(candidate)

        tasks = [
            asyncio.create_task(run_one(assignment, receipt))
            for assignment, receipt in zip(assignments, candidate.professional_runs, strict=True)
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            # Stop sibling work before returning control to the task runner. Calls
            # already sent remain pending and are marked unknown during recovery.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return candidate

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
