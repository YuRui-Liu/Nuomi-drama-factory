"""Reproject active-plan references from existing assets without model work."""
from pathlib import Path
from novelvideo.production_workflow import production_workflow_project_lock, ProductionWorkflowStore
from novelvideo.production_workflow.slot_ids import character_portrait_slot_id, character_state_slot_id
from .planned_binding_service import bindings_for_director_plan


async def refresh_active_references(ctx, store, plan_store, episode, expected_revision):
    from novelvideo.task_backend.runners.episode_assets import _scene_reference_catalog_snapshot

    # Activation and asset publication use these same guards. Do not relabel
    # old rows: derive fresh requirements/IDs/status against the active revision.
    with plan_store.lock_active_revision(episode) as plan:
        if plan is None or plan.revision_id != expected_revision:
            raise ValueError('ACTIVE_DIRECTOR_PLAN_STALE')
        with production_workflow_project_lock(ctx.state_dir):
            reload_catalog = getattr(store, 'load_graph_state', None)
            if callable(reload_catalog):
                await reload_catalog()
            episode_data = await store.get_episode_from_graph(episode)
            if episode_data is None:
                raise ValueError('EPISODE_NOT_FOUND')
            characters = tuple(store.get_all_characters())
            scenes = tuple(await store.list_scenes())
            props = tuple(await store.list_props())
            slots, _ = _scene_reference_catalog_snapshot(ctx=ctx, scenes=scenes)
            portraits, identities = _current_character_media(ctx, characters)
            bindings = bindings_for_director_plan(
                project_id=str(ctx.project_id), episode_number=episode,
                source_plan_revision_id=plan.revision_id, groups=plan.groups,
                shots=tuple(shot for group in plan.groups for shot in group.shots),
                characters=characters, scenes=scenes, props=props,
                episode_identity_ids=episode_data.identity_ids,
                identity_default_map=episode_data.identity_default_map,
                available_character_portraits=portraits,
                available_character_identity_ids=identities,
                available_scene_reference_slots=slots,
            )
            await store.replace_planned_reference_bindings_atomic(episode,
                ('character_identity', 'scene_base', 'scene_variant', 'prop'), bindings)
    return len(bindings)


def _current_character_media(ctx, characters):
    """Read adopted/provisional versions only; never import legacy media here."""
    from novelvideo.narrative_groups.reference_uploads import validate_reference_image
    root = Path(ctx.output_dir).resolve()
    workflow = ProductionWorkflowStore(Path(ctx.state_dir) / 'production_workflow.json')
    portraits, identities = set(), set()
    for character in characters:
        candidates = [(character_portrait_slot_id(character.name), character.name, portraits, 'character_portrait')]
        candidates.extend((character_state_slot_id(character.name, i.identity_id), i.identity_id, identities, 'character_state')
            for i in character.identities)
        for slot_id, key, target, kind in candidates:
            try:
                slot, versions = workflow.get_slot(slot_id)
            except KeyError:
                continue
            version = versions.get(slot.current_version_id)
            if (slot.asset_kind != kind or version is None or version.slot_id != slot_id
                or version.adoption_status.value not in {'adopted', 'provisional'}):
                continue
            try:
                path = (root / version.asset_path).resolve()
                path.relative_to(root)
                validate_reference_image(path, allowed_roots=(root / 'assets' / 'characters' / character.name,))
            except (OSError, ValueError):
                continue
            target.add(key)
    return frozenset(portraits), frozenset(identities)
