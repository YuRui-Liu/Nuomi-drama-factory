"""Project a director shot into one still, without its future action sequence."""

import json
from typing import Any, Mapping


SPATIAL_KEYFRAME_RULE = (
    "Use the selected instant's body facing and gaze target independently. Keep world landmarks, "
    "action axis, distances and relationships consistent; project them through this shot's camera. "
    "An intentional reverse angle may change screen left/right without moving subjects in the world. "
    "Do not copy another panel's screen positions or depict a later turn in this still."
)


def adjacent_keyframe_context(beats, shot_ids) -> str:
    """Frozen neighbor states survive physical packing; never import future motion."""
    selected = set(shot_ids)
    rows = []
    for index, beat in enumerate(beats):
        if str(beat.get('id') or beat.get('beat_id') or '') not in selected:
            continue
        for neighbor_index, anchor in ((index - 1, 'end'), (index + 1, 'start')):
            if not 0 <= neighbor_index < len(beats):
                continue
            neighbor = beats[neighbor_index]
            identifier = str(neighbor.get('id') or neighbor.get('beat_id') or '')
            if identifier in selected:
                continue
            scene = beat.get('scene_id') or beat.get('scene_name')
            neighbor_scene = neighbor.get('scene_id') or neighbor.get('scene_name')
            if scene and neighbor_scene and scene != neighbor_scene:
                continue
            # End geography must never be inferred from opening blocking.
            direction = ((neighbor.get('creative') or {}).get('end_cinematography')
                         if anchor == 'end' else neighbor.get('cinematography'))
            endpoint = ({key: direction[key] for key in ('axis', 'camera_side') if direction.get(key)}
                        if isinstance(direction, Mapping) else {})
            if isinstance(direction, Mapping):
                endpoint['subjects'] = [{key: subject[key] for key in (
                    'subject_id', 'world_position', 'screen_position', 'facing', 'gaze_target')
                    if subject.get(key)} for subject in direction.get('subjects', ())
                    if isinstance(subject, Mapping)]
            rows.append({'shot_id': identifier, 'anchor': anchor,
                         'state': neighbor.get(f'visible_{anchor}_state', ''),
                         'scene': neighbor_scene or '', 'endpoint': endpoint})
    if not rows:
        return ''
    return ('Adjacent source states are continuity context, not additional panels. '
            + SPATIAL_KEYFRAME_RULE + '\n' + json.dumps(rows, ensure_ascii=False))


def panel_description(beat: Mapping[str, Any]) -> str:
    if beat.get("creative_contract_version") == 1:
        from novelvideo.director_plan.keyframes import project_keyframe

        projection = project_keyframe(beat)
        return "\n".join((
            f"Freeze the explicitly selected {projection.anchor} instant only. Do not depict other instants, motion paths, or cut instructions.",
            "Authoritative director keyframe facts: " + json.dumps(projection.facts, ensure_ascii=False),
            SPATIAL_KEYFRAME_RULE,
            "Preserve the mapped identity and wardrobe references. Reference composition must not override these director decisions. "
            "Do not turn dialogue into writing; render only writing explicitly present in the selected visible state.",
        ))
    override = str(beat.get("image_prompt_override") or "").strip()
    start = str(beat.get("visible_start_state") or "").strip()
    if not start:
        # Historical beats do not carry a state contract.
        return override or str(next((beat[key] for key in (
            "visual_description", "shot_description", "action", "content",
            "description", "title",
        ) if beat.get(key)), "continue the scene"))

    lines = [
        "Freeze the opening instant only. Do not depict later actions or their results.",
        f"Opening state (authoritative): {start}",
        SPATIAL_KEYFRAME_RULE,
    ]
    if override:
        lines.append('Requested image treatment, retaining the explicit keyframe spatial facts: ' + override)
    for key in ("scene_name", "time_of_day", "shot_size", "camera_angle", "composition"):
        if beat.get(key):
            lines.append(f"{key}: {beat[key]}")
    if beat.get("detected_identities"):
        lines.append("Character identity context (visibility follows the opening state): "
                     + json.dumps(beat["detected_identities"], ensure_ascii=False))
    direction = beat.get("cinematography")
    if isinstance(direction, Mapping):
        # Motion paths, gaze transitions and cut intentions belong to video.
        facts = {key: direction[key] for key in (
            "axis", "camera_side", "lights", "key_light_id", "shadow_direction", "exposure_priority",
        ) if direction.get(key)}
        facts["subjects"] = [
            {key: subject[key] for key in (
                "subject_id", "world_position", "screen_position", "facing", "gaze_target",
            ) if subject.get(key)}
            for subject in direction.get("subjects", ())
            if isinstance(subject, Mapping)
        ]
        lines.append("Spatial and lighting context, only where consistent with the opening state: "
                     + json.dumps(facts, ensure_ascii=False))
    lines.append(
        "Preserve each mapped identity reference's face and wardrobe, and the established "
        "prop size and appearance. Do not turn dialogue into visible writing. "
        "Render only text explicitly present in the opening state; keep blank areas blank."
    )
    return "\n".join(lines)
