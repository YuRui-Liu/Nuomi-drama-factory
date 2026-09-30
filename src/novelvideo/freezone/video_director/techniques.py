"""Versioned, read-only director techniques for the canvas H3 workflow.

These are original descriptions of reusable direction, not third-party prompts.
Each source identifies a specific creator case or official guide. Provenance is
``analysis_only`` because the cards adapt direction rather than reproduce prompts.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from novelvideo.media_capabilities.video.h3_timeline import H3_FPS, frames_for_duration


CATALOG_VERSION = "2026-09-30.2"
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class TechniqueSource(BaseModel):
    model_config = _MODEL_CONFIG

    url: str = Field(pattern=r"^https://", min_length=9)
    credit: str = Field(min_length=1)
    source_type: Literal["author_original", "reconstructed", "analysis_only"]
    checked_at: date
    basis: str = Field(min_length=1)


class TechniqueApplicability(BaseModel):
    model_config = _MODEL_CONFIG

    modes: tuple[Literal["i2v", "fl2v", "ref_only"], ...] = Field(min_length=1)
    min_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    last_frame_constraint: Literal["required", "allowed", "forbidden"] = "allowed"

    @model_validator(mode="after")
    def validate_range_and_frames(self) -> "TechniqueApplicability":
        if self.max_duration_seconds < self.min_duration_seconds:
            raise ValueError("max_duration_seconds must be at least min_duration_seconds")
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("duplicate applicability mode")
        if self.last_frame_constraint == "required" and set(self.modes) != {"fl2v"}:
            raise ValueError("required last frame only applies to fl2v")
        if self.last_frame_constraint == "forbidden" and "fl2v" in self.modes:
            raise ValueError("fl2v requires a last frame")
        return self


class TechniqueCard(BaseModel):
    model_config = _MODEL_CONFIG

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$", min_length=1)
    version: str = Field(min_length=1)
    status: Literal["active", "retired"]
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    category: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    action_beats: tuple[str, ...] = Field(min_length=1)
    performance: str = Field(min_length=1)
    camera: str = Field(min_length=1)
    ending_composition: str = Field(min_length=1)
    avoid: tuple[str, ...] = Field(min_length=1)
    applicability: TechniqueApplicability
    sources: tuple[TechniqueSource, ...] = Field(min_length=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """Hash only instructions that can reach the optimizer projection."""
        payload = {key: getattr(self, key) for key in (
            "intent", "action_beats", "performance", "camera", "ending_composition", "avoid",
        )}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()


def _source(basis: str, credit: str, url: str) -> TechniqueSource:
    return TechniqueSource(url=url, credit=credit,
                           source_type="analysis_only", checked_at=date(2026, 9, 30),
                           basis=basis)


def _card(*, id: str, title: str, summary: str, category: str, intent: str,
          action_beats: tuple[str, ...], performance: str, camera: str,
          ending_composition: str, avoid: tuple[str, ...],
          modes: tuple[Literal["i2v", "fl2v", "ref_only"], ...],
          min_seconds: float, max_seconds: float, source_basis: str,
          source_credit: str, source_url: str,
          last_frame_constraint: Literal["required", "allowed", "forbidden"] = "allowed",
          ) -> TechniqueCard:
    return TechniqueCard(
        id=id, version="1.0.0", status="active", title=title, summary=summary,
        category=category, intent=intent, action_beats=action_beats,
        performance=performance, camera=camera,
        ending_composition=ending_composition, avoid=avoid,
        applicability=TechniqueApplicability(
            modes=modes, min_duration_seconds=min_seconds,
            max_duration_seconds=frames_for_duration(max_seconds, H3_FPS) / H3_FPS,
            last_frame_constraint=last_frame_constraint,
        ),
        sources=(_source(source_basis, source_credit, source_url),),
    )


_CARDS: tuple[TechniqueCard, ...] = (
    _card(
        id="fixed-reaction", title="定机位反应", summary="保持构图，让人物对已发生的事给出可读反应。",
        category="人物反应", intent="让观众在不换机位的情况下看清反应的起点和结果。",
        action_beats=("先维持原有姿态和视线", "一个外部刺激后出现明确但克制的反应", "动作停稳并留出观看时间"),
        performance="用呼吸、视线和轻微姿态变化表达反应，不凭空添加刺激来源。",
        camera="固定机位与原有景别，保持人物在画面中的位置。",
        ending_composition="结束时保留反应后的姿态与原有空间关系。",
        avoid=("无缘由的摇镜或切镜", "新人物或新事件"),
        modes=("i2v", "fl2v"), min_seconds=2, max_seconds=12,
        source_basis="Fluffi 观影案例以锁定电视视角、短暂惊吓和较长恢复段呈现反应；本卡仅提炼定镜与停顿。",
        source_credit="@GlennHasABeard", source_url="https://x.com/GlennHasABeard/status/2095573685671469310",
    ),
    _card(
        id="expression-build", title="表情递进", summary="从细微表情逐步走向清晰情绪。",
        category="人物反应", intent="用连续的面部与身体变化呈现一次情绪转折。",
        action_beats=("表情先保持中性或原始状态", "眼神与呼吸逐级变化", "情绪在末段变得清晰并稳定"),
        performance="让眉眼、嘴角和呼吸按顺序变化，避免同时夸张启动。",
        camera="保持稳定的近景或中近景，只在有空间余量时轻微推进。",
        ending_composition="在可辨认的最终表情上结束，不额外制造反转。",
        avoid=("突然改写人物情绪动机", "夸张形变或脸部身份漂移"),
        modes=("i2v", "ref_only"), min_seconds=3, max_seconds=12,
        source_basis="Relationship Confession 案例让丈夫的愤怒转为难以置信，并以停顿和目光落点收束；本卡提炼渐进表演。",
        source_credit="@NEXUS_TO_NOVA", source_url="https://x.com/NEXUS_TO_NOVA/status/2082548512286224793",
        last_frame_constraint="forbidden",
    ),
    _card(
        id="two-person-gaze", title="双人视线交接", summary="以视线和停顿说明两人关系。",
        category="双人调度", intent="让两人间的注意力转移在同一连续镜头内可读。",
        action_beats=("先确认两人的位置和视线", "一人先看向另一人", "对方回应视线并保留短暂停顿"),
        performance="回应的时机有先后，不强迫对白或交换人物身份。",
        camera="稳定地保留两人或交替可见的空间关系，不跳轴。",
        ending_composition="两人的视线关系明确，仍符合输入画面位置。",
        avoid=("凭空加入第三人", "无动机的反打和跳轴"),
        modes=("i2v", "fl2v", "ref_only"), min_seconds=3, max_seconds=15,
        source_basis="Relationship Confession 案例保持夫妻左右站位，先抬眼、回避，再回应目光；本卡提炼视线交接。",
        source_credit="@NEXUS_TO_NOVA", source_url="https://x.com/NEXUS_TO_NOVA/status/2082548512286224793",
    ),
    _card(
        id="lateral-follow", title="侧向跟拍", summary="镜头随主体横向移动并保持方向清楚。",
        category="跟拍", intent="通过连续侧向运动展示主体的行进，不改变目标位置。",
        action_beats=("主体按原有方向起步", "镜头以相近速度横向跟随", "主体减速时镜头也稳定下来"),
        performance="动作速度可读，保持身份与服装连续。",
        camera="单向侧移，维持主体在画面中的相对位置和空间轴线。",
        ending_composition="主体在目标位置附近停稳，画面保留行进方向。",
        avoid=("突然反向运动", "跟拍中无理由穿越场景"),
        modes=("i2v", "ref_only"), min_seconds=4, max_seconds=15,
        source_basis="Stealth Aircraft Flyby 案例以一次连续侧向跟镜维持运动方向，末尾仍沿原轨迹离画；本卡提炼单向跟拍。",
        source_credit="@opener_ai", source_url="https://x.com/opener_ai/status/2084441225667735905",
        last_frame_constraint="forbidden",
    ),
    _card(
        id="slow-push-in", title="缓慢推近", summary="在已有主体上逐渐收紧注意力。",
        category="揭示运镜", intent="用轻缓推进突出已有信息或人物决定。",
        action_beats=("开头维持输入构图", "沿主体轴线逐渐推近", "在关键表情或物件上停稳"),
        performance="人物保持原有动作意图，推近不强迫额外表演。",
        camera="单次连续推近，避免变焦跳动和横向换机位。",
        ending_composition="主体占比增加且仍与原有空间和尾帧相容。",
        avoid=("推近时凭空出现细节", "尾帧前再启动不相关动作"),
        modes=("i2v", "fl2v", "ref_only"), min_seconds=3, max_seconds=15,
        source_basis="Radio Operator Evacuation Bridge 案例在信号恢复后缓推到人物面部，动作和情绪同步收束；本卡提炼推近落点。",
        source_credit="@Diplomeme", source_url="https://x.com/Diplomeme/status/2082770042630943156",
    ),
    _card(
        id="subject-entrance", title="主体入画揭示", summary="让已有主体从画外进入并形成清楚落点。",
        category="揭示运镜", intent="利用画内空位和运动方向逐步揭示已有主体。",
        action_beats=("先展示输入画面的空位", "已有主体沿合理路径入画", "主体到达叙事落点后停稳"),
        performance="入画动作符合人物当前状态，不增加新人物。",
        camera="以稳定机位为主，必要时轻微跟随入画路径。",
        ending_composition="主体与原有场景位置关系清楚。",
        avoid=("从不可能的方向瞬移入画", "虚构画外主体"),
        modes=("i2v", "ref_only"), min_seconds=4, max_seconds=15,
        source_basis="Giant Kitchen Spider 案例先建立厨房和画内位置，再让持杯人物入画并停住；本卡提炼入画路径。",
        source_credit="@Ciri_ai", source_url="https://x.com/Ciri_ai/status/2082840410268057697",
        last_frame_constraint="forbidden",
    ),
    _card(
        id="contained-climax", title="受约束动作高潮", summary="在单一已设定动作上增加节奏与清晰度。",
        category="动作高潮", intent="让原有动作有准备、发生和可读的结果。",
        action_beats=("先建立动作的起始姿态", "完成一次主要动作并展示影响", "动作后稳定落位"),
        performance="控制力度与反应时机，不添加未设定的打斗或伤害。",
        camera="跟随主要动作，避免高频切镜遮蔽因果。",
        ending_composition="动作结果清楚且人物与物件位置连续。",
        avoid=("连锁新增动作", "人物或道具连续性丢失"),
        modes=("i2v", "fl2v", "ref_only"), min_seconds=5, max_seconds=15,
        source_basis="Storm-Cliff Golf 案例以站稳、挥杆、球落杯完成一个动作及结果；本卡提炼准备、发生、落位三段。",
        source_credit="@Dheepanratnam", source_url="https://x.com/Dheepanratnam/status/2082799981426037151",
    ),
    _card(
        id="endpoint-continuity", title="首尾帧连续过渡", summary="用一个可信动作衔接已给定的起点与终点。",
        category="首尾帧", intent="尊重两张指定画面，补足中间的连续运动。",
        action_beats=("从首帧姿态和空间关系出发", "只补足到达尾帧所需的变化", "在尾帧对应构图前收束动作"),
        performance="人物表演服从首尾状态，不额外制造尾帧没有的动作。",
        camera="运动仅用于连接已给定构图，避免突然转场或换轴。",
        ending_composition="严格对齐用户提供的尾帧人物、物件和构图。",
        avoid=("尾帧前新增无法收束的动作", "改变尾帧主体或构图"),
        modes=("fl2v",), min_seconds=3, max_seconds=15,
        source_basis="MiniMax 官方基础提示指南说明首尾帧模式须由两张图约束起点与终点；本卡将连续过渡整理为原创工作流。",
        source_credit="MiniMax-AI", source_url="https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt",
        last_frame_constraint="required",
    ),
)


def list_techniques() -> tuple[TechniqueCard, ...]:
    """Return the stable curated catalog without network access."""
    return _CARDS


def resolve_technique(card_id: str, version: str) -> TechniqueCard:
    """Resolve one active catalog version for a new generation attempt."""
    card = next((item for item in _CARDS if item.id == card_id and item.version == version), None)
    if card is None:
        raise ValueError(f"unknown technique {card_id}@{version}")
    if card.status != "active":
        raise ValueError(f"inactive technique {card_id}@{version}")
    return card


def check_applicability(card: TechniqueCard, mode: str,
                        duration_seconds: float) -> dict:
    """Check a draft segment using the same legal H3 frame alignment as submission."""
    reasons: list[dict[str, str]] = []
    if card.status != "active":
        reasons.append({"field": "status", "code": "inactive_technique",
                        "message": "Technique version is no longer active."})
    if mode not in card.applicability.modes:
        reasons.append({"field": "mode", "code": "unsupported_mode",
                        "message": f"Technique does not support {mode}; supported modes: {', '.join(card.applicability.modes)}."})
    try:
        aligned = frames_for_duration(duration_seconds, H3_FPS) / H3_FPS
    except (ValueError, OverflowError, TypeError):
        aligned = None
        reasons.append({"field": "duration_seconds", "code": "invalid_duration",
                        "message": "Duration must be positive and finite."})
    if aligned is not None and aligned < card.applicability.min_duration_seconds - 1e-9:
        reasons.append({"field": "duration_seconds", "code": "duration_too_short",
                        "message": f"Aligned duration {aligned:g}s is below {card.applicability.min_duration_seconds:g}s."})
    if aligned is not None and aligned > card.applicability.max_duration_seconds + 1e-9:
        reasons.append({"field": "duration_seconds", "code": "duration_too_long",
                        "message": f"Aligned duration {aligned:g}s exceeds {card.applicability.max_duration_seconds:g}s."})
    return {"compatible": not reasons, "aligned_duration_seconds": aligned, "reasons": reasons}


def project_technique(card: TechniqueCard) -> dict:
    """Freeze only reusable direction, free of external prompt text and links."""
    return {key: value for key, value in card.model_dump(mode="json").items() if key in {
        "id", "version", "content_hash", "intent", "action_beats", "performance",
        "camera", "ending_composition", "avoid",
    }}


__all__ = ["CATALOG_VERSION", "TechniqueSource", "TechniqueApplicability", "TechniqueCard",
           "list_techniques", "resolve_technique", "check_applicability", "project_technique"]
