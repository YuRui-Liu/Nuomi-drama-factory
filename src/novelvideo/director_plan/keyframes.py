"""Explicit static projection of adopted creative decisions.

The dependency digest excludes the revision id, which is carried separately:
an unrelated later action edit must not invalidate an unchanged opening frame.
"""
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal

from .creative_contract import ShotCreativeDecisions
from .cinematography import ShotCinematography


@dataclass(frozen=True)
class KeyframeProjection:
    shot_id: str
    director_revision_id: str | None
    anchor: Literal["start", "end"]
    facts: dict[str, Any]
    dependency_hash: str
    dependency_fields: tuple[str, ...]


def project_keyframe(beat: Mapping[str, Any]) -> KeyframeProjection:
    if beat.get("creative_contract_version") != 1:
        raise ValueError("creative_contract_version_required")
    if beat.get("image_prompt_override"):
        raise ValueError("creative_override_requires_revision")
    creative = ShotCreativeDecisions.model_validate(beat.get("creative"))
    anchor = creative.keyframe_anchor
    direction = (creative.end_cinematography if anchor == "end" else
                 ShotCinematography.model_validate(beat.get("cinematography")))
    assert direction is not None  # Enforced by the creative end-snapshot validator.
    state_key = f"visible_{anchor}_state"
    state = str(beat.get(state_key) or "").strip()
    if not state:
        raise ValueError(f"{anchor} keyframe requires explicit visible state")
    optics = creative.optics.model_dump(mode="json", exclude_none=True,
        exclude={"purpose", "source", "focus_change", "end_focus_target"})
    if anchor == "end":
        optics["focus_target"] = creative.optics.end_focus_target
    composition = creative.composition
    facts: dict[str, Any] = {
        "state": state,
        "optics": optics,
        "framing": {"frame": getattr(creative.framing, f"{anchor}_frame"),
                    "must_read": list(creative.framing.must_read)},
        "composition": {"layout": getattr(composition, f"{anchor}_composition"),
                        **composition.model_dump(mode="json", exclude={
                            "purpose", "source", "start_composition", "end_composition"})},
        "axis": direction.axis,
        "camera_side": direction.camera_side,
        "subjects": [subject.model_dump(mode="json", exclude={"motion_path"})
                     for subject in direction.subjects],
        "lights": [light.model_dump(mode="json") for light in direction.lights],
        "key_light_id": direction.key_light_id,
        "shadow_direction": direction.shadow_direction,
        "exposure_priority": direction.exposure_priority,
        "lighting": creative.lighting.model_dump(mode="json", exclude={"purpose", "source"}),
        "performance": [{"subject_id": character.subject_id,
                         "behavior": getattr(character, f"{anchor}_behavior")}
                        for character in creative.performance.characters],
    }
    # A moving shot's initial physical camera position is not its end position.
    # End blocking explicitly owns the selected viewpoint.
    if anchor == "start":
        facts["camera_position"] = creative.camera_position.model_dump(
            mode="json", exclude={"purpose", "source"})
    for field in ("scene_name", "time_of_day", "detected_identities", "style_snapshot_id", "style_snapshot_hash"):
        if beat.get(field) is not None:
            facts[field] = beat[field]
    encoded = json.dumps({"projection_version": 1, "anchor": anchor, "facts": facts},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    fields = [state_key, f"creative.framing.{anchor}_frame",
              "creative.framing.must_read", f"creative.composition.{anchor}_composition",
              "creative.composition.attention_center", "creative.composition.subject_placement",
              "creative.composition.frame_occupancy", "creative.composition.depth_layers",
              "creative.composition.occlusion", "creative.composition.look_room",
              f"creative.performance.characters.{anchor}_behavior",
              "creative.performance.characters.subject_id"]
    fields.extend(f"creative.optics.{('end_focus_target' if anchor == 'end' and key == 'focus_target' else key)}"
                  for key in optics)
    fields.extend(f"creative.lighting.{key}" for key in facts["lighting"])
    direction_path = "creative.end_cinematography" if anchor == "end" else "cinematography"
    fields.extend(f"{direction_path}.{key}" for key in (
        "axis", "camera_side", "subjects.subject_id", "subjects.world_position", "subjects.screen_position",
        "subjects.facing", "subjects.gaze_target", "lights", "key_light_id", "shadow_direction", "exposure_priority"))
    if anchor == "start":
        fields.extend(f"creative.camera_position.{key}" for key in facts["camera_position"])
    fields.extend(key for key in ("scene_name", "time_of_day", "detected_identities", "style_snapshot_id", "style_snapshot_hash") if key in facts)
    return KeyframeProjection(str(beat.get("id") or beat.get("director_shot_id") or ""),
                              beat.get("director_revision_id"), anchor, facts,
                              hashlib.sha256(encoded.encode("utf-8")).hexdigest(), tuple(fields))
