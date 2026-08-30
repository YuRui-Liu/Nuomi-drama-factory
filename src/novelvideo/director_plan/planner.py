from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, PromptedOutput

from novelvideo.config import (
    get_newapi_text_pydantic_model,
    get_newapi_text_pydantic_model_settings,
)

from .models import NarrativeGroupPlan, SourceSpan
from .prompts import build_episode_prompt, build_group_repair_prompt


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DirectorPlanInput(_FrozenModel):
    episode: int
    source_script_hash: str
    source_spans: tuple[SourceSpan, ...]
    relevant_bible: dict[str, Any]
    aspect_ratio: str
    style_director: dict[str, Any]
    project_style_snapshot_id: str
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
        self._agent = agent or Agent(
            get_newapi_text_pydantic_model("DIRECTOR_PLAN_MODEL", "deepseek-v4-flash"),
            output_type=PromptedOutput(DirectorPlanDraft),
            retries={"tools": 0, "output": 2},
            model_settings=get_newapi_text_pydantic_model_settings(
                "DIRECTOR_PLAN_THINKING_LEVEL", "low"
            ),
        )

    async def plan_episode(self, input: DirectorPlanInput) -> DirectorPlanDraft:
        response = await self._agent.run(build_episode_prompt(input))
        return DirectorPlanDraft.model_validate(response.output)

    async def repair_group(self, input: GroupRepairInput) -> NarrativeGroupPlan:
        response = await self._agent.run(build_group_repair_prompt(input))
        draft = DirectorPlanDraft.model_validate(response.output)
        if len(draft.groups) != 1:
            raise ValueError("group repair must return exactly one group")
        group = draft.groups[0]
        if group.id != input.failed_group.id:
            raise ValueError("group repair cannot replace a different group id")
        return group
