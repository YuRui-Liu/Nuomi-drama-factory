"""Pure, fail-closed structured H3 prompt optimization for canvas Director."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from novelvideo.knowledge_runtime.codex import StructuredImage, validate_structured_images
from novelvideo.media_capabilities.video.h3_prompt_profile import (
    H3_CANVAS_WRITING_RULES, H3_PROMPT_PROFILE_ID, H3_PROMPT_PROFILE_VERSION,
)
from novelvideo.media_capabilities.video.h3_wire import compile_h3_wire
from novelvideo.text_task_runtime.runtime import StructuredTextRuntime

from .capabilities import validate_generation
from .reference_plan import ReferencePlan, compile_reference_plan
from .models import (
    CanvasBaseWire, CanvasReferenceWire, DirectorDraft, FrozenTechniqueProjection, OptimizedDirector,
    OptimizedSegment,
)


class _OptimizedBaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    segment_id: str
    wire: CanvasBaseWire


class _OptimizedReferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    segment_id: str
    wire: CanvasReferenceWire


class _OptimizedTechniqueBaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    segment_id: str
    wire: CanvasBaseWire | None = None
    technique_conflict: str | None = Field(default=None, min_length=1)


class _OptimizedTechniqueReferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    segment_id: str
    wire: CanvasReferenceWire | None = None
    technique_conflict: str | None = Field(default=None, min_length=1)


class TechniqueConflictError(ValueError):
    """A selected card cannot be reconciled with frozen source facts."""


_EXPLICIT_DIALOGUE = re.compile(
    r"(?<!\w)(?P<speaker>(?:[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2}|"
    r"[^\W\d_][\w-]{0,39}))\s*"
    r"(?P<verb>(?:says?|said|asks?|asked|whispers?|whispered|shouts?|shouted|"
    r"replies|replied|说道|低声说|回答|说|问|喊|答)\s*[:：]?|[:：])\s*"
    r"(?P<quote>\"[^\"\n]*\"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」|『[^』\n]*』|'[^'\n]*')",
    flags=re.IGNORECASE,
)
_SUBJECT_TAG = re.compile(r"<Subject ([1-9][0-9]*)>")
_PICTURE_TAG = re.compile(r"<Picture ([1-9][0-9]*)>")
_NON_SPEAKER_LABELS = frozenset({"camera", "shot", "style", "title", "music", "sfx", "镜头", "画面", "字幕", "音乐", "音效"})


def _locked_dialogue(prompt: str) -> list[dict[str, str]]:
    """Extract only source text with an explicit speaker plus quoted speech."""
    names = re.findall(r'<Picture [1-9][0-9]*>是([^：:\n，,]{1,20})[：:]', prompt)
    return [{"speaker": _bound_speaker(_speaker_for_match(match), names), "quote": match.group("quote")}
            for match in _EXPLICIT_DIALOGUE.finditer(prompt)
            if _speaker_for_match(match).casefold() not in _NON_SPEAKER_LABELS]


def _bound_speaker(value, names):
    for name in sorted(names, key=len, reverse=True):
        if value == name:
            return name
        if value.startswith(name) and value[len(name):].startswith((
                '画外', '看着', '坚定', '轻声', '低声', '大声', '高声', '平静', '温柔', '哭着', '笑着')):
            return name
    return value


def _speaker_for_match(match: re.Match[str]) -> str:
    speaker = match.group("speaker")
    if match.group("verb") in (":", "："):
        for suffix in ("低声说", "说道", "回答", "说", "问", "喊", "答"):
            if speaker.endswith(suffix) and len(speaker) > len(suffix):
                return speaker[:-len(suffix)]
    return speaker


def _check_dialogue_identity(wire: CanvasBaseWire | CanvasReferenceWire,
                             locks: list[dict[str, str]]) -> None:
    if not locks:
        return
    fields = ((wire.integrated_multimodal_description, wire.overall_soundscape)
              if isinstance(wire, CanvasBaseWire) else
              (wire.detailed_description, wire.overall_soundscape))
    required = Counter((lock["speaker"], lock["quote"]) for lock in locks)
    observed = Counter((_bound_speaker(_speaker_for_match(match), [lock['speaker'] for lock in locks]), match.group("quote"))
                       for field in fields for match in _EXPLICIT_DIALOGUE.finditer(field))
    for (speaker, quote), occurrences in required.items():
        if observed[(speaker, quote)] < occurrences:
            raise ValueError(f"optimized dialogue differs from source: {speaker}")


def _check_reference_identity(wire: CanvasReferenceWire,
                              facts: list[dict[str, object]]) -> None:
    """Validate Picture/Subject identities against actual attached input order."""
    by_subject: dict[str, list[dict[str, object]]] = {}
    for fact in facts:
        by_subject.setdefault(str(fact["subject_tag"]), []).append(fact)
    lines = [line.strip() for line in wire.subject_definitions.splitlines() if line.strip()]
    if len(lines) != len(by_subject):
        raise ValueError("reference subject count differs from input images")
    for line, (subject, subject_facts) in zip(lines, by_subject.items(), strict=True):
        labels = _SUBJECT_TAG.findall(line)
        if not line.startswith(subject) or labels != [subject[9:-1]]:
            raise ValueError("reference subject numbering differs from input images")
        picture_matches = list(_PICTURE_TAG.finditer(line))
        pictures = [match.group() for match in picture_matches]
        expected = [str(fact["picture_tag"]) for fact in subject_facts]
        if pictures != expected:
            raise ValueError("reference picture order or subject mapping differs from input images")
        for picture_index, fact in enumerate(subject_facts):
            label = fact["variant_label"]
            if label:
                start = (picture_matches[picture_index - 1].end()
                         if picture_index else len(subject))
                preceding_picture_span = line[start:picture_matches[picture_index].start()]
                if str(label) not in preceding_picture_span:
                    raise ValueError("reference variant label differs from input image")
    expected_numbers = {int(str(fact["picture_tag"])[9:-1]) for fact in facts}
    all_text = "\n".join((wire.subject_definitions, wire.summary,
                          wire.detailed_description, wire.overall_soundscape,
                          wire.non_diegetic_music))
    if any(int(number) not in expected_numbers for number in _PICTURE_TAG.findall(all_text)):
        raise ValueError("reference output invented a picture")


def _reference_facts(draft: DirectorDraft) -> list[dict[str, object]]:
    seen: set[str] = set()
    subjects: dict[str, int] = {}
    facts: list[dict[str, object]] = []
    for reference in draft.references:
        if reference.image_id in seen:
            continue
        seen.add(reference.image_id)
        subject_key = reference.character_id or f"image:{reference.image_id}"
        subject_number = subjects.setdefault(subject_key, len(subjects) + 1)
        facts.append({
            "picture_tag": f"<Picture {len(facts) + 1}>",
            "subject_tag": f"<Subject {subject_number}>",
            "image_id": reference.image_id,
            "asset_id": reference.asset_id,
            "character_id": reference.character_id,
            "variant_id": reference.variant_id,
            "variant_label": reference.variant_label,
            "asset_kind": reference.asset_kind,
        })
    return facts


async def optimize(
    runtime: StructuredTextRuntime,
    draft: DirectorDraft,
    *,
    frozen_images: Mapping[str, StructuredImage],
    frozen_techniques: Mapping[str, FrozenTechniqueProjection | Mapping[str, object]] | None = None,
    system_prompt: str | None = None,
    reference_limit: int = 5,
) -> OptimizedDirector:
    """Optimize each ordered segment using frozen trusted image bytes.

    Runtime errors and invalid structured output propagate; raw editor prose is
    never treated as a provider prompt.
    """
    validation = validate_generation(draft, reference_limit=reference_limit)
    known_segment_ids = {segment.id for segment in draft.segments}
    selected = frozen_techniques or {}
    unknown_segment_ids = set(selected) - known_segment_ids
    if unknown_segment_ids:
        raise ValueError(f"frozen technique has unknown segment ID: {sorted(unknown_segment_ids)[0]}")
    projections = {
        segment_id: FrozenTechniqueProjection.model_validate(projection).model_dump(mode="json")
        for segment_id, projection in selected.items()
    }
    reference_facts = _reference_facts(draft)
    reference_ids = [str(fact["image_id"]) for fact in reference_facts]
    result: list[OptimizedSegment] = []
    for source_index, (source, aligned) in enumerate(zip(
        draft.segments, validation.timeline, strict=True
    )):
        frame_ids = [image.image_id for image in (source.first_frame, source.last_frame) if image]
        image_ids = reference_ids + frame_ids if validation.route == 'h3_ref' else frame_ids
        try:
            images = [frozen_images[image_id] for image_id in image_ids]
        except KeyError as exc:
            raise ValueError(f"frozen image is missing: {exc.args[0]}") from exc
        validate_structured_images(images)
        mode = 'ref2va' if validation.route == 'h3_ref' else {"i2v": "i2va", "fl2v": "fl2va"}[aligned.mode]
        source_data = {
            "segment_id": source.id,
            "mode": mode,
            "source_prompt": source.prompt,
            "locked_dialogue": _locked_dialogue(source.prompt),
            "neighbor_segments": [
                {"segment_id": neighbor.id, "source_prompt": neighbor.prompt,
                 "requested_duration_seconds": neighbor.duration_seconds}
                for neighbor in draft.segments[max(0, source_index - 1):source_index + 2]
            ],
            "requested_duration_seconds": source.duration_seconds,
            "duration_seconds": aligned.duration_seconds,
            "frames": aligned.frames,
            "aspect_ratio": draft.aspect_ratio,
            "resolution": draft.resolution,
            "references": reference_facts if reference_ids else [],
            "subject_definition_template": "\n".join(
                subject + ": " + "; ".join(
                    f"{fact.get('variant_label') or ''} {fact['picture_tag']}".strip()
                    for fact in reference_facts if fact['subject_tag'] == subject)
                for subject in dict.fromkeys(str(fact['subject_tag']) for fact in reference_facts)
            ),
            "frame_images": [
                {"attachment_index": len(reference_ids) + index + 1,
                 "picture_tag": f"<Picture {index + 1}>" if not reference_ids else None, "image_id": image_id,
                 "anchor": "first" if index == 0 else "last"}
                for index, image_id in enumerate(frame_ids)
            ],
        }
        technique = projections.get(source.id)
        if technique is not None:
            source_data["technique"] = technique
        response_type = (
            _OptimizedTechniqueReferenceResponse if validation.route == "h3_ref"
            else _OptimizedTechniqueBaseResponse
        ) if technique is not None else (
            _OptimizedReferenceResponse if validation.route == "h3_ref"
            else _OptimizedBaseResponse
        )
        prompt = ("Return a structured H3 wire for this one target segment. Copy segment_id exactly. "
                  "Neighbor segments are continuity context, not extra outputs or images. "
                  "Use the images in the exact order listed. Copy locked_dialogue speaker and quote "
                  "into the wire together exactly, including the original quote marks. "
                  "Do not treat other quoted source text as dialogue.\n")
        if technique is not None:
            prompt += (
                "Priority and precedence: source_prompt, attached images and their order/identity, "
                "locked_dialogue, segment_id, mode, aligned duration_seconds, frames, and other "
                "source facts override technique. Use technique only to guide compatible action "
                "beats and pacing, performance, camera movement, and ending composition for this "
                "target segment. Do not copy an external case prompt or invent characters, images, "
                "events, or actions to satisfy the technique. If technique conflicts with those "
                "facts, return wire: null and technique_conflict: a brief reason. "
                "On success, return wire: a structured object and technique_conflict: null. "
                "Both keys are required by the response schema. Never return both a conflict "
                "reason and a non-null wire.\n"
            )
        prompt += "INPUT_JSON:\n" + json.dumps(source_data, ensure_ascii=False)
        final_system_prompt = H3_CANVAS_WRITING_RULES
        if reference_ids and frame_ids:
            final_system_prompt += ('\nThe attachments after the declared reference images are the actual first/last frame anchors, '
                'not extra reference Subjects or Pictures. Keep the supplied reference numbering unchanged. '
                'Describe the initial and final compositions as first frame and last frame without inventing Picture tags. '
                'Only supplied anchors exist; when no last frame is supplied, do not invent a locked ending frame. '
                'Preserve these endpoint compositions, identity and actions. Interior motivated cuts are allowed only when the source directs them.')
        if system_prompt:
            final_system_prompt += "\nAdditional caller context (source facts only):\n" + system_prompt

        # Explicit editor bindings can be compiled without asking AI to regenerate
        # supplier numbering. Legacy drafts without named bindings retain their
        # existing path; never guess a person's identity from an image or avatar.
        bindings = dict(re.findall(r'<Picture ([1-9][0-9]*)>是([^：:\n，,]{1,20})[：:]', source.prompt))
        named_refs = {fact['image_id']: bindings.get(str(index + 1))
                      for index, fact in enumerate(reference_facts)}
        if reference_ids and all(named_refs.values()) and technique is None:
            plan_input = {**source_data, 'identity_bindings': named_refs}

            @model_validator(mode='after')
            def validate_plan(plan):
                compile_reference_plan(plan, reference_facts, named_refs,
                                       source_data['locked_dialogue'], aligned.duration_seconds)
                return plan

            plan_type = create_model('BoundReferencePlan', __base__=ReferencePlan,
                                     __validators__={'validate_plan': validate_plan})
            try:
                plan = await runtime.run_structured(output_type=plan_type, images=images,
                    system_prompt=('你是逐镜导演，只输出请求的导演计划结构，不输出供应商 wire。'
                        'identity_bindings 是冻结身份；subjects 按 references 去重后的 subject 顺序返回，'
                        'subject_id 使用该主体第一张图的 image_id。为每个主体明确 visible、offscreen 或 unused，'
                        '由本镜原文和首帧决定，不能为了参考图齐全让所有角色入镜。'
                        '只设计目标镜头的动作、机位、表演和环境声，不增加人物、剧情、对白。'
                        '已给首帧即是起点；无尾帧不能编造锁定尾帧。邻镜仅供连续性参考。'
                        '所有文本使用人物姓名，不写任何 Subject/Picture/Video/Audio 协议标签。'
                        'locked_dialogue 由程序原样合成，不在 action、subjects、soundscape 重复或改写台词。'
                        'music 无配乐时为 N/A。存在 technique 时遵守其兼容的导演要求，冲突时拒绝改写源事实。'),
                    prompt=json.dumps(plan_input, ensure_ascii=False))
                wire = compile_reference_plan(plan_type.model_validate(plan), reference_facts, named_refs,
                                              source_data['locked_dialogue'], aligned.duration_seconds)
            except Exception as exc:
                exc.director_segment_id = source.id
                raise
            _check_dialogue_identity(wire, source_data['locked_dialogue'])
            _check_reference_identity(wire, reference_facts)
            result.append(OptimizedSegment(segment_id=source.id, mode=mode,
                requested_duration_seconds=source.duration_seconds,
                duration_seconds=aligned.duration_seconds, frames=aligned.frames,
                wire=wire, prompt=compile_h3_wire(wire)))
            continue

        # Validate locked dialogue inside the runtime's bounded schema repair
        # loop, rather than failing the entire multi-shot job after it returns.
        @model_validator(mode='after')
        def validate_dialogue(response):
            if response.wire is not None:
                _check_dialogue_identity(response.wire, source_data['locked_dialogue'])
            return response

        response_type = create_model('DialogueLocked' + response_type.__name__,
            __base__=response_type, __validators__={'validate_dialogue': validate_dialogue})
        response = await runtime.run_structured(
            prompt=prompt, output_type=response_type,
            system_prompt=final_system_prompt, images=images,
        )
        response = response_type.model_validate(response)
        if response.segment_id != source.id:
            raise ValueError(f"optimized segment ID mismatch: {source.id}")
        if technique is not None and response.technique_conflict:
            raise TechniqueConflictError(f"technique conflict: {response.technique_conflict}")
        wire = response.wire
        if wire is None:
            raise ValueError(f"optimized segment wire is missing: {source.id}")
        if wire.mode != mode or (isinstance(wire, CanvasReferenceWire) != (mode == "ref2va")):
            raise ValueError(f"optimized segment mode mismatch: {source.id}")
        if abs(wire.duration_seconds - aligned.duration_seconds) > 1e-9:
            raise ValueError(f"optimized segment duration mismatch: {source.id}")
        _check_dialogue_identity(wire, source_data["locked_dialogue"])
        if isinstance(wire, CanvasReferenceWire):
            _check_reference_identity(wire, reference_facts)
        result.append(OptimizedSegment(
            segment_id=source.id, mode=mode,
            requested_duration_seconds=source.duration_seconds,
            duration_seconds=aligned.duration_seconds, frames=aligned.frames,
            wire=wire, prompt=compile_h3_wire(wire),
        ))
    return OptimizedDirector(
        revision=draft.revision, route=validation.route,
        profile_id=H3_PROMPT_PROFILE_ID, profile_version=H3_PROMPT_PROFILE_VERSION,
        optimized_at=datetime.now(timezone.utc), segments=tuple(result),
    )


__all__ = ["TechniqueConflictError", "optimize"]
