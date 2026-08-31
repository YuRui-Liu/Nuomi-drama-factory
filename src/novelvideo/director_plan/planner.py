from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
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
    groups: tuple[NarrativeGroupPlan, ...]


class GroupRepairInput(_FrozenModel):
    episode: DirectorPlanInput
    failed_group: NarrativeGroupPlan
    previous_group: NarrativeGroupPlan | None = None
    next_group: NarrativeGroupPlan | None = None
    relevant_source_spans: tuple[SourceSpan, ...]
    issues: tuple[dict[str, Any], ...]


class DirectorPlanner:
    def __init__(self, agent: Any | None = None) -> None:
        from novelvideo.text_task_runtime.runtime import current_text_task_runtime

        self._runtime = current_text_task_runtime() if agent is None else None
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

    async def plan_episode(self, input: DirectorPlanInput) -> DirectorPlanDraft:
        if self._runtime is not None:
            output = await self._runtime.run_structured(
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
            output = await self._runtime.run_structured(
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
