from __future__ import annotations

from typing import Any
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
from novelvideo.screenplay_semantics.models import DramaticBeat, Scene
from .prompts import build_episode_prompt, build_group_repair_prompt


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DirectorPlanContractError(ValueError):
    """The provider responded, but its structured director-plan output was invalid."""


class DirectorPlanInput(_FrozenModel):
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
    prompt_version: str = "director-plan-v2"


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

    async def plan_episode(self, input: DirectorPlanInput) -> DirectorPlanDraft:
        if self._runtime is not None:
            output = await self._author_for(input).run_structured(
                prompt=build_episode_prompt(input), output_type=DirectorPlanDraft
            )
        else:
            response = await self._agent.run(build_episode_prompt(input))
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
