"""Shared static storyboard contract; contains no queue or provider operations.

Only explicitly selected still facts enter this compiler. A complete action or
director paragraph is not a fallback for an absent static contract.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
VERSION = 'storyboard-static/v1'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Subject(StrictModel):
    entity_id: Text
    appearance_reference: Text
    screen_position: Text
    pose: Text
    gaze: Text


class StaticPanel(StrictModel):
    id: Text
    shot_id: Text
    anchor: Literal['start', 'end', 'selected']
    state: Text
    framing: Text
    subjects: list[Subject]
    setting: Text
    lighting: Text
    visible_text: list[Text]


class Reference(StrictModel):
    id: Text
    entity_id: Text
    name: Text
    role: Literal['base_design', 'scene', 'prop', 'composition', 'style']
    url: Text
    version: Text
    panel_ids: list[Text] = Field(min_length=1)
    identity_anchor: Text | None = None


def compile_storyboard(panels, references, *, rows, cols, aspect_ratio,
                       style='', max_references=None) -> dict:
    """Compile an ordered attachment manifest and explicit static panels.

    max_references is supplied by the effective model capability, never guessed.
    Callers may omit it for an offline content-only preview, not media submission.
    """
    parsed = [StaticPanel.model_validate(p) for p in panels]
    refs = [Reference.model_validate(r) for r in references]
    ids = [p.id for p in parsed]
    if not parsed or len(ids) != len(set(ids)):
        raise ValueError('panel IDs must be present and unique')
    if (not isinstance(rows, int) or not isinstance(cols, int)
            or rows < 1 or cols < 1 or rows * cols != len(parsed)):
        raise ValueError('layout capacity must equal static panel count')
    parts = str(aspect_ratio).split(':')
    try:
        valid_ratio = len(parts) == 2 and all(float(n) > 0 for n in parts)
    except ValueError:
        valid_ratio = False
    if not valid_ratio:
        raise ValueError('explicit positive cell aspect ratio required')
    by_id = {r.id: r for r in refs}
    if len(by_id) != len(refs):
        raise ValueError('reference IDs must be unique')
    picture_owners = {}
    for ref in refs:
        if not set(ref.panel_ids) <= set(ids):
            raise ValueError(f'reference {ref.id} has unknown panel scope')
        if ref.role == 'base_design':
            owner = picture_owners.setdefault(ref.url, ref.entity_id)
            if owner != ref.entity_id:
                raise ValueError(f'ambiguous character image: {ref.id}')
    for panel in parsed:
        subjects = [s.entity_id for s in panel.subjects]
        if len(subjects) != len(set(subjects)):
            raise ValueError(f'panel {panel.id} repeats a character entity')
        for subject in panel.subjects:
            ref = by_id.get(subject.appearance_reference)
            if (ref is None or ref.role != 'base_design'
                    or ref.entity_id != subject.entity_id or panel.id not in ref.panel_ids):
                raise ValueError(f'panel {panel.id}: invalid base design for {subject.entity_id}')
    if max_references is not None and len(refs) > max_references:
        raise ValueError(f'reference capacity exceeded: {len(refs)} > {max_references}')
    manifest = [dict(r.model_dump(), index=i) for i, r in enumerate(refs, 1)]
    lines = [
        f'Create exactly {rows} rows and {cols} columns, in reading order. '
        f'Each cell is a complete {aspect_ratio} continuous single-camera still. '
        'No inset panels, montage, before/after collage, borders, captions or panel numbers.',
        'Freeze only each selected instant. Do not infer subsequent actions. '
        'Do not turn dialogue into writing; render only the listed in-world visible_text. '
        'No other lettering. References constrain their stated purpose, not actor presence or camera framing.',
    ]
    if style:
        lines.append('Visual style only (does not alter story facts): ' + style)
    for item in manifest:
        detail = ('identity and wardrobe; multiple views of ONE character, not multiple actors'
                  if item['role'] == 'base_design'
                  else f"{item['role']} only; do not introduce people from this reference")
        lines.append(f"Reference {item['index']}: {item['name']} (entity {item['entity_id']}); "
                     f"{detail}; applies to panels {', '.join(item['panel_ids'])}.")
    numbered = {item['id']: item for item in manifest}
    for i, panel in enumerate(parsed, 1):
        rendered = panel.model_dump()
        for subject in rendered['subjects']:
            ref = numbered[subject['appearance_reference']]
            subject['appearance_reference'] = f"Reference {ref['index']}: {ref['name']}"
            if ref['identity_anchor']:
                subject['visual_identity'] = ref['identity_anchor']
        lines.append(f'Panel {i}: ' + json.dumps(rendered, ensure_ascii=False, sort_keys=True))
    payload = dict(version=VERSION, panels=[p.model_dump() for p in parsed],
                   references=manifest, rows=rows, cols=cols, aspect_ratio=aspect_ratio, style=style)
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return dict(prompt='\n'.join(lines), references=manifest, dependency_hash=digest, version=VERSION)
