"""Explicit ideation, planning confirmation, and screenplay workflow API."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import resolve_project_scope
from novelvideo.script_creation.generation import GenerationConflict, GenerationValidation
from novelvideo.script_creation.store import DocumentConflict, DocumentNotFound, DocumentStore, DocumentValidation
from novelvideo.script_creation.workflow import CreativeWorkflowService, IdeaFields
from novelvideo.task_backend.client import cancel_project_task, enqueue_project_task
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager

router = APIRouter()
PREFIX = "/projects/{project}/creative-workflow"
TASK_TYPE = "creative_workflow"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateIdeationBody(Strict):
    brief_document_id: str = Field(min_length=1)
    brief_revision_id: str = Field(min_length=1)
    generate: bool = False
    client_mutation_id: str = Field(min_length=1, max_length=128)


class IdeaBody(Strict, IdeaFields):
    client_mutation_id: str = Field(min_length=1, max_length=128)


class EditIdeaBody(IdeaBody):
    base_revision_id: str = Field(min_length=1)


class MutationBody(Strict):
    client_mutation_id: str = Field(min_length=1, max_length=128)


class AdoptIdeaBody(MutationBody):
    candidate_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)


class StartPlanBody(Strict):
    ideation_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    idea_revision_id: str = Field(min_length=1)
    brief_document_id: str = Field(min_length=1)
    brief_revision_id: str = Field(min_length=1)
    script_mode: Literal["single", "series"]
    episode_count: int = Field(ge=1, le=100)
    instruction: str = Field(default="", max_length=8000)
    client_mutation_id: str = Field(min_length=1, max_length=128)


class ConfirmPlanBody(Strict):
    plan_revision_ids: dict[str, str]
    client_mutation_id: str = Field(min_length=1, max_length=128)


class AdoptScreenplayBody(MutationBody):
    revision_id: str = Field(min_length=1)


class ReviewScreenplayBody(MutationBody):
    revision_id: str = Field(min_length=1)
    plan_run_id: str = Field(min_length=1)


class RecoverScreenplayBody(MutationBody):
    draft_index: int = Field(ge=0)


async def _store(project: str, user: dict, role: str = "viewer"):
    resolved = await resolve_project_scope(project, user, required_role=role)
    store = DocumentStore(Path(resolved.state_dir) / "data.db")
    await store.initialize()
    service = CreativeWorkflowService(store)
    await service.initialize()
    return store, service, resolved


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, DocumentNotFound):
        return HTTPException(404, detail=str(exc))
    if isinstance(exc, (DocumentConflict, GenerationConflict)):
        return HTTPException(409, detail={"message": str(exc),
                                          "current_revision_id": getattr(exc, "current_revision_id", None)})
    if isinstance(exc, (DocumentValidation, GenerationValidation, ValueError)):
        return HTTPException(422, detail=str(exc))
    return HTTPException(500, detail="creative workflow operation failed")


def _check_task_scope(user: dict) -> None:
    if user.get("credential_kind") == "agent_session" and "tasks:submit" not in set(user.get("scopes") or []):
        raise HTTPException(403, detail="scope missing: 'tasks:submit'")


async def _queue(resolved, *, operation: str, scope: str, payload: dict):
    if resolved.ctx is None:
        raise HTTPException(409, detail={"code": "project_context_required"})
    queued = await enqueue_project_task(resolved.ctx, task_type=TASK_TYPE, queue_kind="default",
                                       episode=0, scope=scope,
                                       payload={"project_id": str(resolved.ctx.project_id),
                                                "operation": operation, **payload})
    return {
        "task_id": queued.task_state.task_id,
        "task_type": TASK_TYPE,
        "task_key": project_task_state_key(TASK_TYPE, str(resolved.ctx.project_id), 0, scope=scope),
        "scope": scope,
        "backend": queued.backend,
        "queue": queued.queue,
    }


async def _record_screenplay_review_task(store: DocumentStore, document_id: str,
                                         revision_id: str, attempt_id: str, task):
    if task.status in {"failed", "cancelled"}:
        return await store.fail_unstarted_screenplay_review(
            document_id, revision_id, attempt_id, str(task.task_id),
            getattr(task, "error", None) or f"review task {task.status} before review execution",
        )
    return await store.record_screenplay_review_task(
        document_id, revision_id, str(task.task_id), attempt_id=attempt_id)


async def _queue_generation_once(service: CreativeWorkflowService, resolved, run: dict[str, object]):
    if run.get("status") in {"running", "completed", "failed", "paused", "needs_review", "needs_rebase"}:
        return {"task_id": run.get("task_id")}
    if run.get("task_id"):
        return {"task_id": run["task_id"]}
    queued = await _queue(resolved, operation="generation", scope=f"run:{run['id']}",
                          payload={"run_id": run["id"]})
    await service.record_generation_task(str(run["id"]), str(queued["task_id"]))
    return queued


@router.get(PREFIX + "/state")
async def get_state(project: str, user: dict = Depends(get_api_user)):
    _, service, _ = await _store(project, user)
    return {"ok": True, "data": await service.state()}


@router.get(PREFIX + "/ideations/{ideation_id}")
async def get_ideation(project: str, ideation_id: str, user: dict = Depends(get_api_user)):
    _, service, _ = await _store(project, user)
    try:
        return {"ok": True, "data": await service.get_ideation(ideation_id)}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/ideations", status_code=status.HTTP_202_ACCEPTED)
async def create_ideation(project: str, body: CreateIdeationBody,
                          user: dict = Depends(get_api_user)):
    if body.generate:
        _check_task_scope(user)
    _, service, resolved = await _store(project, user, "editor")
    try:
        run = await service.create_ideation(**body.model_dump())
        if not body.generate:
            return {"ok": True, "data": {"run": run}}
        if run.get("status") != "pending" or run.get("task_id"):
            return {"ok": True, "data": {"run": run, "task_id": run.get("task_id")}}
        queued = await _queue(resolved, operation="generate_ideation", scope=f"ideation:{run['id']}",
                              payload={"ideation_id": run["id"]})
        await service.record_ideation_task(run["id"], queued["task_id"])
        return {"ok": True, "data": {"run": run, **queued}}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, GenerationConflict,
            GenerationValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/ideations/{ideation_id}/candidates")
async def add_candidate(project: str, ideation_id: str, body: IdeaBody,
                        user: dict = Depends(get_api_user)):
    _, service, _ = await _store(project, user, "editor")
    try:
        result = await service.add_candidate(ideation_id, **body.model_dump())
        return {"ok": True, "data": result}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.put(PREFIX + "/ideations/{ideation_id}/candidates/{candidate_id}")
async def edit_candidate(project: str, ideation_id: str, candidate_id: str, body: EditIdeaBody,
                         user: dict = Depends(get_api_user)):
    _, service, _ = await _store(project, user, "editor")
    try:
        result = await service.edit_candidate(ideation_id, candidate_id, **body.model_dump())
        return {"ok": True, "data": result}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/ideations/{ideation_id}/candidates/{candidate_id}/regenerate",
             status_code=status.HTTP_202_ACCEPTED)
async def regenerate_candidate(project: str, ideation_id: str, candidate_id: str, body: MutationBody,
                               user: dict = Depends(require_scope("tasks:submit"))):
    _, service, resolved = await _store(project, user, "editor")
    try:
        job = await service.queue_candidate_regeneration(
            ideation_id, candidate_id, client_mutation_id=body.client_mutation_id)
        if job.get("status") in {"completed", "failed", "needs_rebase"} or job.get("task_id"):
            return {"ok": True, "data": job}
        scope = f"ideation:{ideation_id}:candidate:{candidate_id}:{body.client_mutation_id}"
        queued = await _queue(resolved, operation="regenerate_candidate", scope=scope,
                              payload={"ideation_id": ideation_id, "candidate_id": candidate_id,
                                       "mutation_id": body.client_mutation_id})
        await service.set_regeneration_task(ideation_id, body.client_mutation_id, queued["task_id"])
        return {"ok": True, "data": {**job, **queued}}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/ideations/{ideation_id}/adopt")
async def adopt_idea(project: str, ideation_id: str, body: AdoptIdeaBody,
                     user: dict = Depends(get_api_user)):
    _, service, _ = await _store(project, user, "editor")
    try:
        return {"ok": True, "data": await service.adopt_idea(ideation_id, **body.model_dump())}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/plans", status_code=status.HTTP_202_ACCEPTED)
async def create_plan(project: str, body: StartPlanBody,
                      user: dict = Depends(require_scope("tasks:submit"))):
    _, service, resolved = await _store(project, user, "editor")
    try:
        run = await service.start_plan(**body.model_dump())
        queued = await _queue_generation_once(service, resolved, run)
        return {"ok": True, "data": {"run": run, **queued}}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, GenerationConflict,
            GenerationValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/plans/{run_id}/confirm", status_code=status.HTTP_202_ACCEPTED)
async def confirm_plan(project: str, run_id: str, body: ConfirmPlanBody,
                       user: dict = Depends(require_scope("tasks:submit"))):
    _, service, resolved = await _store(project, user, "editor")
    try:
        confirmed = await service.confirm_plan(run_id, **body.model_dump())
        screenplay = confirmed["screenplay_run"]
        queued = await _queue_generation_once(service, resolved, screenplay)
        return {"ok": True, "data": {**confirmed, "task_id": queued["task_id"]}}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, GenerationConflict,
            GenerationValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.get(PREFIX + "/runs/{run_id}")
async def get_run(project: str, run_id: str, user: dict = Depends(get_api_user)):
    _, service, _ = await _store(project, user)
    try:
        return {"ok": True, "data": await service.get_run(run_id)}
    except (DocumentNotFound, DocumentConflict) as exc:
        raise _error(exc) from exc


@router.delete(PREFIX + "/runs/{run_id}/pause")
async def pause_run(project: str, run_id: str,
                    user: dict = Depends(require_scope("tasks:submit"))):
    store, service, resolved = await _store(project, user, "editor")
    if resolved.ctx is None:
        raise HTTPException(409, detail={"code": "project_context_required"})
    try:
        run = await service.get_run(run_id)
        if run.get("mode") not in {"planning", "screenplay"}:
            raise GenerationValidation("creative workflow run required")
        if run["status"] not in {"pending", "running"}:
            return {"ok": True, "data": {"run": run}}
        task = get_task_manager().get_task_for_project(
            resolved.ctx, TASK_TYPE, 0, scope=f"run:{run_id}")
        if task is not None and task.status in {"submitting", "queued", "running"}:
            await cancel_project_task(resolved.ctx, task)
            if run["status"] == "running":
                return {"ok": True, "data": {"run": await service.get_run(run_id)}}
        run.update(status="paused", error="用户停止了创作任务")
        paused = await store.generation_update(run_id, run, expected_task_id=run.get("task_id"))
        return {"ok": True, "data": {"run": paused}}
    except (DocumentNotFound, DocumentConflict, DocumentValidation,
            GenerationConflict, GenerationValidation) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/runs/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_run(project: str, run_id: str,
                    user: dict = Depends(require_scope("tasks:submit"))):
    store, service, resolved = await _store(project, user, "editor")
    if resolved.ctx is None:
        raise HTTPException(409, detail={"code": "project_context_required"})
    try:
        run = await service.get_run(run_id)
        if run.get("mode") not in {"planning", "screenplay"}:
            raise GenerationValidation("creative workflow run required")
        if run["status"] == "running":
            task = get_task_manager().get_task_for_project(
                resolved.ctx, TASK_TYPE, 0, scope=f"run:{run_id}")
            if task is not None and task.status in {"submitting", "queued", "running"}:
                raise GenerationConflict("creative workflow run is still running")
            run["status"] = "paused"
            run = await store.generation_update(run_id, run, expected_task_id=run.get("task_id"))
        if run["status"] not in {"failed", "paused", "pending"}:
            raise GenerationConflict("only a failed or paused run can be retried")
        if run["status"] in {"failed", "paused"}:
            run = await store.generation_requeue(run_id)
        queued = await _queue(resolved, operation="generation", scope=f"run:{run_id}",
                              payload={"run_id": run_id})
        run = await service.record_generation_task(run_id, str(queued["task_id"]))
        return {"ok": True, "data": {"run": run, **queued}}
    except (DocumentNotFound, DocumentConflict, DocumentValidation,
            GenerationConflict, GenerationValidation) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/runs/{run_id}/recover-screenplay", status_code=status.HTTP_202_ACCEPTED)
async def recover_screenplay(project: str, run_id: str, body: RecoverScreenplayBody,
                             user: dict = Depends(get_api_user)):
    _, service, _ = await _store(project, user, "editor")
    try:
        recovered = await service.recover_screenplay_draft(
            run_id, draft_index=body.draft_index,
            client_mutation_id=body.client_mutation_id,
        )
        return {"ok": True, "data": recovered}
    except (DocumentNotFound, DocumentConflict, DocumentValidation,
            GenerationConflict, GenerationValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/screenplays/{document_id}/adopt")
async def adopt_screenplay(project: str, document_id: str, body: AdoptScreenplayBody,
                           user: dict = Depends(get_api_user)):
    _, service, _ = await _store(project, user, "editor")
    try:
        return {"ok": True, "data": await service.adopt_screenplay(document_id, **body.model_dump())}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.post(PREFIX + "/screenplays/{document_id}/review", status_code=status.HTTP_202_ACCEPTED)
async def review_screenplay(project: str, document_id: str, body: ReviewScreenplayBody,
                            user: dict = Depends(require_scope("tasks:submit"))):
    store, service, resolved = await _store(project, user, "editor")
    try:
        review = await service.queue_screenplay_review(
            document_id, body.revision_id, plan_run_id=body.plan_run_id,
            client_mutation_id=body.client_mutation_id)
        attempt_id = review["attempt_id"]
        base_scope = f"screenplay-review:{document_id}:{body.revision_id}"
        legacy_attempt = attempt_id == review.get("mutation_id")
        scope = base_scope if legacy_attempt else f"{base_scope}:{attempt_id}"
        if resolved.ctx is None:
            raise HTTPException(409, detail={"code": "project_context_required"})
        existing = get_task_manager().get_task_for_project(resolved.ctx, TASK_TYPE, 0, scope=scope)
        if existing is not None:
            saved = await _record_screenplay_review_task(
                store, document_id, body.revision_id, attempt_id, existing)
            return {"ok": True, "data": saved}
        if review.get("status") != "pending" or review.get("task_id"):
            return {"ok": True, "data": review}
        if legacy_attempt:
            review = await store.mark_screenplay_review_submission_unknown(
                document_id, body.revision_id, attempt_id,
                "legacy review attempt has no durable task receipt; duplicate enqueue is blocked",
            )
            return {"ok": True, "data": review}
        claimed = await store.claim_screenplay_review_submission(
            document_id, body.revision_id, attempt_id)
        if not claimed:
            latest = await store.get_screenplay_review(document_id, body.revision_id)
            attempt = next((item for item in (latest or {}).get("attempts", [])
                            if item.get("attempt_id") == attempt_id), None)
            if attempt and attempt.get("status") == "pending" and not attempt.get("task_id"):
                attempt = await store.mark_screenplay_review_submission_unknown(
                    document_id, body.revision_id, attempt_id,
                    "enqueue claim exists but no durable task receipt was found",
                )
            return {"ok": True, "data": attempt or latest}
        try:
            queued = await _queue(resolved, operation="review_screenplay", scope=scope,
                                  payload={"document_id": document_id,
                                           "revision_id": body.revision_id,
                                           "attempt_id": attempt_id})
        except Exception as exc:
            await store.mark_screenplay_review_submission_unknown(
                document_id, body.revision_id, attempt_id, str(exc))
            raise
        saved = await store.record_screenplay_review_task(
            document_id, body.revision_id, str(queued["task_id"]), attempt_id=attempt_id)
        return {"ok": True, "data": {**saved, **queued}}
    except (DocumentNotFound, DocumentConflict, DocumentValidation, ValueError) as exc:
        raise _error(exc) from exc


@router.get(PREFIX + "/screenplays/{document_id}/reviews/{revision_id}")
async def get_screenplay_review(project: str, document_id: str, revision_id: str,
                               user: dict = Depends(get_api_user)):
    store, service, resolved = await _store(project, user)
    try:
        review = await service.store.get_screenplay_review(document_id, revision_id)
        if review and resolved.ctx is not None and review.get("status") in {"pending", "submission_unknown"}:
            attempt_id = review.get("attempt_id")
            base_scope = f"screenplay-review:{document_id}:{revision_id}"
            legacy_attempt = attempt_id == review.get("mutation_id")
            scope = base_scope if legacy_attempt else f"{base_scope}:{attempt_id}"
            task = get_task_manager().get_task_for_project(resolved.ctx, TASK_TYPE, 0, scope=scope)
            if task is not None:
                await _record_screenplay_review_task(
                    store, document_id, revision_id, attempt_id, task)
                review = await store.get_screenplay_review(document_id, revision_id)
            elif (review.get("status") == "pending" and review.get("enqueue_claimed")):
                await store.mark_screenplay_review_submission_unknown(
                    document_id, revision_id, attempt_id,
                    "enqueue claim exists but no durable task receipt was found",
                )
                review = await store.get_screenplay_review(document_id, revision_id)
        return {"ok": True, "data": review}
    except (DocumentNotFound, DocumentConflict, DocumentValidation) as exc:
        raise _error(exc) from exc
