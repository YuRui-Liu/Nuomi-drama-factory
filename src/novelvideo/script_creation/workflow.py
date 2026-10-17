"""Durable ideation, planning confirmation, and screenplay operations."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .generation import GenerationConflict, GenerationService, GenerationValidation
from .store import DocumentConflict, DocumentNotFound, DocumentStore, DocumentValidation


class IdeaFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    one_line: str = Field(min_length=1, max_length=1000)
    protagonist_conflict: str = Field(min_length=1, max_length=2000)
    highlights: str = Field(min_length=1, max_length=2000)
    final_choice: str = Field(min_length=1, max_length=2000)


class IdeationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[IdeaFields] = Field(min_length=3, max_length=3)


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _idea_markdown(fields: dict[str, str]) -> str:
    return (f"# {fields['one_line']}\n\n"
            f"一句话立意：{fields['one_line']}\n\n"
            f"主角与核心冲突：{fields['protagonist_conflict']}\n\n"
            f"故事看点：{fields['highlights']}\n\n"
            f"人物最终抉择：{fields['final_choice']}")


def _candidate_view(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if key != "revision_history"}


def _ideation_view(data: dict[str, Any]) -> dict[str, Any]:
    return {**data, "candidates": [_candidate_view(item) for item in data.get("candidates", [])]}


def _revision_fields(candidate: dict[str, Any], revision_id: str) -> dict[str, str]:
    if candidate.get("revision_id") == revision_id:
        return {key: candidate[key] for key in IdeaFields.model_fields}
    for revision in candidate.get("revision_history", []):
        if revision["revision_id"] == revision_id:
            return dict(revision["fields"])
    raise DocumentNotFound("idea revision not found")


def _candidate_revision(candidate: dict[str, Any], revision_id: str) -> dict[str, Any]:
    return {
        "id": candidate["id"],
        "revision_id": revision_id,
        "source_brief_revision_id": candidate["source_brief_revision_id"],
        "source_run_id": candidate.get("source_run_id"),
        **_revision_fields(candidate, revision_id),
    }


class CreativeWorkflowService:
    def __init__(self, store: DocumentStore):
        self.store = store

    async def initialize(self) -> None:
        await self.store.initialize()
        async with self.store._db() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS script_ideation_runs (
                    id TEXT PRIMARY KEY, mutation_id TEXT UNIQUE NOT NULL,
                    request_hash TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS script_ideation_mutations (
                    ideation_id TEXT NOT NULL, mutation_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL, response TEXT NOT NULL,
                    PRIMARY KEY(ideation_id, mutation_id));
            """)
            await db.commit()

    async def _load(self, ideation_id: str) -> dict[str, Any]:
        await self.initialize()
        async with self.store._db() as db:
            row = await (await db.execute("SELECT data FROM script_ideation_runs WHERE id=?",
                                          (ideation_id,))).fetchone()
        if row is None:
            raise DocumentNotFound("ideation run not found")
        return json.loads(row["data"])

    async def _update_ideation(self, ideation_id: str, mutate, *, expected_task_id: str | None = None,
                               allowed_statuses: set[str] | None = None) -> dict[str, Any]:
        """Read and update the latest state under one SQLite write transaction."""
        await self.initialize()
        async with self.store._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute("SELECT data FROM script_ideation_runs WHERE id=?",
                                              (ideation_id,))).fetchone()
                if row is None:
                    raise DocumentNotFound("ideation run not found")
                data = json.loads(row["data"])
                if expected_task_id is not None and data.get("task_id") != expected_task_id:
                    raise DocumentConflict("ideation task superseded")
                if allowed_statuses is not None and data.get("status") not in allowed_statuses:
                    await db.rollback()
                    return _ideation_view(data)
                mutate(data)
                data["updated_at"] = _now()
                await db.execute("UPDATE script_ideation_runs SET data=?,updated_at=? WHERE id=?",
                                 (json.dumps(data, ensure_ascii=False), data["updated_at"], ideation_id))
                await db.commit()
                return _ideation_view(data)
            except Exception:
                await db.rollback()
                raise

    async def _mutate(self, ideation_id: str, mutation_id: str, payload: Any, mutate):
        if not mutation_id.strip():
            raise DocumentValidation("client_mutation_id required")
        await self.initialize()
        digest = _digest(payload)
        async with self.store._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                old = await (await db.execute(
                    "SELECT request_hash,response FROM script_ideation_mutations WHERE ideation_id=? AND mutation_id=?",
                    (ideation_id, mutation_id))).fetchone()
                if old:
                    if old["request_hash"] != digest:
                        raise DocumentConflict("ideation mutation id reused with different payload")
                    await db.rollback()
                    return json.loads(old["response"])
                row = await (await db.execute("SELECT data FROM script_ideation_runs WHERE id=?",
                                              (ideation_id,))).fetchone()
                if row is None:
                    raise DocumentNotFound("ideation run not found")
                data = json.loads(row["data"])
                response = mutate(data)
                data["updated_at"] = _now()
                encoded = json.dumps(data, ensure_ascii=False)
                encoded_response = json.dumps(response, ensure_ascii=False)
                await db.execute("UPDATE script_ideation_runs SET data=?,updated_at=? WHERE id=?",
                                 (encoded, data["updated_at"], ideation_id))
                await db.execute("INSERT INTO script_ideation_mutations VALUES (?,?,?,?)",
                                 (ideation_id, mutation_id, digest, encoded_response))
                await db.commit()
                return json.loads(encoded_response)
            except Exception:
                await db.rollback()
                raise

    async def _mutation_replay(self, ideation_id: str, mutation_id: str, payload: Any):
        if not mutation_id.strip():
            raise DocumentValidation("client_mutation_id required")
        await self.initialize()
        digest = _digest(payload)
        async with self.store._db() as db:
            row = await (await db.execute(
                "SELECT request_hash,response FROM script_ideation_mutations "
                "WHERE ideation_id=? AND mutation_id=?", (ideation_id, mutation_id))).fetchone()
        if row is None:
            return None
        if row["request_hash"] != digest:
            raise DocumentConflict("ideation mutation id reused with different payload")
        return json.loads(row["response"])

    async def create_ideation(self, *, brief_document_id: str, brief_revision_id: str,
                              client_mutation_id: str, generate: bool = False) -> dict[str, Any]:
        if not client_mutation_id.strip():
            raise DocumentValidation("client_mutation_id required")
        brief = await self.store.get(brief_document_id)
        if brief.kind != "brief" or not brief.revision.markdown.strip():
            raise DocumentValidation("saved creative brief required")
        if brief.current_revision_id != brief_revision_id:
            raise DocumentConflict("brief revision changed", current_revision_id=brief.current_revision_id)
        await self.initialize()
        request = {"brief_document_id": brief_document_id, "brief_revision_id": brief_revision_id,
                   "generate": bool(generate)}
        digest = _digest(request)
        async with self.store._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute("SELECT * FROM script_ideation_runs WHERE mutation_id=?",
                                              (client_mutation_id,))).fetchone()
                if row:
                    if row["request_hash"] != digest:
                        raise DocumentConflict("ideation mutation id reused with different payload")
                    await db.rollback()
                    return _ideation_view(json.loads(row["data"]))
                stamp, ideation_id = _now(), _id()
                data = {
                    "id": ideation_id, "status": "pending" if generate else "completed",
                    "brief_document_id": brief_document_id, "brief_revision_id": brief_revision_id,
                    "candidates": [], "adopted": None, "adoption_history": [],
                    "generation_requested": bool(generate), "task_id": None, "error": None,
                    "regenerations": {}, "created_at": stamp, "updated_at": stamp,
                }
                await db.execute("INSERT INTO script_ideation_runs VALUES (?,?,?,?,?,?)",
                                 (ideation_id, client_mutation_id, digest,
                                  json.dumps(data, ensure_ascii=False), stamp, stamp))
                await db.commit()
                return _ideation_view(data)
            except Exception:
                await db.rollback()
                raise

    async def get_ideation(self, ideation_id: str) -> dict[str, Any]:
        return _ideation_view(await self._load(ideation_id))

    async def list_ideations(self) -> list[dict[str, Any]]:
        await self.initialize()
        async with self.store._db() as db:
            rows = await (await db.execute("SELECT data FROM script_ideation_runs ORDER BY created_at,id")).fetchall()
        return [_ideation_view(json.loads(row["data"])) for row in rows]

    async def _set_ideation_status(self, ideation_id: str, *, status: str, task_id: str | None = None,
                                   error: str | None = None, expected_task_id: str | None = None,
                                   allowed_statuses: set[str] | None = None) -> dict[str, Any]:
        def mutate(data):
            if task_id is not None and data.get("task_id") not in {None, task_id}:
                raise DocumentConflict("ideation already has a different task")
            data.update(status=status, error=error)
            if task_id is not None:
                data["task_id"] = task_id
        return await self._update_ideation(ideation_id, mutate, expected_task_id=expected_task_id,
                                           allowed_statuses=allowed_statuses)

    async def record_ideation_task(self, ideation_id: str, task_id: str) -> dict[str, Any]:
        def mutate(data):
            if data.get("task_id") not in {None, task_id}:
                raise DocumentConflict("ideation already has a different task")
            data["task_id"] = task_id
        return await self._update_ideation(ideation_id, mutate)

    async def record_generation_task(self, run_id: str, task_id: str) -> dict[str, Any]:
        service = GenerationService(self.store)
        run = await service.get(run_id)
        if run.get("task_id") not in {None, task_id}:
            raise GenerationConflict("generation already has a different task")
        run["task_id"] = task_id
        return await self.store.generation_update(run_id, run)

    async def add_candidate(self, ideation_id: str, *, one_line: str, protagonist_conflict: str,
                            highlights: str, final_choice: str, client_mutation_id: str) -> dict[str, Any]:
        fields = IdeaFields(one_line=one_line, protagonist_conflict=protagonist_conflict,
                            highlights=highlights, final_choice=final_choice).model_dump()
        ideation = await self._load(ideation_id)
        if ideation["status"] not in {"completed", "failed"}:
            raise DocumentConflict("ideation generation is still active")
        candidate_document = await self.store.create(
            kind="idea_candidate", title="自写立意", markdown=_idea_markdown(fields),
            client_mutation_id=f"creative-idea:{ideation_id}:{client_mutation_id}",
            source_origin={"ideation_id": ideation_id,
                           "brief_revision_id": ideation["brief_revision_id"], "source_run_id": None})
        def mutate(data):
            if data["status"] not in {"completed", "failed"}:
                raise DocumentConflict("ideation generation is still active")
            candidate = {"id": candidate_document.id, "revision_id": candidate_document.current_revision_id,
                         "source_brief_revision_id": data["brief_revision_id"], "source_run_id": None,
                         **fields, "revision_history": []}
            candidate["revision_history"].append({"revision_id": candidate["revision_id"], "fields": fields})
            data["candidates"].append(candidate)
            return _candidate_view(candidate)
        return await self._mutate(ideation_id, client_mutation_id, fields, mutate)

    async def create_idea_candidate(self, ideation_id: str, **fields) -> dict[str, Any]:
        """Compatibility name used by the direct service contract."""
        return await self.add_candidate(ideation_id, **fields)

    async def edit_candidate(self, ideation_id: str, candidate_id: str, *, base_revision_id: str,
                             one_line: str, protagonist_conflict: str, highlights: str,
                             final_choice: str, client_mutation_id: str) -> dict[str, Any]:
        fields = IdeaFields(one_line=one_line, protagonist_conflict=protagonist_conflict,
                            highlights=highlights, final_choice=final_choice).model_dump()
        payload = {"candidate_id": candidate_id, "base_revision_id": base_revision_id, **fields}
        replay = await self._mutation_replay(ideation_id, client_mutation_id, payload)
        if replay is not None:
            return replay
        data = await self._load(ideation_id)
        current = next((item for item in data["candidates"] if item["id"] == candidate_id), None)
        if current is None:
            raise DocumentNotFound("idea candidate not found")
        if current["revision_id"] != base_revision_id:
            raise DocumentConflict("idea candidate changed", current_revision_id=current["revision_id"])
        revised_document = await self.store.save(
            candidate_id, base_revision_id=base_revision_id, markdown=_idea_markdown(fields),
            client_mutation_id=f"creative-idea-edit:{ideation_id}:{client_mutation_id}")
        def mutate(data):
            candidate = next((item for item in data["candidates"] if item["id"] == candidate_id), None)
            if candidate is None:
                raise DocumentNotFound("idea candidate not found")
            if candidate["revision_id"] != base_revision_id:
                raise DocumentConflict("idea candidate changed", current_revision_id=candidate["revision_id"])
            candidate["revision_id"] = revised_document.current_revision_id
            candidate.update(fields)
            candidate["revision_history"].append({"revision_id": candidate["revision_id"], "fields": fields})
            return _candidate_view(candidate)
        return await self._mutate(ideation_id, client_mutation_id, payload, mutate)

    async def adopt_idea(self, ideation_id: str, *, candidate_id: str, revision_id: str,
                         client_mutation_id: str) -> dict[str, Any]:
        payload = {"candidate_id": candidate_id, "revision_id": revision_id}
        def mutate(data):
            candidate = next((item for item in data["candidates"] if item["id"] == candidate_id), None)
            if candidate is None:
                raise DocumentNotFound("idea candidate not found")
            idea = _candidate_revision(candidate, revision_id)
            if data.get("adopted"):
                data["adoption_history"].append(data["adopted"])
            data["adopted"] = {"candidate_id": candidate_id, "revision_id": revision_id,
                               "brief_revision_id": idea["source_brief_revision_id"]}
            return {"adopted": data["adopted"]}
        return await self._mutate(ideation_id, client_mutation_id, payload, mutate)

    async def queue_candidate_regeneration(self, ideation_id: str, candidate_id: str, *,
                                           client_mutation_id: str) -> dict[str, Any]:
        current = await self._load(ideation_id)
        previous = current.get("regenerations", {}).get(client_mutation_id)
        if previous is not None:
            if previous["candidate_id"] != candidate_id:
                raise DocumentConflict("regeneration mutation id reused for another candidate")
            return dict(previous)
        payload = {"candidate_id": candidate_id, "operation": "regenerate"}
        def mutate(data):
            candidate = next((item for item in data["candidates"] if item["id"] == candidate_id), None)
            if candidate is None:
                raise DocumentNotFound("idea candidate not found")
            regeneration = {"ideation_id": ideation_id, "candidate_id": candidate_id,
                            "mutation_id": client_mutation_id, "base_revision_id": candidate["revision_id"],
                            "status": "pending", "task_id": None, "error": None}
            data["regenerations"][client_mutation_id] = regeneration
            return regeneration
        return await self._mutate(ideation_id, client_mutation_id, payload, mutate)

    async def set_regeneration_task(self, ideation_id: str, mutation_id: str, task_id: str) -> None:
        def mutate(data):
            job = data.get("regenerations", {}).get(mutation_id)
            if job is None:
                raise DocumentNotFound("candidate regeneration not found")
            if job.get("task_id") not in {None, task_id}:
                raise DocumentConflict("regeneration already has another task")
            job["task_id"] = task_id
        await self._update_ideation(ideation_id, mutate)

    async def execute_ideation(self, ideation_id: str, *, runtime: Any, task_id: str) -> dict[str, Any]:
        data = await self._load(ideation_id)
        if data["status"] == "completed":
            return _ideation_view(data)
        if data["status"] == "needs_rebase":
            return _ideation_view(data)
        if not data.get("generation_requested"):
            return _ideation_view(data)
        data = await self._set_ideation_status(ideation_id, status="running", task_id=task_id,
                                               allowed_statuses={"pending", "running"})
        if data["status"] != "running":
            return data
        try:
            brief = await self.store.get(data["brief_document_id"])
            if brief.current_revision_id != data["brief_revision_id"]:
                return await self._set_ideation_status(
                    ideation_id, status="needs_rebase", task_id=task_id,
                    error="创作简报已变化，请基于当前版本重新生成",
                    expected_task_id=task_id, allowed_statuses={"running"})
            brief = await self.store.get_revision(data["brief_document_id"], data["brief_revision_id"])
            prompt = (
                "根据下列已保存的创作简报，提出恰好三个有明显差异的立意方向。每个方向需清晰给出一句话立意、"
                "主角与核心冲突、故事看点、人物最终抉择。避免复述简报。\n\n" + brief.revision.markdown
            )
            generated = await runtime.run_structured(prompt=prompt, output_type=IdeationBatch)
            batch = IdeationBatch.model_validate(generated)
            candidates = []
            for item in batch.candidates:
                fields = item.model_dump()
                candidate_document = await self.store.create(
                    kind="idea_candidate", title=f"立意方向 {len(candidates) + 1}",
                    markdown=_idea_markdown(fields),
                    client_mutation_id=f"creative-idea-generated:{ideation_id}:{task_id}:{len(candidates) + 1}",
                    source_origin={"ideation_id": ideation_id,
                                   "brief_revision_id": data["brief_revision_id"],
                                   "source_run_id": ideation_id})
                candidate = {"id": candidate_document.id, "revision_id": candidate_document.current_revision_id,
                             "source_brief_revision_id": data["brief_revision_id"],
                             "source_run_id": ideation_id, **fields, "revision_history": []}
                candidate["revision_history"].append({"revision_id": candidate["revision_id"], "fields": fields})
                candidates.append(candidate)
            def finish(latest):
                latest["candidates"] = candidates
                latest["status"], latest["error"] = "completed", None
            return await self._update_ideation(ideation_id, finish, expected_task_id=task_id,
                                               allowed_statuses={"running"})
        except Exception as exc:
            await self._set_ideation_status(ideation_id, status="failed", task_id=task_id,
                                            error=str(exc), expected_task_id=task_id,
                                            allowed_statuses={"running"})
            raise

    async def execute_candidate_regeneration(self, ideation_id: str, candidate_id: str,
                                             mutation_id: str, *, runtime: Any,
                                             task_id: str) -> dict[str, Any]:
        data = await self._load(ideation_id)
        job = data.get("regenerations", {}).get(mutation_id)
        if job is None:
            raise DocumentNotFound("candidate regeneration not found")
        if job["status"] == "completed":
            candidate = next(item for item in data["candidates"] if item["id"] == candidate_id)
            return _candidate_view(candidate)
        candidate = next((item for item in data["candidates"] if item["id"] == candidate_id), None)
        if candidate is None:
            raise DocumentNotFound("idea candidate not found")
        if candidate["revision_id"] != job["base_revision_id"]:
            updated = await self._update_ideation(ideation_id, lambda latest: latest["regenerations"][mutation_id].update(
                status="needs_rebase", error="候选已被编辑，请基于当前版本重新生成"))
            candidate = next(item for item in updated["candidates"] if item["id"] == candidate_id)
            return _candidate_view(candidate)
        try:
            brief = await self.store.get(data["brief_document_id"])
            if brief.current_revision_id != data["brief_revision_id"]:
                updated = await self._update_ideation(ideation_id,
                    lambda latest: latest["regenerations"][mutation_id].update(
                        status="needs_rebase", error="创作简报已变化，请基于当前版本重新生成"))
                candidate = next(item for item in updated["candidates"] if item["id"] == candidate_id)
                return _candidate_view(candidate)
            fields = _candidate_revision(candidate, job["base_revision_id"])
            prompt = (
                "只重新创作这一张立意卡，保持与其他候选独立。输出一句话立意、主角与核心冲突、故事看点、人物最终抉择。"
                "请基于原简报和原候选形成有实质差异、完整且可执行的新方向。\n\n"
                f"创作简报：\n{brief.revision.markdown}\n\n原候选：\n" +
                "\n".join(f"{key}: {fields[key]}" for key in IdeaFields.model_fields)
            )
            generated = await runtime.run_structured(prompt=prompt, output_type=IdeaFields)
            new_fields = IdeaFields.model_validate(generated).model_dump()
            revised_document = await self.store.save(
                candidate_id, base_revision_id=job["base_revision_id"], markdown=_idea_markdown(new_fields),
                client_mutation_id=f"creative-idea-regenerate:{ideation_id}:{mutation_id}")
            def finish(latest):
                latest_candidate = next(item for item in latest["candidates"] if item["id"] == candidate_id)
                latest_job = latest["regenerations"][mutation_id]
                if latest_candidate["revision_id"] != latest_job["base_revision_id"]:
                    latest_job.update(status="needs_rebase", error="候选已被编辑，请基于当前版本重新生成")
                    return
                latest_candidate["revision_id"] = revised_document.current_revision_id
                latest_candidate.update(new_fields)
                latest_candidate["revision_history"].append({"revision_id": latest_candidate["revision_id"],
                                                             "fields": new_fields})
                latest_job.update(status="completed", task_id=task_id, error=None)
            latest = await self._update_ideation(ideation_id, finish)
            latest_candidate = next(item for item in latest["candidates"] if item["id"] == candidate_id)
            return _candidate_view(latest_candidate)
        except Exception as exc:
            failure_message = str(exc)
            def mark_failed(latest):
                current_job = latest.get("regenerations", {}).get(mutation_id)
                if current_job and current_job.get("status") not in {"completed", "needs_rebase"}:
                    current_job.update(status="failed", task_id=task_id, error=failure_message)
            await self._update_ideation(ideation_id, mark_failed)
            raise

    async def start_plan(self, *, ideation_id: str, candidate_id: str, idea_revision_id: str,
                         brief_document_id: str, brief_revision_id: str, script_mode: str,
                         episode_count: int, instruction: str, client_mutation_id: str) -> dict[str, Any]:
        data = await self._load(ideation_id)
        adopted = data.get("adopted") or {}
        if adopted.get("candidate_id") != candidate_id or adopted.get("revision_id") != idea_revision_id:
            raise GenerationValidation("adopt this exact idea revision before starting a plan")
        if data["brief_document_id"] != brief_document_id or data["brief_revision_id"] != brief_revision_id:
            raise GenerationConflict("the selected idea belongs to a different brief revision")
        brief = await self.store.get(brief_document_id)
        if brief.current_revision_id != brief_revision_id:
            raise GenerationConflict("brief revision changed", current_revision_id=brief.current_revision_id)
        candidate = next((item for item in data["candidates"] if item["id"] == candidate_id), None)
        if candidate is None:
            raise DocumentNotFound("idea candidate not found")
        idea = _candidate_revision(candidate, idea_revision_id)
        source_idea = {**idea, "candidate_id": candidate_id, "revision_id": idea_revision_id}
        run = await GenerationService(self.store).start(
            mode="planning", brief_id=brief_document_id, script_mode=script_mode,
            episode_count=episode_count, episode_number=1, instruction=instruction,
            mutation_id=f"creative-plan:{client_mutation_id}", source_idea=source_idea,
            source_ideation_id=ideation_id, source_candidate_id=candidate_id)
        return {**run, "phase": "planning"}

    async def confirm_plan(self, plan_run_id: str, *, plan_revision_ids: dict[str, str],
                           client_mutation_id: str) -> dict[str, Any]:
        generation = GenerationService(self.store)
        plan = await generation.get(plan_run_id)
        if plan.get("mode") != "planning" or plan.get("status") != "completed":
            raise GenerationValidation("a completed planning run is required")
        idea = plan.get("source_idea") or {}
        ideation_id = plan.get("source_ideation_id")
        ideation = await self._load(str(ideation_id))
        candidate = next((item for item in ideation["candidates"] if item["id"] == idea.get("candidate_id")), None)
        if candidate is None:
            raise DocumentNotFound("source idea candidate not found")
        _candidate_revision(candidate, str(idea.get("revision_id")))
        output_candidates: dict[str, str | None] = {}
        for step in plan["steps"]:
            output = step.get("output") or {}
            if output.get("kind") == "document" and output.get("document_id"):
                output_candidates[output["document_id"]] = None
            elif output.get("kind") == "candidate" and output.get("candidate_id") and output.get("document_id"):
                output_candidates[output["document_id"]] = output["candidate_id"]
            else:
                raise GenerationValidation("planning output is incomplete")
        if set(plan_revision_ids) != set(output_candidates):
            raise GenerationConflict(
                "planning output document collection changed "
                f"(expected={sorted(output_candidates)}, provided={sorted(plan_revision_ids)})")
        for document_id, revision_id in plan_revision_ids.items():
            document = await self.store.get(document_id)
            if document.current_revision_id != revision_id:
                raise GenerationConflict("a planning revision has changed",
                                         current_revision_id=document.current_revision_id)
        expected = {plan["brief_id"]: plan["brief_revision_id"],
                    idea["candidate_id"]: idea["revision_id"], **plan_revision_ids}
        revisions_hash = _digest({"plan_run_id": plan_run_id, "revisions": expected})
        screenplay_payload = await generation.prepare(
            mode="screenplay", brief_id=plan["brief_id"], script_mode=plan["script_mode"],
            episode_count=plan["episode_count"], episode_number=1, instruction=plan["instruction"],
            mutation_id=f"screenplay:{plan_run_id}:{revisions_hash}",
            source_idea=idea, source_ideation_id=ideation_id,
            source_candidate_id=idea["candidate_id"], confirmed_revisions=expected,
            plan_run_id=plan_run_id)
        return await self.store.generation_confirm_plan(
            plan_run_id, screenplay_data=screenplay_payload, mutation_id=client_mutation_id,
            revisions_hash=revisions_hash, frozen_revisions=expected,
            output_candidates=output_candidates)

    async def execute(self, run_id: str, *, runtime: Any, task_id: str,
                      cancel_check=None, commit_guard=None, progress=None) -> dict[str, Any]:
        return await GenerationService(self.store).execute(
            run_id, runtime=runtime, task_id=task_id, cancel_check=cancel_check,
            commit_guard=commit_guard, progress=progress)

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return await GenerationService(self.store).get(run_id)

    async def queue_screenplay_review(self, document_id: str, revision_id: str, *,
                                      plan_run_id: str, client_mutation_id: str):
        return await self.store.create_screenplay_review(
            document_id, revision_id, plan_run_id=plan_run_id, mutation_id=client_mutation_id)

    async def recover_screenplay_draft(self, run_id: str, *, draft_index: int,
                                       client_mutation_id: str):
        run = await self.get_run(run_id)
        if run.get("mode") != "screenplay" or run.get("status") != "needs_review":
            raise GenerationValidation("only a needs-review screenplay run can recover a draft")
        plan_run_id = run.get("plan_run_id")
        dependencies = run.get("confirmed_revisions")
        if not plan_run_id or not isinstance(dependencies, dict):
            raise GenerationValidation("screenplay run has no confirmed planning dependencies")
        await self._validate_confirmed_dependencies(
            plan_run_id, dependencies, screenplay_run_id=run_id)
        return await self.store.recover_screenplay_draft(
            run_id, draft_index=draft_index, mutation_id=client_mutation_id)

    async def _screenplay_review_history(self, document_id: str, revision_id: str, *,
                                         plan_run_id: str, confirmed_revisions: dict[str, str]):
        from .craft_store import text_hash

        lineage = await self.store.get_screenplay_review_lineage(document_id, revision_id)
        completed = []
        for ancestor in lineage:
            for review in ancestor["attempts"]:
                result = review.get("result")
                if (review.get("plan_run_id") != plan_run_id
                        or review.get("confirmed_revisions") != confirmed_revisions
                        or review.get("status") not in {"passed", "needs_revision"}
                        or not isinstance(result, dict)
                        or result.get("status") != review.get("status")
                        or result.get("candidate_hash") != text_hash(ancestor["markdown"])):
                    continue
                issues = result.get("issues")
                if (not isinstance(issues, list)
                        or any(not isinstance(issue, dict) or not issue.get("id")
                               or issue.get("severity") not in {"blocker", "suggestion"}
                               for issue in issues)):
                    continue
                completed.append({"markdown": ancestor["markdown"], "result": result})

        if not completed:
            return None, []

        # The list is nearest revision first, and attempts are newest first. An
        # empty issue list on the selected completed review is a resolution boundary.
        selected = completed[0]
        issue_catalog = {}
        for candidate in completed:
            for issue in candidate["result"]["issues"]:
                issue_catalog.setdefault(issue["id"], issue)

        issue_history = []
        known = set()
        pending = list(selected["result"]["issues"])
        while pending:
            issue = pending.pop(0)
            issue_id = issue["id"]
            if issue_id in known:
                continue
            known.add(issue_id)
            issue_history.append(issue)
            prior_issue_id = issue.get("prior_issue_id")
            if prior_issue_id is not None and prior_issue_id not in known:
                ancestor = issue_catalog.get(prior_issue_id)
                if ancestor is None:
                    raise DocumentValidation(
                        "completed screenplay review has incomplete prior issue history")
                pending.append(ancestor)
        return selected["markdown"], issue_history

    async def execute_screenplay_review(self, document_id: str, revision_id: str, *,
                                        task_id: str, runtime: Any, cancel_check=None,
                                        attempt_id: str | None = None):
        review_record = await self.store.claim_screenplay_review(
            document_id, revision_id, task_id, attempt_id=attempt_id)
        if review_record["status"] in {"passed", "needs_revision", "needs_rebase"}:
            return review_record
        try:
            plan, dependencies = await self._validate_confirmed_dependencies(
                review_record["plan_run_id"], review_record["confirmed_revisions"])
            document = await self.store.get_revision(document_id, revision_id)
            if document.kind != "episode_script":
                raise DocumentValidation("screenplay document required")
            from novelvideo.agent_teams.adapters import method_runtime
            from novelvideo.script_creation.craft_models import CraftReview
            from .craft_store import text_hash

            reviewer = method_runtime("reviewer", "episode_script", runtime)
            markdown = document.revision.markdown
            previous_markdown, issue_history = await self._screenplay_review_history(
                document_id, revision_id, plan_run_id=review_record["plan_run_id"],
                confirmed_revisions=review_record["confirmed_revisions"],
            )
            confirmed_documents = []
            for dependency_id, dependency_revision in sorted(dependencies.items()):
                frozen = await self.store.get_revision(dependency_id, dependency_revision)
                confirmed_documents.append({
                    "document_id": dependency_id,
                    "revision_id": dependency_revision,
                    "kind": frozen.kind,
                    "title": frozen.title,
                    "episode_number": frozen.episode_number,
                    "text": frozen.revision.markdown,
                })
            reference = {
                "brief_id": plan["brief_id"],
                "brief_revision_id": plan["brief_revision_id"],
                "source_idea": plan.get("source_idea"),
                "confirmed_revisions": dependencies,
                "confirmed_planning_documents": confirmed_documents,
            }
            episode_number = document.episode_number
            if plan.get("script_mode") == "series" and episode_number == 1:
                scope = (
                    "当前范围：系列剧第 1 集。第 1 集只按已确认的本集计划审查，按已确认的第 1 集梗概检查本集目标、冲突和行动；"
                    "全剧 final_choice 不要求在第 1 集完成。不得把全剧最终选择未在第 1 集完成视为遗漏，"
                    "也不得把后续集计划中的事件尚未发生视为当前集缺项。"
                )
            elif plan.get("script_mode") == "series" and episode_number is not None:
                scope = (
                    f"当前范围：系列剧第 {episode_number} 集。按已确认的本集梗概审查当前集目标、冲突和行动；"
                    "不要把其他集的计划或全剧结局误判为本集必须完成的事件。"
                )
            else:
                scope = "当前范围：审查用户提交的完整剧本修订及其整体故事目标。"
            prompt = (
                "对用户明确提交的剧本修订进行独立审查。逐场检查因果、人物动机、场景连续性、拍摄可执行性与对白。"
                "必须按 CraftReview 合同给出有原文证据的覆盖范围、优势与问题；不能代写、不能改动正文，"
                "不得把建议项当作阻断项，也不能隐去阻断问题。逐项核对甲、乙等人物的身份和动作主体，"
                "不得把人物对应关系互换；每个问题的当前正文引文必须直接支持其理由，证据不足时不得断言为阻断问题。"
                "对每个历史问题，当前正文已解决时必须把旧 ID 列入 resolved_issue_ids；仍存在时必须引用当前正文并用 prior_issue_id 续报。"
                "当前正文已消除旧稿中的身份或指代歧义时，不得依据旧稿重复报告；不得复活上次审稿已解决的问题。\n审查范围：" + scope
                + "\n固定规划依据（以下包含用户确认的精确修订正文）：\n"
                + json.dumps(reference, ensure_ascii=False)
                + "\n上次有效完成审稿时的正文（只用于比较修订变化，不作为当前事实依据）：\n"
                + (previous_markdown if previous_markdown is not None else "无")
                + "\n上次审稿仍存在的问题（只含该次审稿未解决的问题及其必要 ID 祖先）：\n"
                + json.dumps(issue_history, ensure_ascii=False)
                + "\n待审正文：\n" + markdown
            )
            if cancel_check:
                await cancel_check()
            result = await reviewer.run_structured(
                prompt=prompt,
                system_prompt=("你是独立剧本审稿人。只审阅提供的确切正文，所有 findings 与 strengths 必须引用原文。"
                               "不输出 passed 字段；结论由程序根据 blocker 计算。"),
                output_type=CraftReview,
                validation_context={"draft": markdown, "reference": json.dumps(reference, ensure_ascii=False),
                                    "issue_history": issue_history},
            )
            result = CraftReview.model_validate(
                result.model_dump(mode="json") if hasattr(result, "model_dump") else result,
                context={"draft": markdown, "reference": json.dumps(reference, ensure_ascii=False),
                         "issue_history": issue_history},
            )
            if cancel_check:
                await cancel_check()
            saved = await self.store.complete_screenplay_review(
                document_id, revision_id, task_id,
                result={**result.model_dump(mode="json"), "status": result.status,
                        "candidate_hash": text_hash(markdown),
                        "method_hash": _digest({"reviewer": str(getattr(reviewer, "snapshot", None)),
                                                "prompt": prompt})},
                status=result.status, attempt_id=review_record["attempt_id"],
            )
            return saved
        except Exception as exc:
            await self.store.complete_screenplay_review(
                document_id, revision_id, task_id,
                result={"error": str(exc)}, status="failed", error=str(exc),
                attempt_id=review_record["attempt_id"])
            raise

    async def state(self) -> dict[str, Any]:
        runs = await GenerationService(self.store).list()
        documents = await self.store.list()
        return {
            "ideations": await self.list_ideations(),
            "plans": [run for run in runs if run.get("mode") in {"planning", "screenplay"}],
            "adopted_screenplays": [document for document in documents
                                    if document.kind == "episode_script" and document.adopted_revision_id],
        }

    async def adopt_screenplay(self, document_id: str, *, revision_id: str,
                               client_mutation_id: str) -> dict[str, Any]:
        document = await self.store.get(document_id)
        if document.kind != "episode_script":
            raise DocumentValidation("screenplay document required")
        revision = await self.store.get_revision(document_id, revision_id)
        proof = await self._generated_screenplay_review(document_id, revision_id, revision.revision.markdown)
        review_record = None
        if proof is None:
            review_record = await self.store.get_screenplay_review(document_id, revision_id)
            if not review_record or review_record.get("status") != "passed":
                raise DocumentValidation("screenplay revision must pass a real review before adoption")
            from .craft_store import text_hash
            if review_record.get("result", {}).get("candidate_hash") != text_hash(revision.revision.markdown):
                raise DocumentValidation("screenplay review does not match this exact revision")
            plan_run_id = review_record["plan_run_id"]
            dependencies = review_record["confirmed_revisions"]
            run_id = None
        else:
            plan_run_id, dependencies, run_id = proof
        plan, dependencies = await self._validate_confirmed_dependencies(
            plan_run_id, dependencies, screenplay_run_id=run_id)
        source_candidate_id = (plan.get("source_idea") or {}).get("candidate_id")
        live_dependencies = {key: value for key, value in dependencies.items()
                             if key not in {document_id, source_candidate_id}}
        adopted = await self.store.adopt_revision(
            document_id, revision_id=revision_id, client_mutation_id=client_mutation_id,
            require_current=True, dependency_revisions=live_dependencies)
        return {"document": adopted, "adopted_revision_id": revision_id}

    async def _validate_confirmed_dependencies(self, plan_run_id: str, dependencies: dict[str, str], *,
                                               screenplay_run_id: str | None = None):
        generation = GenerationService(self.store)
        plan = await generation.get(plan_run_id)
        if (plan.get("mode") != "planning" or plan.get("status") != "completed"
                or plan.get("phase") != "confirmed"):
            raise DocumentValidation("a confirmed planning run is required for screenplay adoption")
        if plan.get("confirmed_revisions") != dependencies:
            raise DocumentConflict("screenplay confirmation no longer matches the current plan decision")
        if screenplay_run_id is not None and screenplay_run_id not in set((plan.get("confirmations") or {}).values()):
            raise DocumentValidation("screenplay run is not part of this plan confirmation")
        source_candidate_id = (plan.get("source_idea") or {}).get("candidate_id")
        for document_id, revision_id in dependencies.items():
            if document_id == source_candidate_id:
                await self.store.get_revision(document_id, revision_id)
                continue
            current = await self.store.get(document_id)
            if current.current_revision_id != revision_id:
                raise DocumentConflict("confirmed planning dependency changed",
                                       current_revision_id=current.current_revision_id)
        return plan, dependencies

    async def _generated_screenplay_review(self, document_id: str, revision_id: str, markdown: str):
        from .craft_store import text_hash

        for run in await GenerationService(self.store).list():
            if run.get("mode") != "screenplay" or run.get("status") != "completed":
                continue
            for step in run.get("steps", []):
                review = step.get("craft_review") or {}
                output = step.get("output") or {}
                if review.get("status") != "passed" or review.get("blocker_count", 0) != 0:
                    continue
                if review.get("candidate_hash") != text_hash(markdown):
                    continue
                if output.get("kind") == "document":
                    if output.get("document_id") == document_id and output.get("revision_id") == revision_id:
                        return run["plan_run_id"], run["confirmed_revisions"], run["id"]
                elif output.get("kind") == "candidate":
                    candidate_id = output.get("candidate_id")
                    candidate = await self.store.generation_candidate(candidate_id)
                    adoption = await self.store.proposal_adoption_for_candidate(candidate_id)
                    if (candidate.get("target_document_id") == document_id and adoption
                            and adoption["document_id"] == document_id
                            and await self.store.revision_descends_from(
                                document_id, adoption["revision_id"], revision_id)):
                        return run["plan_run_id"], run["confirmed_revisions"], run["id"]
        return None
