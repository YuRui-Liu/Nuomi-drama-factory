"""Lossless, provider-specific projection of adopted creative decisions.

The optimizer copies these exact slots; it does not get to reinterpret them.
The normal H3 compiler then emits them through its existing official wire.
"""
from __future__ import annotations

import json
from collections.abc import Mapping


def _facts(value: object) -> str:
    if isinstance(value, Mapping):
        return "; ".join(f"{key}: {_facts(item)}" for key, item in value.items()
                         if key != "source" and item is not None)
    if isinstance(value, (tuple, list)):
        return " / ".join(_facts(item) for item in value)
    return str(value)


def creative_h3_slots(creative: Mapping) -> dict[str, str]:
    """Canonical shot prose slots, retaining decisions and their purposes."""
    return {
        "framing": _facts(creative.get("framing", {})),
        "angle": _facts(creative.get("camera_position", {})),
        "focus": _facts(creative.get("optics", {})),
        "composition": ". ".join(
            f"{name}: {_facts(creative.get(name, {}))}"
            for name in ("composition", "motion", "performance", "lighting", "handoff")
        ),
    }


def creative_h3_rigid_slots(creative: Mapping) -> dict:
    """Exact overlaps with the existing H3 optics/lighting protocol."""
    optics = creative.get("optics", {})
    lighting = creative.get("lighting", {})
    result = {}
    if optics.get("mode"):
        result["optics"] = {
            "lens_or_fov": _facts({key: optics[key] for key in (
                "mode", "focal_length_mm", "sensor_basis", "field_of_view_degrees",
                "field_of_view_axis", "not_applicable_reason") if optics.get(key) is not None}),
            "camera_height": creative["camera_position"]["height"],
            "subject_distance": optics["subject_distance"],
            "depth_of_field": optics["depth_of_field"],
            "focus_plan": f"{optics['focus_target']}; {optics['focus_change']}",
        }
    if lighting.get("light_ids"):
        result["lighting"] = {
            "quality": lighting["quality"], "color": lighting["color"],
            "shadow_direction": lighting["shadow"], "catchlight": lighting["eye_light"],
            "subject_effect": lighting["subject_background_exposure"],
        }
    return result


def creative_h3_execution_slots(state: Mapping, fps: int = 24) -> dict:
    """Canonical executable action, acting and opening-blocking slots."""
    characters = state["creative"].get("performance", {}).get("characters", ())
    frames = round(float(state["duration_seconds"]) * fps)
    if frames < 2:
        raise ValueError("capability_conflict: H3 cannot represent an adopted shot shorter than two frames")
    events = {0: [f"visible_start_state: {state['visible_start_state']}"],
              1: [f"adopted action: {state.get('action', '')}"]}
    for character in characters:
        for beat in character.get("beats", ()):
            frame = min(frames - 1, round(float(beat["at_seconds"]) * fps))
            events.setdefault(frame, []).append(
                f"{character['subject_id']} at {beat['at_seconds']}s ({beat['event_anchor']}): {beat['behavior']}")
    boundaries = sorted(events)
    events[boundaries[-1]].append(f"visible_end_state at shot end: {state['visible_end_state']}")
    actions = [{"start_frame": frame, "end_frame": boundaries[index + 1] if index + 1 < len(boundaries) else frames,
                "description": "; ".join(events[frame])}
               for index, frame in enumerate(boundaries)]
    acting = [{"character_id": item["subject_id"], "state": item["start_behavior"],
               "want": item["goal"], "hidden": item["obstacle"], "body_rhythm": item["weight_and_inertia"],
               "visible_behavior": _facts({key: item[key] for key in ("visible_behavior", "gaze", "contact", "held_objects")}),
               "change": _facts({key: item[key] for key in ("end_behavior", "tactics", "beats")})}
              for item in characters]
    performances = {item["subject_id"]: item for item in characters}
    subjects = []
    for item in state.get("cinematography", {}).get("subjects", ()):
        held = performances.get(item["subject_id"], {}).get("held_objects", "none")
        subjects.append({"character_id": item["subject_id"],
                         "position": f"world: {item['world_position']}; screen: {item['screen_position']}",
                         "facing": item["facing"], "gaze": item["gaze_target"],
                         "held_props": [] if held.casefold() in {"none", "no objects", "no objects held", "nothing", "无"} else [held]})
    return {"actions": actions, "character_acting": acting,
            "spatial_blocking": {"summary": state["visible_start_state"], "subjects": subjects, "prop_attachments": []}}


def project_adopted_h3_fields(plan, director_context: str):
    """Bind adopted fields at the model boundary, before the independent gate.

    The model owns provider-specific audio/frame interpretation, not a second
    copy of the director's decisions. Never repair a changed shot boundary here.
    """
    from .h3_director_plan import H3DirectorPlan

    try:
        context = json.loads(director_context)
    except (ValueError, TypeError):
        return plan
    if not isinstance(context, dict):
        return plan
    states = context.get("source_shot_states", ())
    if not any(state.get("creative_contract_version") == 1 for state in states):
        return plan
    if len(states) != len(plan.shots) or any(state.get("creative") is None for state in states):
        raise ValueError("capability_conflict: H3 shot count cannot preserve adopted shot boundaries")
    data = plan.model_dump(mode="json")
    rigid = data.get("rigid_prompt")
    if rigid is None:
        raise ValueError("capability_conflict: adopted H3 fields require rigid_prompt")
    acting = None
    lighting = None
    for state, shot in zip(states, data["shots"], strict=True):
        if abs((shot["end_frame"] - shot["start_frame"]) / plan.fps - state["duration_seconds"]) > 1 / plan.fps:
            raise ValueError(f"capability_conflict: {state['shot_id']} duration was changed by H3")
        creative = state["creative"]
        shot.update(creative_h3_slots(creative))
        overlap = creative_h3_rigid_slots(creative)
        if "optics" in overlap:
            rigid["optics"] = [item for item in rigid["optics"] if item["shot_id"] != shot["shot_id"]]
            rigid["optics"].append({"shot_id": shot["shot_id"], **overlap["optics"]})
        if "lighting" in overlap:
            if lighting is not None and lighting != overlap["lighting"]:
                raise ValueError("capability_conflict: adopted per-shot lighting cannot share global H3 lighting")
            lighting = overlap["lighting"]
            rigid["lighting"].update(lighting)
        motion = creative.get("motion", {})
        if motion.get("mode") == "fixed":
            shot["camera"] = {"type": "static"}
        elif motion.get("mode") == "moving":
            shot["camera"] = {"type": "moving", "direction": motion["path"], "speed": motion["speed_profile"]}
        if "visible_start_state" in state and "visible_end_state" in state:
            execution = creative_h3_execution_slots(state, plan.fps)
            if acting is not None and acting != execution["character_acting"]:
                raise ValueError("capability_conflict: adopted per-shot acting cannot share global H3 acting")
            acting = execution["character_acting"]
            rigid["character_acting"] = acting
            rigid["spatial_blocking"] = [item for item in rigid["spatial_blocking"] if item["shot_id"] != shot["shot_id"]]
            rigid["spatial_blocking"].append({"shot_id": shot["shot_id"], **execution["spatial_blocking"]})
            shot["actions"] = [{**action, "phase": "establish" if index == 0 else "execute",
                                "start_frame": action["start_frame"] + shot["start_frame"],
                                "end_frame": action["end_frame"] + shot["start_frame"]}
                               for index, action in enumerate(execution["actions"])]
    return H3DirectorPlan.model_validate(data)


def verify_h3_creative_contract(plan, director_context: str) -> None:
    """Reject provider re-cutting and rewriting before wire compilation."""
    if not director_context:
        return
    try:
        context = json.loads(director_context)
    except (ValueError, TypeError):
        return  # Existing legacy director prose is not this versioned contract.
    if not isinstance(context, dict):
        return
    states = context.get("source_shot_states", ())
    adopted = [state for state in states if state.get("creative") is not None]
    if not adopted:
        return
    if len(adopted) != len(states) or len(plan.shots) != len(states):
        raise ValueError("capability_conflict: H3 shot count cannot preserve adopted shot boundaries")
    for state, shot in zip(states, plan.shots, strict=True):
        shot_id = state["shot_id"]
        if abs((shot.end_frame - shot.start_frame) / plan.fps - state["duration_seconds"]) > 1 / plan.fps:
            raise ValueError(f"capability_conflict: {shot_id} duration was changed by H3")
        creative = state["creative"]
        for name, expected in creative_h3_slots(creative).items():
            if getattr(shot, name) != expected:
                raise ValueError(f"capability_conflict: {shot_id} adopted {name} was rewritten by H3")
        rigid = getattr(plan, "rigid_prompt", None)
        for section, expected in creative_h3_rigid_slots(creative).items():
            actual = None
            if rigid is not None:
                actual = (next((item for item in rigid.optics if item.shot_id == shot.shot_id), None)
                          if section == "optics" else getattr(rigid, section, None))
            if actual is None or any(getattr(actual, name, None) != value for name, value in expected.items()):
                raise ValueError(f"capability_conflict: {shot_id} adopted {section} was rewritten by H3")
        motion = creative.get("motion", {})
        if "visible_start_state" in state and "visible_end_state" in state:
            execution = creative_h3_execution_slots(state, plan.fps)
            actual_actions = [{"start_frame": action.start_frame - shot.start_frame,
                               "end_frame": action.end_frame - shot.start_frame,
                               "description": action.description} for action in shot.actions]
            if actual_actions != execution["actions"]:
                raise ValueError(f"capability_conflict: {shot_id} adopted actions were rewritten by H3")
            if rigid is None:
                raise ValueError(f"capability_conflict: {shot_id} missing adopted acting/blocking")
            if [item.model_dump(mode="json") for item in rigid.character_acting] != execution["character_acting"]:
                raise ValueError(f"capability_conflict: {shot_id} adopted character_acting was rewritten by H3")
            blocking = next((item for item in rigid.spatial_blocking if item.shot_id == shot.shot_id), None)
            if blocking is None or blocking.model_dump(mode="json", exclude={"shot_id"}) != execution["spatial_blocking"]:
                raise ValueError(f"capability_conflict: {shot_id} adopted spatial_blocking was rewritten by H3")
        if motion.get("mode") in ("fixed", "moving"):
            fixed = motion["mode"] == "fixed"
            if shot.camera.is_static != fixed:
                raise ValueError(f"capability_conflict: {shot_id} camera motion contradicts adopted decision")
            if not fixed and (shot.camera.direction != motion["path"] or shot.camera.speed != motion["speed_profile"]):
                raise ValueError(f"capability_conflict: {shot_id} camera path or speed was rewritten by H3")
