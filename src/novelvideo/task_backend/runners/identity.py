"""Celery runner for episode identity planning."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def _build_identity_planner_result(
    *,
    episode: int,
    new_count: int,
    resolved_count: int,
    identities: list[dict[str, str]],
    auto_promoted_characters: list[str],
    binding_count: int | None = None,
    binding_statuses: dict[str, int] | None = None,
) -> dict[str, Any]:
    result = {
        "episode": episode,
        "new_count": new_count,
        "resolved_count": resolved_count,
        "identities": identities,
        "auto_promoted_characters": auto_promoted_characters,
    }
    if binding_count is not None:
        result["binding_count"] = binding_count
        result["binding_statuses"] = dict(binding_statuses or {})
    return result


def _character_identity_bindings(
    *,
    project_id: str,
    episode_number: int,
    director_plan,
    draft,
    characters,
    scenes,
    props,
):
    """Project only identity bindings, overlaying the zero-write draft snapshot."""
    from novelvideo.narrative_groups.planned_binding_service import (
        bindings_by_kind,
        bindings_for_director_plan,
    )

    groups = tuple(director_plan.groups)
    shots = tuple(shot for group in groups for shot in group.shots)
    if not groups or not shots:
        raise ValueError("ACTIVE_DIRECTOR_PLAN_HAS_NO_SCOPE")
    characters_by_name = {character.name: character for character in characters}
    characters_by_name.update(
        {character.name: character for character in draft.characters}
    )
    bindings = bindings_for_director_plan(
        project_id=project_id,
        episode_number=episode_number,
        source_plan_revision_id=director_plan.revision_id,
        groups=groups,
        shots=shots,
        characters=tuple(characters_by_name.values()),
        scenes=scenes,
        props=props,
    )
    return bindings_by_kind(bindings).get("character_identity", ())


def run_identity_planner(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_identity_planner(envelope, ctx),
            envelope,
            task_type="identity_planner",
        )
    )


async def _run_identity_planner(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    from novelvideo.agents.identity_planner import IdentityPlanner
    from novelvideo.cognee import CogneeStore
    from novelvideo.sqlite_store import SQLiteStore

    episode = int(envelope.get("episode") or (envelope.get("payload") or {}).get("episode") or 0)
    manager = get_task_manager()

    def update(
        progress: float | None = None, task: str | None = None, log: str | None = None
    ) -> None:
        manager.update_progress_for_project(
            ctx,
            "identity_planner",
            episode,
            progress=progress,
            current_task=task,
            logs=[log] if log else None,
        )

    update(0.05, "加载项目数据...")
    sqlite_store = SQLiteStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
    )
    await sqlite_store.initialize()
    await sqlite_store.load_graph_state()

    cognee_store = CogneeStore(
        ctx.owner_project_label,
        output_dir=str(ctx.output_dir),
        state_dir=str(ctx.state_dir),
        sqlite_store=sqlite_store,
    )
    await cognee_store.initialize()
    await cognee_store.load_graph_state()

    episode_obj = cognee_store.get_episode(episode)
    if episode_obj is None:
        raise ValueError(f"Episode {episode} not found")

    update(0.10, "分析身份需求...")
    planner = IdentityPlanner(cognee_store)

    def on_log(message: str) -> None:
        update(log=message)

    draft = await planner.build_identity_plan_draft(episode_obj, on_log=on_log)

    from novelvideo.director_plan.store import DirectorPlanStore

    director_plan_store = DirectorPlanStore(ctx.output_dir)
    with director_plan_store.lock_active_revision(episode) as director_plan:
        if director_plan is None:
            raise ValueError("ACTIVE_DIRECTOR_PLAN_REQUIRED")
        bindings = _character_identity_bindings(
            project_id=ctx.project_id,
            episode_number=episode,
            director_plan=director_plan,
            draft=draft,
            characters=tuple(cognee_store.get_all_characters()),
            scenes=tuple(await sqlite_store.list_scenes()),
            props=tuple(await sqlite_store.list_props()),
        )
        await sqlite_store.publish_identity_plan_atomic(
            episode_number=episode,
            characters=draft.characters,
            episode_identity_ids=draft.episode_identity_ids,
            identity_default_map=draft.identity_default_map,
            identity_baseline_digests=draft.identity_baseline_digests,
            episode_identity_baseline_digest=(
                draft.episode_identity_baseline_digest
            ),
            bindings=bindings,
        )
    await cognee_store.load_graph_state()
    refreshed = cognee_store.get_episode(episode) or episode_obj

    identities: list[dict[str, str]] = []
    episode_identity_ids = set(getattr(refreshed, "identity_ids", []) or [])
    for character in cognee_store.get_all_characters():
        for identity in getattr(character, "identities", []) or []:
            identity_id = getattr(identity, "identity_id", "") or ""
            if not identity_id or identity_id not in episode_identity_ids:
                continue
            identities.append(
                {
                    "character_name": character.name,
                    "identity_id": identity_id,
                    "identity_name": getattr(identity, "identity_name", "") or identity_id,
                    "appearance_details": getattr(identity, "appearance_details", "") or "",
                }
            )

    update(
        0.95,
        "身份规划完成",
        f"新增 {draft.new_count} 个身份，复用 {draft.resolved_count} 个身份",
    )
    return _build_identity_planner_result(
        episode=episode,
        new_count=draft.new_count,
        resolved_count=draft.resolved_count,
        identities=identities,
        auto_promoted_characters=list(getattr(planner, "auto_promoted_characters", []) or []),
        binding_count=len(bindings),
        binding_statuses=dict(Counter(binding.status for binding in bindings)),
    )


register_project_task_runner(
    "identity_planner",
    run_identity_planner,
    text_task_role="knowledge_extraction",
)
