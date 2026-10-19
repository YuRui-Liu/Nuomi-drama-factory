"""B 切片：导演计划 → 分镜表行。

确定性投影：同输入同输出、字段顺序固定。计划是唯一真源，表只是它的读模型；
行内的 group_id / segment_index 与 `director` 块使用同一来源，避免两套地址漂移。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from novelvideo.director_plan.panel_spec import compile_panel_spec


def _raw(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _text(source: Any, name: str) -> str:
    value = _raw(source, name)
    if value is None:
        return ""
    return str(value).strip()


def _items(source: Any, name: str) -> list[Any]:
    value = _raw(source, name)
    if not value:
        return []
    return list(value)


def _performance_entries(creative: Any) -> list[dict[str, str]]:
    decision = _raw(creative, "performance")
    return [
        {
            "id": _text(character, "subject_id"),
            "expression": _text(character, "visible_behavior"),
            "action": _text(character, "start_behavior"),
            "gaze": _text(character, "gaze"),
            "intensity": "",
        }
        for character in _items(decision, "characters")
    ]


def project_plan_rows(revision) -> list[dict]:
    rows: list[dict] = []
    for group in _items(revision, "groups"):
        group_id = _text(group, "id")
        for index, shot in enumerate(_items(group, "shots"), start=1):
            rows.append(
                _project_shot(
                    shot,
                    group,
                    group_id=group_id,
                    segment_index=index,
                    shot_no=len(rows) + 1,
                )
            )
    return rows


def _project_shot(shot, group, *, group_id: str, segment_index: int, shot_no: int) -> dict:
    spec = compile_panel_spec(shot, group)
    cinematography = _raw(shot, "cinematography")
    start_state = _text(shot, "visible_start_state")
    end_state = _text(shot, "visible_end_state")
    performance = _performance_entries(_raw(shot, "creative"))
    subjects = [
        {
            "id": _text(subject, "subject_id"),
            "screen_position": _text(subject, "screen_position"),
            "facing": _text(subject, "facing"),
            "gaze": _text(subject, "gaze_target"),
        }
        for subject in _items(cinematography, "subjects")
    ]
    lights = [
        {
            "id": _text(light, "light_id"),
            "type": _text(light, "source_type"),
            "direction": _text(light, "direction"),
            "temperature": _text(light, "color_temperature"),
            "intensity": _text(light, "relative_intensity"),
            "motivation": _text(light, "motivation"),
        }
        for light in _items(cinematography, "lights")
    ]
    visual_description = " → ".join(
        part
        for part in (
            start_state,
            _text(shot, "action"),
            end_state,
        )
        if part
    )
    duration = _raw(shot, "duration_seconds")
    return {
        "shot_no": shot_no,
        "group_id": group_id,
        "segment_index": segment_index,
        "duration": int(round(float(duration))) if duration is not None else 0,
        "visual_description": visual_description,
        "shot": _text(shot, "shot_size"),
        "camera_angle": _text(shot, "camera_angle"),
        "axis": _text(cinematography, "axis"),
        "key_light": _text(cinematography, "key_light_id"),
        "performance": "；".join(item["expression"] for item in performance if item["expression"]),
        "dialogue": " / ".join(str(item) for item in _items(shot, "dialogue_source_ids")),
        "sound": "",
        "director": {
            "group_id": group_id,
            "segment_index": segment_index,
            "technique_intent": _text(shot, "technique_intent"),
            "framing": {
                "shot_size": _text(shot, "shot_size"),
                "angle": _text(shot, "camera_angle"),
                "composition": _text(shot, "composition"),
            },
            "blocking": {
                "axis": _text(cinematography, "axis"),
                "camera_side": _text(cinematography, "camera_side"),
                "screen_direction": _text(cinematography, "screen_direction"),
                "subjects": subjects,
            },
            "lights": lights,
            "performance": performance,
            "props": [],
            "continuity": {
                "start_state": start_state,
                "end_state": end_state,
                "handoff": "",
            },
            "panel_spec": spec.model_dump(mode="json"),
        },
    }
