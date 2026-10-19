"""B 切片（导演分镜）panel 规格编译器。

确定性编译器：把镜头与组的事实组装成拍摄语言。世界坐标只用于推导屏幕事实，
绝不进入成品文本；一旦发现残留坐标即记 `panel_coordinate_leak` 并移除。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PanelSection = Literal[
    "count",
    "shot",
    "axis",
    "blocking",
    "lighting",
    "performance",
    "props",
    "continuity",
]

SECTION_ORDER: tuple[PanelSection, ...] = (
    "count",
    "shot",
    "axis",
    "blocking",
    "lighting",
    "performance",
    "props",
    "continuity",
)

# 世界坐标数组（如 [0.7, 0, 0.3]）与坐标键名是唯一被拦截的形态；
# 地标 + 米数（“距机位 3 米”“1.5 米高”）属于允许的拍摄语言。
_COORDINATE_ARRAY = re.compile(r"\[\s*[-+]?\d+(?:\.\d+)?(?:\s*,\s*[-+]?\d+(?:\.\d+)?){1,}\s*\]")
_WORLD_COORDINATE_KEY = re.compile(r"world_(?:position|origin)\s*[:=]?")


class PanelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: dict[str, str] = Field(default_factory=dict)
    prompt_text: str = ""
    negatives: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def _raw(source: Any, name: str) -> Any:
    """读取蛇形字段，同时接受 pydantic 模型、SimpleNamespace 与纯 dict。"""
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _field(source: Any, name: str, default: str = "") -> str:
    value = _raw(source, name)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return value


def _items(source: Any, name: str) -> list[Any]:
    value = _raw(source, name)
    if not value:
        return []
    return list(value)


def _strip_world_coordinates(text: str) -> tuple[str, list[str]]:
    leaks: list[str] = []

    def drop_array(match: re.Match[str]) -> str:
        leaks.append(match.group(0))
        return ""

    def drop_key(match: re.Match[str]) -> str:
        leaks.append(match.group(0).strip())
        return ""

    cleaned = _COORDINATE_ARRAY.sub(drop_array, text)
    cleaned = _WORLD_COORDINATE_KEY.sub(drop_key, cleaned)
    return cleaned, leaks


def _join(parts: list[str], separator: str = "；") -> str:
    return separator.join(part for part in parts if part)


def compile_panel_spec(shot, group) -> PanelSpec:
    cinematography = _raw(shot, "cinematography")
    subjects = _items(cinematography, "subjects")
    props = _items(cinematography, "props")
    lights = _items(cinematography, "lights")
    geo_layout = _field(group, "geo_layout")
    issues: list[str] = []
    leaks: list[str] = []

    def missing(name: str) -> None:
        issues.append(f"panel_spec_missing_field: {name}")

    def clean(text: str) -> str:
        cleaned, found = _strip_world_coordinates(text)
        leaks.extend(found)
        return cleaned

    shot_size = _field(shot, "shot_size")
    camera_angle = _field(shot, "camera_angle")
    composition = _field(shot, "composition")
    axis = _field(cinematography, "axis")
    camera_side = _field(cinematography, "camera_side")
    screen_direction = _field(cinematography, "screen_direction")
    key_light_id = _field(cinematography, "key_light_id")
    shadow_direction = _field(cinematography, "shadow_direction")
    exposure_priority = _field(cinematography, "exposure_priority")
    action = _field(shot, "action")
    start_state = _field(shot, "visible_start_state")
    end_state = _field(shot, "visible_end_state")

    if not shot_size:
        missing("shot_size")
    if not camera_angle:
        missing("camera_angle")
    if cinematography is None:
        missing("cinematography")
    elif not axis and subjects:
        missing("axis")
    if not lights:
        missing("lights")
    if not start_state:
        missing("visible_start_state")
    if not end_state:
        missing("visible_end_state")
    if not geo_layout:
        missing("geo_layout")

    count = f"画面内恰好 {len(subjects)} 人" if subjects else "画面内无人物"
    if props:
        count = f"{count}，道具 {len(props)} 件"

    shot_text = _join([shot_size, camera_angle, composition])

    axis_text = _join([
        f"轴线 {axis}" if axis else "",
        f"机位在 {camera_side}" if camera_side else "",
        f"屏幕运动 {screen_direction}" if screen_direction else "",
        "屏幕方向只从机位描述",
    ])

    blocking = _join([
        "，".join(part for part in (
            f"{_field(subject, 'subject_id')} 在{_field(subject, 'screen_position')}"
            if _field(subject, "screen_position") else _field(subject, "subject_id"),
            _field(subject, "facing"),
            f"视线{_field(subject, 'gaze_target')}" if _field(subject, "gaze_target") else "",
        ) if part)
        for subject in subjects
    ]) or "无站位要求"

    light_entries = [
        "，".join(part for part in (
            _join([
                _field(light, "source_type"),
                _field(light, "light_id"),
            ], " ") + (f" 来自{_field(light, 'direction')}" if _field(light, "direction") else ""),
            _field(light, "color_temperature"),
            f"强度{_field(light, 'relative_intensity')}" if _field(light, "relative_intensity") else "",
            f"动机{_field(light, 'motivation')}" if _field(light, "motivation") else "",
        ) if part)
        for light in lights
    ]
    light_extras = [
        f"关键光 {key_light_id}" if key_light_id else "",
        f"投影朝{shadow_direction}" if shadow_direction else "",
        f"曝光优先：{exposure_priority}" if exposure_priority else "",
    ]
    lighting_parts = light_entries + (["；".join(part for part in light_extras if part)] if any(light_extras) else [])
    lighting = _join(lighting_parts) or "沿用场景光逻辑"

    performance = _join([
        f"表演：{action}" if action else "",
        f"起始：{start_state}" if start_state else "",
        f"结束：{end_state}" if end_state else "",
    ])

    prop_entries = [
        "，".join(part for part in (
            f"道具 {_field(prop, 'prop_id')}" if _field(prop, "prop_id") else "",
            f"朝向{_field(prop, 'facing')}" if _field(prop, "facing") else "",
            f"{_field(prop, 'held_by')}持握" if _field(prop, "held_by") else "",
        ) if part)
        for prop in props
    ]
    props_text = _join(prop_entries) or "无额外道具"

    continuity = _join([
        f"场景空间图：{geo_layout}" if geo_layout else "",
        "动作只写正向，不写禁止",
    ])

    sections = {
        "count": clean(count),
        "shot": clean(shot_text),
        "axis": clean(axis_text),
        "blocking": clean(blocking),
        "lighting": clean(lighting),
        "performance": clean(performance),
        "props": clean(props_text),
        "continuity": clean(continuity),
    }
    issues.extend(f"panel_coordinate_leak: {leak}" for leak in leaks)
    prompt_text = "\n".join(
        f"{name}: {value}" for name, value in sections.items() if value
    )
    return PanelSpec(sections=sections, prompt_text=prompt_text, negatives=[], issues=issues)
