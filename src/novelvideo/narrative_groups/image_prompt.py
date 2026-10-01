"""Project a director shot into one still, without its future action sequence."""

import json
from typing import Any, Mapping


def panel_description(beat: Mapping[str, Any]) -> str:
    override = str(beat.get("image_prompt_override") or "").strip()
    if override:
        return override
    start = str(beat.get("visible_start_state") or "").strip()
    if not start:
        # Historical beats do not carry a state contract.
        return str(next((beat[key] for key in (
            "visual_description", "shot_description", "action", "content",
            "description", "title",
        ) if beat.get(key)), "continue the scene"))

    lines = [
        "Freeze the opening instant only. Do not depict later actions or their results.",
        f"Opening state (authoritative): {start}",
    ]
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
            "axis", "camera_side", "lights", "key_light_id", "shadow_direction",
        ) if direction.get(key)}
        facts["subjects"] = [
            {key: subject[key] for key in (
                "subject_id", "world_position", "screen_position",
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
