"""Compile a directable cinematic brief from a director-plan row."""

from __future__ import annotations

from dataclasses import dataclass

PHASE_SEPARATOR = " → "
INFECTED_MARKERS = ("丧尸", "感染者")
INTENT_TEXT = {
    "zombie_bypass": "威胁转移：丧尸绕过林渡，扑向幸存者",
    "zombie_gaze_past_protagonist": "视线转移：丧尸越过林渡肩头，锁定街角",
    "sudden_hand_insert": "突发：一只手从前景插入，逼近镜头",
    "over_shoulder_confrontation": "对峙：越过肩头看主角，压迫感逼近",
    "street_crossing": "穿越：主角在废车与感染者之间穿行",
    "mirror_reveal": "揭示：后视镜里出现跟随者",
}


@dataclass(frozen=True)
class Beat:
    start: float
    end: float
    action: str
    micro: str


def _phases(description: str) -> list[str]:
    return [part.strip() for part in description.split(PHASE_SEPARATOR) if part.strip()]


def _subjects(director: dict) -> list[dict]:
    blocking = director.get("blocking") or {}
    return [item for item in blocking.get("subjects") or [] if item.get("id")]


def _has_infected(subjects: list[dict]) -> bool:
    return any(any(marker in str(item.get("id", "")) for marker in INFECTED_MARKERS) for item in subjects)


def _micro_event(name: str) -> str:
    if any(marker in name for marker in INFECTED_MARKERS):
        return "承重腿先落、拖拽腿随后，头晚半拍才转"
    if "幸存者" in name:
        return "手指攥紧背包带，脚跟蹭地"
    return "肩胛下沉，呼吸压低，视线先到、头随后"


def _first_micro(subjects: list[dict]) -> str:
    if not subjects:
        return "呼吸压低，视线先到"
    return _micro_event(str(subjects[0].get("id") or ""))


def _split(duration: float, phases: list[str], subjects: list[dict]) -> list[Beat]:
    if not phases:
        phases = ["保持起始状态"]
    ratios = {1: [1.0], 2: [0.4, 0.6], 3: [0.2, 0.5, 0.3]}.get(len(phases))
    if ratios is None:
        ratios = [1 / len(phases)] * len(phases)
    beats: list[Beat] = []
    cursor = 0.0
    for index, phase in enumerate(phases):
        end = duration if index == len(phases) - 1 else round(cursor + duration * ratios[index], 2)
        beats.append(Beat(cursor, end, phase, _first_micro(subjects)))
        cursor = end
    return beats


def _dialogue(row: dict) -> str:
    prompt = str(row.get("shot_prompt") or "")
    for line in reversed(prompt.splitlines()):
        if "：“" in line and line.rstrip().endswith("”"):
            return line.strip()
    return ""


def compile_cinematic_brief(row: dict) -> str:
    director = row.get("director") or {}
    duration = float(row.get("duration") or 0)
    subjects = _subjects(director)
    blocking = director.get("blocking") or {}
    camera_side = str(blocking.get("camera_side") or "机位未声明").strip()
    axis = str(blocking.get("axis") or "轴线未声明").strip()
    screen_direction = str(blocking.get("screen_direction") or "").strip()
    continuity = director.get("continuity") or {}
    start_state = str(continuity.get("start_state") or "").strip()
    end_state = str(continuity.get("end_state") or "").strip()
    phases = _phases(str(row.get("visual_description") or ""))
    beats = _split(duration, phases, subjects)
    technique = str(director.get("technique_intent") or "").strip()
    framing = director.get("framing") or {}
    shot_size = str(framing.get("shot_size") or row.get("shot") or "medium").strip()
    angle = str(framing.get("angle") or "eye_level").strip()
    lights = director.get("lights") or []

    scene_map = ""
    continuity_section = str((director.get("panel_spec") or {}).get("sections", {}).get("continuity") or "")
    for line in continuity_section.split("；"):
        if line.startswith("场景空间图："):
            scene_map = line.removeprefix("场景空间图：").strip()
    if not scene_map:
        scene_map = "主干道与街角；东侧护栏；两侧废车与碎片"

    light_text = "；".join(
        "，".join(part for part in (
            str(light.get("type") or ""),
            str(light.get("direction") or ""),
            str(light.get("temperature") or ""),
            str(light.get("intensity") or ""),
        ) if part)
        for light in lights
    ) or "沿用场景光逻辑"

    task = INTENT_TEXT.get(technique) or (end_state or "完成本镜动作")
    first_two = (beats[0].action if beats else start_state) + "；" + _first_micro(subjects) + "。",

    lines = [
        "GEO (locked): " + scene_map + "；机位始终在" + camera_side + "，不越轴；轴线：" + axis + "。",
        "",
        "镜头任务: " + task,
        "",
        "首 2 秒: " + first_two[0],
        "",
        "节拍:",
    ]
    for index, beat in enumerate(beats):
        suffix = "（微事件：" + beat.micro + "）" if index == 0 else ""
        lines.append(f"{beat.start:.1f}–{beat.end:.1f}s " + beat.action + suffix)
    lines += ["", "表演:"]
    for item in subjects:
        parts = []
        if item.get("screen_position"):
            parts.append("在" + str(item["screen_position"]))
        if item.get("facing"):
            parts.append(str(item["facing"]))
        if item.get("gaze"):
            parts.append("视线" + str(item["gaze"]))
        parts.append("微事件：" + _micro_event(str(item.get("id") or "")))
        lines.append(str(item.get("id") or "") + "：" + "；".join(parts))
    if _has_infected(subjects):
        lines += [
            "",
            "感染者运动: 重量（先承重腿、再拖拽腿）；延迟（躯干先转、头晚半拍）；非同步（多人不同步、不同朝向）；惯性（停步前冲半拍、肘先于手）；病态靠肤色/失焦/关节迟滞，无血、无烂眼。",
        ]
    lines += [
        "",
        "运镜: Static Shot，" + shot_size + "/" + angle + "，机位在" + camera_side + "；不 Pan、不 Push，靠人物在画面内位移完成叙事。",
        "",
        "光: " + light_text + "。",
        "",
        "声音: " + (str(row.get("sound") or "").strip() or ("风声、远处金属、拖步擦沥青、布料摩擦与呼吸；无配乐。" if _has_infected(subjects) else "风声、远处金属、脚步与呼吸；无配乐。")),
        "",
        "连续性: 起始 " + (start_state or "见分镜") + "；结束 " + (end_state or "保持姿态") + "；屏幕方向 " + (screen_direction or "只从机位描述") + "。",
    ]
    dialogue = _dialogue(row)
    if dialogue:
        lines += ["", "台词（逐字）: " + dialogue]
    return "\n".join(lines).strip()