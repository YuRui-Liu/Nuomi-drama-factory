"""Retire legacy portrait-only storyboards when their wardrobe sheet is ready."""
from dataclasses import replace
from pathlib import Path

from .service import _sidecar_guard, load_materialized_groups, save_groups


def reconcile_wardrobe_dependencies(project_dir, episode, workflow):
    # Re-read inside the same guard used by generation completion. Never write a
    # caller's stale group snapshot over a just-completed render.
    with _sidecar_guard(project_dir, episode):
        groups = load_materialized_groups(project_dir, episode)
        updated = []
        changed = False
        for group in groups:
            stages = dict(group.stages)
            for stage in ('sketch', 'render'):
                state = stages[stage]
                if state.status != 'completed' or state.needs_regeneration:
                    continue
                refs = state.provider_parameters.get('reference_audit', {}).get('asset_references', [])
                if not any(_has_wardrobe_upgrade(ref, workflow, Path(project_dir)) for ref in refs):
                    continue
                stages[stage] = replace(state, needs_regeneration=True,
                    stale_reason='portrait_reference_superseded_by_identity_sheet')
                changed = True
            # A video already running when the catalogue changes may finish
            # later. Reconcile it on the next read without touching its task.
            if (stages['render'].stale_reason == 'portrait_reference_superseded_by_identity_sheet'
                and stages['render'].needs_regeneration
                and stages['video'].status not in {'queued', 'running'}
                and not stages['video'].needs_regeneration):
                stages['video'] = replace(stages['video'], needs_regeneration=True,
                    stale_reason='render_wardrobe_reference_changed')
                changed = True
            updated.append(replace(group, stages=stages))
        if changed:
            save_groups(project_dir, episode, updated)
        return updated


def _has_wardrobe_upgrade(ref, workflow, root):
    parts = str(ref.get('asset_slot_id') or '').split(':')
    if len(parts) != 3 or parts[0] != 'character' or parts[2] != 'portrait':
        return False
    identity = str(ref.get('identity_entity_id') or ref.get('entity_key') or '')
    if not identity or identity == parts[1]:
        return False
    slot_id = f'character:{parts[1]}:state:{identity}'
    try:
        slot, versions = workflow.get_slot(slot_id)
    except KeyError:
        return False
    version = versions.get(slot.current_version_id)
    if (version is None or version.slot_id != slot_id
        or version.adoption_status.value not in {'adopted', 'provisional'}):
        return False
    path = (root / version.asset_path).resolve()
    return path.is_relative_to(root.resolve()) and path.is_file()
