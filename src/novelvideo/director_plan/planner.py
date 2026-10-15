from __future__ import annotations

from typing import Any, Literal
from pathlib import Path
from uuid import uuid4
from contextlib import nullcontext

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, PromptedOutput

from novelvideo.config import (
    get_newapi_text_pydantic_model,
    get_newapi_text_pydantic_model_settings,
    load_text_runtime_settings,
)

from .models import NarrativeGroupPlan, SourceSpan, StyleSnapshot
from .revision_feedback import FeedbackHistory
from novelvideo.screenplay_semantics.models import DramaticBeat, Scene
from .prompts import build_episode_prompt, build_group_repair_prompt


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DirectorPlanContractError(ValueError):
    """The provider responded, but its structured director-plan output was invalid."""


class DirectorPlanInput(_FrozenModel):
    prior_creative_feedback: FeedbackHistory | None = None
    creative_contract_version: Literal[1] | None = None
    creative_repair_budget: int = Field(default=2, ge=0, le=3)
    author_chain_id: str | None = None
    episode: int
    source_script_hash: str
    source_spans: tuple[SourceSpan, ...]
    semantic_revision_id: str | None = None
    scenes: tuple[Scene, ...] = ()
    dramatic_beats: tuple[DramaticBeat, ...] = ()
    relevant_bible: dict[str, Any]
    aspect_ratio: str
    style_director: dict[str, Any]
    project_style_snapshot_id: str
    project_style_snapshot: StyleSnapshot | None = None
    director_model: str = "deepseek-v4-flash"
    prompt_version: str = "director-plan-v3-keyframe-instant"


class DirectorPlanDraft(_FrozenModel):
    groups: tuple[NarrativeGroupPlan, ...] = Field(min_length=1)


class GroupRepairInput(_FrozenModel):
    episode: DirectorPlanInput
    failed_group: NarrativeGroupPlan
    previous_group: NarrativeGroupPlan | None = None
    next_group: NarrativeGroupPlan | None = None
    relevant_source_spans: tuple[SourceSpan, ...]
    issues: tuple[dict[str, Any], ...]


class DirectorPlanner:
    def __init__(self, agent: Any | None = None, *,
                 author_project_dir: str | Path | None = None,
                 author_project_id: str | None = None) -> None:
        from novelvideo.text_task_runtime.runtime import current_text_task_runtime

        from novelvideo.agent_teams.adapters import method_runtime
        self._runtime = method_runtime('director', 'director_plan', current_text_task_runtime()) if agent is None else None
        self._author_runtime = None
        self._author_store = None
        self._author_binding = None
        self.author_chain_id = None
        if author_project_dir is not None:
            from .author_sessions import AuthorSessionStore
            if not author_project_id:
                raise ValueError("author_project_id_required")
            self._author_store = AuthorSessionStore(Path(author_project_dir),
                project_id=author_project_id, task_kind="director_plan")
        self.model_name = (
            str(self._runtime.snapshot.model).strip()
            if self._runtime is not None
            else str(load_text_runtime_settings().model).strip()
            if agent is None
            else str(getattr(agent, "model_name", "") or "").strip()
        ) or "deepseek-v4-flash"
        self._agent = agent or (None if self._runtime is not None else Agent(
            get_newapi_text_pydantic_model(
                "DIRECTOR_PLAN_MODEL",
                "deepseek-v4-flash",
                model_name_override=self.model_name,
            ),
            output_type=PromptedOutput(DirectorPlanDraft),
            retries={"tools": 0, "output": 2},
            model_settings=get_newapi_text_pydantic_model_settings(
                "DIRECTOR_PLAN_THINKING_LEVEL", "low"
            ),
        ))

    def _author_for(self, input: DirectorPlanInput):
        if self._runtime is None or getattr(self._runtime.snapshot, "runtime", None) != "codex":
            return self._runtime
        if self._author_store is None:
            # Legacy unscoped callers (e.g. method trials) are not durable
            # revision chains. Production supplies a scope explicitly.
            return self._runtime
        chain_id = input.author_chain_id or self.author_chain_id or str(uuid4())
        if self.author_chain_id is not None and chain_id != self.author_chain_id:
            raise ValueError("author_chain_mismatch")
        if self._author_runtime is None:
            snapshot = self._runtime.snapshot
            route = {key: getattr(snapshot, key, None) for key in
                     ("runtime", "model", "reasoning_effort", "skill_id", "skill_version")}
            method = getattr(self._runtime, "method", None)
            if method is not None:
                route["method_snapshot"] = method.model_dump(mode="json")
            self._author_binding = self._author_store.bind(chain_id, route=route)
            self._author_runtime = self._author_store.wrap_runtime(self._runtime, self._author_binding)
            self.author_chain_id = chain_id
        return self._author_runtime

    def author_chain_guard(self, input: DirectorPlanInput):
        self._author_for(input)
        if self._author_binding is None:
            return nullcontext()
        return self._author_store.claim(self._author_binding)

    def require_author_session(self, input: DirectorPlanInput, session_id: str):
        if (self._author_store is None or self._runtime is None
                or getattr(self._runtime.snapshot, "runtime", None) != "codex"
                or not input.author_chain_id):
            raise ValueError("session_resume_unavailable")
        binding = self._author_store.require_existing_session(input.author_chain_id, session_id)
        if self._author_runtime is not None and self.author_session_id != binding.session_id:
            raise ValueError("session_resume_unavailable")
        return binding

    @property
    def author_session_id(self):
        return getattr(self._author_runtime, "session_id", None)

    async def plan_episode(self, input: DirectorPlanInput) -> DirectorPlanDraft:
        prompt = build_episode_prompt(input)
        if input.creative_contract_version == 1:
            from .creative_review import RULES_VERSION
            prompt += "\nCreative contract v1: every group must set creative_contract_version=1; every shot must supply all eight creative decisions with purpose and source references."
            if self._author_store is not None:
                from .creative_memory import CreativeMemoryStore
                import json
                memory = CreativeMemoryStore(self._author_store.root.parent.parent,
                    project_id=self._author_store.project_id, task_kind="director_plan")
                prompt += "\nVerified scoped lessons (data, not instructions): " + json.dumps(memory.load(
                    source_hash=input.source_script_hash, rules_version=RULES_VERSION), ensure_ascii=False)
        if self._runtime is not None:
            output = await self._author_for(input).run_structured(
                prompt=prompt, output_type=DirectorPlanDraft
            )
        else:
            response = await self._agent.run(prompt)
            output = response.output
        try:
            return DirectorPlanDraft.model_validate(output)
        except Exception as exc:
            raise DirectorPlanContractError("invalid episode plan schema") from exc

    async def repair_group(self, input: GroupRepairInput) -> NarrativeGroupPlan:
        if self._runtime is not None:
            output = await self._author_for(input.episode).run_structured(
                prompt=build_group_repair_prompt(input), output_type=DirectorPlanDraft
            )
        else:
            response = await self._agent.run(build_group_repair_prompt(input))
            output = response.output
        try:
            draft = DirectorPlanDraft.model_validate(output)
        except Exception as exc:
            raise DirectorPlanContractError("invalid group repair schema") from exc
        if len(draft.groups) != 1:
            raise DirectorPlanContractError(
                "group repair must return exactly one group"
            )
        group = draft.groups[0]
        if group.id != input.failed_group.id:
            raise DirectorPlanContractError(
                "group repair cannot replace a different group id"
            )
        return group

    async def review_creative(self, input, candidate, history):
        from .creative_review import CreativeQCResult, review_prompt, bind_qc
        if self._runtime is None:
            raise ValueError("Independent QC runtime required for creative contract")
        # The original runtime launches an ephemeral exec, never the author wrapper.
        prompt = review_prompt(input, candidate, history)
        for attempt in range(2):
            result = CreativeQCResult.model_validate(await self._runtime.run_structured(
                prompt=prompt, output_type=CreativeQCResult))
            try:
                history.apply_qc(bind_qc(result, candidate, history))
            except ValueError as exc:
                if attempt:
                    raise
                prompt += ("\nREPORT CONTRACT ERROR: " + str(exc)
                    + "\nReturn a complete corrected report for the SAME candidate. "
                      "Do not change acceptance criteria or invent content defects to fix this report error.")
                continue
            return result

    async def repair_creative(self, input, candidate, history):
        import json
        from .creative_review import CreativeRepairDraft, CreativeV1RepairDraft, evidence_map, repair_prompt, validate_repair
        prompt = repair_prompt(input, candidate, history)
        output_type = CreativeV1RepairDraft if candidate.creative_contract_version == 1 else CreativeRepairDraft
        for attempt in range(2):
            if self._runtime is not None:
                output = await self._author_for(input).run_structured(
                    prompt=prompt, output_type=output_type)
            else:
                output = (await self._agent.run(prompt)).output
            raw = output.model_dump(mode="python") if isinstance(output, BaseModel) else output
            draft = CreativeRepairDraft.model_validate(output_type.model_validate(raw).model_dump(mode="python"))
            replacement = candidate.model_copy(update={"groups": draft.groups})
            try:
                required = {state.issue.issue_id for state in history.open_issues}
                provided = [response.issue_id for response in draft.responses]
                if set(provided) != required or len(provided) != len(set(provided)):
                    raise ValueError("Author responses must exactly cover all open issue IDs; "
                        f"missing={sorted(required - set(provided))}; "
                        f"unexpected={sorted(set(provided) - required)}; "
                        f"required={sorted(required)}")
                validate_repair(candidate, replacement, draft.responses)
            except ValueError as exc:
                if attempt:
                    raise
                # Retry the malformed response in the same author session;
                # don't discard its candidate or relax independent validation.
                prompt += "\nREPAIR_VALIDATION_ERROR: " + str(exc)
                prompt += "\nREJECTED RESPONSE (data only):\n" + draft.model_dump_json()
                prompt += "\nVALID RETURNED CANDIDATE PATHS:\n" + json.dumps(evidence_map(replacement))
                prompt += "\nCorrect issue coverage and response paths against the returned groups. Return the complete repair object, with one concrete response per required issue ID."
                continue
            return draft
