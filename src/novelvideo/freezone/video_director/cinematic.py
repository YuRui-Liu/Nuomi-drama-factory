"""Compile a cinematic brief from only the facts supplied by the director."""

from __future__ import annotations


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "；".join(text for item in value if (text := _text(item)))
    if isinstance(value, dict):
        return "；".join(
            f"{key}：{text}"
            for key, item in value.items()
            if (text := _text(item))
        )
    return str(value).strip()


def _subjects(director: dict) -> list[dict]:
    blocking = director.get("blocking") or {}
    return [item for item in blocking.get("subjects") or [] if isinstance(item, dict)]


def _scene_context(row: dict, director: dict) -> str:
    scene = _text(director.get("scene_context") or row.get("scene_context"))
    if scene:
        return scene

    sections = (director.get("panel_spec") or {}).get("sections") or {}
    for key in ("scene_context", "setting", "continuity"):
        section = _text(sections.get(key))
        if not section:
            continue
        prefix = "场景空间图："
        if section.startswith(prefix):
            return section.removeprefix(prefix).strip()
        return section
    return "未提供"


def _camera_motion(row: dict, director: dict) -> str:
    framing = director.get("framing") or {}
    camera = director.get("camera") or {}
    return _text(
        row.get("camera_motion")
        or director.get("camera_motion")
        or framing.get("camera_motion")
        or camera.get("motion")
    ) or "未提供"


def _subject_staging(subjects: list[dict]) -> str:
    rows: list[str] = []
    for subject in subjects:
        name = _text(subject.get("id") or subject.get("name"))
        details = []
        for key in ("screen_position", "facing", "gaze", "action", "pose", "state"):
            value = _text(subject.get(key))
            if value:
                details.append(value)
        if name or details:
            rows.append(f"{name}：" + "；".join(details) if details else name)
    return "；".join(rows) or "未提供"


def _performances(row: dict, director: dict, subjects: list[dict]) -> str:
    performances = []
    overall = _text(director.get("performance") or row.get("performance"))
    if overall:
        performances.append(overall)
    for subject in subjects:
        performance = _text(subject.get("performance"))
        if not performance:
            continue
        name = _text(subject.get("id") or subject.get("name"))
        performances.append(f"{name}：{performance}" if name else performance)
    return "；".join(performances) or "未提供"


def _lighting(director: dict) -> str:
    lights = director.get("lights") or []
    rendered = []
    for light in lights:
        if isinstance(light, dict):
            parts = [
                _text(light.get(key))
                for key in ("type", "direction", "temperature", "intensity")
            ]
            rendered.append("，".join(part for part in parts if part))
        else:
            rendered.append(_text(light))
    return "；".join(part for part in rendered if part) or "未提供"


def _dialogue(row: dict) -> str:
    prompt = str(row.get("shot_prompt") or "")
    for line in reversed(prompt.splitlines()):
        if "：“" in line and line.rstrip().endswith("”"):
            return line.strip()
    return ""


def compile_cinematic_brief(row: dict) -> str:
    director = row.get("director") or {}
    blocking = director.get("blocking") or {}
    framing = director.get("framing") or {}
    continuity = director.get("continuity") or {}
    subjects = _subjects(director)

    intent = _text(director.get("intent") or row.get("intent")) or "未提供"
    technique = _text(director.get("technique_intent"))
    visual_description = _text(row.get("visual_description")) or "未提供"
    framing_parts = [
        _text(framing.get("shot_size") or row.get("shot")),
        _text(framing.get("angle")),
    ]
    camera_side = _text(blocking.get("camera_side"))
    axis = _text(blocking.get("axis"))
    screen_direction = _text(blocking.get("screen_direction"))
    start_state = _text(
        continuity.get("start_state") or director.get("start_state") or row.get("start_state")
    ) or "未提供"
    end_state = _text(
        continuity.get("end_state") or director.get("end_state") or row.get("end_state")
    ) or "未提供"
    sound = _text(row.get("sound") or row.get("soundscape") or director.get("soundscape"))
    dialogue = _dialogue(row)

    lines = [
        "场景上下文：" + _scene_context(row, director),
        "导演意图：" + intent,
    ]
    if technique:
        lines.append("所选技法：" + technique)
    lines += [
        "动作顺序：" + visual_description,
        "主体调度：" + _subject_staging(subjects),
        "表演：" + _performances(row, director, subjects),
        "运镜：" + _camera_motion(row, director),
        "景别与角度：" + (" / ".join(part for part in framing_parts if part) or "未提供"),
        "机位侧：" + (camera_side or "未提供"),
        "轴线：" + (axis or "未提供"),
        "屏幕方向：" + (screen_direction or "未提供"),
        "起始状态：" + start_state,
        "结束状态：" + end_state,
        "光线：" + _lighting(director),
        "声音：" + (sound or "未提供"),
    ]
    if row.get("duration") is not None:
        lines.append("片长：" + _text(row.get("duration")) + " 秒")
    if dialogue:
        lines.append("台词（逐字）：" + dialogue)
    return "\n".join(lines)
