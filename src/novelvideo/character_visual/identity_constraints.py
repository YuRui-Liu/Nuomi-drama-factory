"""Adopted casting constraints shared by identity generation and its QC."""
import json
import re
from pathlib import Path

from .models import CharacterVisualBible


def adopted_identity_bible(project_dir, character_name, identity_id=None):
    if not isinstance(project_dir, (str, Path)):
        return None
    path = Path(project_dir) / 'state' / 'character_visual_workspaces.json'
    if not path.is_file():
        return None
    workspace = json.loads(path.read_text(encoding='utf-8')).get(character_name, {})
    value = (workspace.get('identity_visual_bibles', {}).get(identity_id)
        if identity_id else None) or workspace.get('visual_bible')
    if not value or value.get('status') != 'confirmed':
        return None
    return CharacterVisualBible.model_validate(value)


def bible_constraints(bible):
    # Casting anchors can include props from a casting scene. Physical identity
    # comes from dedicated fields; the chosen state owns clothes/accessories.
    return json.dumps({key: getattr(bible, key) for key in
        ('face_shape', 'facial_features', 'hair_style', 'body_type', 'distinctive_features')},
        ensure_ascii=False)


def effective_identity_appearance(project_dir, character, identity):
    """Keep persisted user text untouched; constrain ordinary extracted costumes."""
    appearance = str(identity.appearance_details or '').strip()
    # Generation and QC must see the same explicit identity-level state,
    # including same-age infection variants; a costume alone is insufficient.
    explicit_state = [
        f'{label}：{value}' for label, value in (
            ('身份面部设定', str(getattr(identity, 'face_prompt', '') or '').strip()),
            ('身份体型设定', str(getattr(identity, 'body_type', '') or '').strip()),
        ) if value
    ]
    if explicit_state:
        appearance = '\n'.join([appearance, *explicit_state]).strip()
    # Explicit identity designs and age/face variants remain independent.
    if (getattr(identity, 'source', 'extracted') not in {'extracted', 'identity_planner'}
        or getattr(identity, 'face_prompt', '') or getattr(identity, 'portrait_image', '')
        or (getattr(identity, 'age_group', '') and getattr(character, 'age_group', '')
            and identity.age_group != character.age_group)):
        return appearance
    bible = adopted_identity_bible(project_dir, character.name, identity.identity_id)
    if bible is None:
        return appearance
    # Auto-generated costume clauses must not independently redesign hair.
    if bible.hair_style:
        parts = re.split(r'[，,。；;\n]', appearance)
        appearance = '，'.join(p for p in parts if p.strip() and not re.search(
            r'头发|发型|短发|长发|黑发|白发|银发|金发|束发|束起|马尾|发髻|辫|刘海|\bhair\b|ponytail|bun\b', p, re.I))
    return appearance + '\n已采用定角约束（面部、发型与身体身份优先，服装文字不得覆盖）：\n' + bible_constraints(bible)
