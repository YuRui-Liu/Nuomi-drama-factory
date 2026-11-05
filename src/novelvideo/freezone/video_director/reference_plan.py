"""Compile explicit identity decisions into supplier syntax; never renumber with AI."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import CanvasReferenceWire
from .prepared_validation import h3_dialogue_language, spoken_text


class SubjectState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject_id: str
    presence: Literal['visible', 'offscreen', 'unused']
    action: str = Field(min_length=1)


class ReferencePlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str = Field(min_length=1)
    action: str = Field(min_length=1)
    subjects: list[SubjectState]
    soundscape: str = Field(min_length=1)
    music: str = Field(min_length=1)


def compile_reference_plan(plan, facts, names, dialogue, duration):
    by_subject = {}
    for fact in facts:
        by_subject.setdefault(fact['subject_tag'], []).append(fact)
    expected = [items[0]['image_id'] for items in by_subject.values()]
    if [state.subject_id for state in plan.subjects] != expected:
        raise ValueError('reference plan must preserve subject IDs and order')
    # Protocol tags are owned by this compiler, not generated prose.
    for value in [plan.summary, plan.action, plan.soundscape, plan.music,
                  *(state.action for state in plan.subjects)]:
        if any(tag in value for tag in ('<Subject', '<Picture', '<Video', '<Audio')):
            raise ValueError('reference plan prose must not contain supplier tags')
    definitions, states, retention = [], [], []
    presence = {'visible': '可见', 'offscreen': '画外，不入镜', 'unused': '本镜不用，不入镜也不发声'}
    for (tag, items), state in zip(by_subject.items(), plan.subjects, strict=True):
        definitions.append(tag + ': ' + '; '.join(
            f"{names[f['image_id']]} {f.get('variant_label') or ''} {f['picture_tag']}".replace('  ', ' ')
            for f in items))
        states.append(f'{tag}（{names[state.subject_id]}）：{presence[state.presence]}。{state.action}')
        retention.append(dict(subject=tag, retain=('weak_reference' if state.presence == 'unused'
            else 'fully_preserved') + ' - identity and selected costume; visibility follows the shot state'))
    speaker_ids = {}
    spoken_lines = []
    for line in dialogue:
        speaker_id = speaker_ids.setdefault(line['speaker'], len(speaker_ids) + 1)
        if any('\u3400' <= char <= '\u9fff' for char in line['speaker']):
            cue = f"{line['speaker']}（S{speaker_id}）说："
        else:
            cue = f"{line['speaker']} (S{speaker_id}) says:"
        spoken_lines.append(
            f"{cue}<d>[{h3_dialogue_language(line)}] {spoken_text(line['quote'])}</d>"
        )
    return CanvasReferenceWire(mode='ref2va', duration_seconds=duration,
        subject_definitions='\n'.join(definitions), summary=plan.summary,
        retention_analysis=retention,
        detailed_description=plan.action + '\n逐镜人物状态：\n' + '\n'.join(states)
            + ('\n' + '\n'.join(spoken_lines) if spoken_lines else ''),
        overall_soundscape=plan.soundscape,
        non_diegetic_music=plan.music)
