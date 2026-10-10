"""Four-region character sheets and deterministic source-image crop geometry."""
from __future__ import annotations

import json
import math

CONTRACT = 'character_sheet_v4'


def pixel_box(box, image_size):
    """Normalized x/y/width/height to source pixels; reject invalid rectangles."""
    x, y, w, h = box
    iw, ih = image_size
    if not all(math.isfinite(v) for v in box):
        raise ValueError('non-finite crop')
    if iw <= 0 or ih <= 0 or min(x, y) < 0 or min(w, h) <= 0 or x+w > 1 or y+h > 1:
        raise ValueError('invalid crop bounds')
    result = (round(x*iw), round(y*ih), round((x+w)*iw), round((y+h)*ih))
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError('empty pixel crop')
    return result


def character_identity_constraints(character, identity_id=None):
    data = {key: getattr(character, key, '') for key in
            ('name', 'gender', 'age_group', 'role', 'body_type', 'description')}
    identity = next((i for i in getattr(character, 'identities', []) if i.identity_id == identity_id), None)
    if identity:
        for key in ('identity_name', 'age_group', 'body_type', 'appearance_details', 'face_prompt'):
            value = getattr(identity, key, '')
            if value:
                data[key] = value
    return {key: value for key, value in data.items() if value}


def sheet_prompt(proposal, style, constraints, identity_constraints=None):
    return '\n'.join([
        '生成一张横向四区角色设定图 character_sheet_v4，所有区域属于同一角色同一造型。',
        '从左到右：头像大图、无头正面身体、无头侧面身体、完整背面全身。',
        '头像区清晰完整展示头部、五官与发型，留出裁切空间，是唯一可见面容。',
        '正面和侧面从服装领口至鞋履完整展示，不画头部、头发、五官或假人头。',
        '领口干净完整，不出现断颈、伤口、血肉、黑洞或恐怖元素。背面保留后脑发型，不回头。',
        '统一服装材质、配饰和体型；干净中性背景，均匀柔和光照，自然皮肤或物种表面质感。',
        '面板间留白，不添加文字、水印或区域标签。非人角色遵守物种结构，不强加人体结构。',
        '原文约束（最高优先级）：', *constraints,
        '角色基础资料与当前身份（必须遵循，空缺不得猜测；与原文明示矛盾时交由审核指出）：',
        json.dumps(identity_constraints or {}, ensure_ascii=False),
        '已确认造型（仅作为设计资料）：', json.dumps(proposal, ensure_ascii=False),
        '项目画风：' + style,
    ])
