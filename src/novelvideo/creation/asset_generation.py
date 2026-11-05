"""Draft character/scene designs bound to a real, explicitly executable canvas node.

Preparing a design never calls an image provider and never publishes a shared
asset. The ObjectStore version and request ledger are committed before the
recoverable graph projection, so a canvas CAS conflict preserves the candidate.
"""

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.freezone.orchestration.models import (
    OrchestrationError, frozen_node_result, fingerprint, input_fingerprint,
)
from novelvideo.freezone.orchestration.verification import verify_media
from .asset_reference import AssetReferenceError, AssetReferences, table
from .library_references import digest, packed
from .models import AssetRef, OperationRequest, Scope, StrictModel
from .operations import OperationError, OperationService, create_text_object
from .projection import ProjectionService
from .store import ObjectStore


class AssetDesignProposal(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100000)


class AssetDesignProposalRef(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1, strict=True)
    kind: Literal['story_fragment']


AssetDesignPurpose = Literal['avatar', 'half_body', 'full_body', 'body', 'character_sheet', 'scene']
AssetDesignAspectRatio = Literal[
    '1:1', '9:16', '16:9', '3:4', '4:3', '3:2', '2:3', '4:5', '5:4', '21:9',
]

class AssetDesignCameraSelection(StrictModel):
    cameraBodyId: Literal['panavision_dxl2', 'arri_alexa_65', 'red_vraptor_xl', 'sony_venice_2'] | None = None
    lensId: Literal['arri_signature_prime', 'cooke_s4i', 'zeiss_supreme_prime', 'panavision_primo_70'] | None = None
    focalLengthMm: Literal[8, 14, 24, 35, 50, 75, 125] | None = None
    aperture: Literal['f/1.4', 'f/2', 'f/2.8', 'f/4', 'f/5.6', 'f/8'] | None = None


_DEFAULT_ASSET_DESIGN_PURPOSE = {'character': 'character_sheet', 'scene': 'scene'}
_ASSET_DESIGN_RATIO_BY_PURPOSE = {
    'avatar': '1:1',
    'half_body': '2:3',
    'full_body': '2:3',
    'character_sheet': '16:9',
    'scene': '16:9',
}
_ASSET_DESIGN_CAMERA_BY_PURPOSE = {
    'avatar': {'cameraBodyId': 'arri_alexa_65', 'lensId': 'arri_signature_prime',
               'focalLengthMm': 75, 'aperture': 'f/4'},
    'half_body': {'cameraBodyId': 'arri_alexa_65', 'lensId': 'arri_signature_prime',
                  'focalLengthMm': 50, 'aperture': 'f/4'},
    'full_body': {'cameraBodyId': 'arri_alexa_65', 'lensId': 'arri_signature_prime',
                  'focalLengthMm': 50, 'aperture': 'f/5.6'},
    'character_sheet': {'cameraBodyId': 'arri_alexa_65', 'lensId': 'arri_signature_prime',
                        'focalLengthMm': 50, 'aperture': 'f/8'},
    'scene': {'cameraBodyId': 'arri_alexa_65', 'lensId': 'arri_signature_prime',
              'focalLengthMm': 35, 'aperture': 'f/8'},
}


def _asset_design_usage(kind, purpose):
    resolved_purpose = purpose or _DEFAULT_ASSET_DESIGN_PURPOSE[kind]
    if resolved_purpose == 'body':
        resolved_purpose = 'half_body'
    if kind == 'character' and resolved_purpose not in {
            'avatar', 'half_body', 'full_body', 'character_sheet'}:
        raise ValueError('人物资产用途必须是头像、半身、全身或角色设定表')
    if kind == 'scene' and resolved_purpose != 'scene':
        raise ValueError('场景资产用途必须是场景')
    return resolved_purpose


def _asset_design_camera_selection(purpose, explicit=None):
    defaults = deepcopy(_ASSET_DESIGN_CAMERA_BY_PURPOSE[purpose])
    if explicit is not None:
        defaults.update(explicit.model_dump(exclude_none=True))
    return defaults


def _asset_design_prompt(kind, purpose, name, description, prompt, aspect_ratio=None):
    """Add an immutable, purpose-specific visual contract to new image designs."""
    if kind != 'character' or purpose != 'character_sheet':
        return prompt
    resolved_ratio = aspect_ratio or _ASSET_DESIGN_RATIO_BY_PURPOSE[purpose]
    return (
        f"角色名称：{name.strip()}。人物设定：{description.strip() or '遵循出图描述中的人物身份与外观'}。\n"
        f"出图描述：{prompt.strip()}\n\n"
        "角色设定表必须按以下要求生成，不得退化成单张头像或半身肖像："
        f"生成一张出图比例为{resolved_ratio}的专业角色设定图，使用横排四列、边界清晰的四分区布局。"
        "最左侧区域是足够清晰的脸部特写；右侧三列依次展示正面全身、侧面全身、背面全身。"
        "右侧三个全身视图共用同一基线和人物尺度，都要从头顶到鞋底完整入镜，不裁切头部、手脚或服装。"
        "四个区域必须是同一人物：面部身份、年龄、发型、体型比例、服装和配饰完全一致。"
        "服装和配饰严格沿用当前角色设定及已确认的参考造型，不新增或替换已确认的款式、颜色或配饰；"
        "如果输入没有确认服装，保持角色设定中已经给出的造型，不自行套用统一制服。"
        "使用干净的中性浅色纯背景和均匀柔和照明，清晰呈现脸部和服装结构。"
        "画面内不得出现文字、标题、标注、边框、水印、标志或其他身份的人物。"
    )


def _configured_image_node_model():
    """Pin a design node to the configured GRSAI default without hydrating secrets."""
    from novelvideo import config
    from novelvideo.media_capabilities.models import DEFAULT_GRSAI_IMAGE_MODEL, GRSAI_IMAGE_MODELS

    settings_path = Path(config.STATE_DIR) / 'local' / 'settings.db'
    if not settings_path.is_file():
        return f'grsai/{DEFAULT_GRSAI_IMAGE_MODEL}'
    try:
        with closing(_read_only(settings_path)) as conn:
            row = conn.execute('''SELECT body_json FROM media_provider_accounts
                WHERE id=?''', ('grsai-main',)).fetchone()
        account = json.loads(row['body_json']) if row else None
    except (sqlite3.Error, OSError, json.JSONDecodeError, KeyError, TypeError):
        return f'grsai/{DEFAULT_GRSAI_IMAGE_MODEL}'
    model = account.get('model') if isinstance(account, dict) else None
    if not isinstance(model, str) or model not in GRSAI_IMAGE_MODELS:
        model = DEFAULT_GRSAI_IMAGE_MODEL
    return f'grsai/{model}'


class AssetDesignPrepare(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    canvas_id: str = Field(min_length=1, max_length=64)
    expected_canvas_revision: int = Field(ge=1, strict=True)
    kind: Literal['character', 'scene']
    purpose: AssetDesignPurpose | None = None
    aspect_ratio: AssetDesignAspectRatio | None = None
    camera_selection: AssetDesignCameraSelection | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=12000)
    prompt: str = Field(min_length=1, max_length=12000)
    source_proposal: AssetDesignProposal | None = None
    source_proposal_ref: AssetDesignProposalRef | None = None
    style_template_id: str | None = Field(default=None, max_length=128)
    base_asset_ref: AssetRef | None = None
    references: list[dict] = Field(default_factory=list, max_length=100)
    group_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode='after')
    def nonempty_text(self):
        if not self.name.strip() or not self.prompt.strip():
            raise ValueError('设计名称与提示词不能为空')
        _asset_design_usage(self.kind, self.purpose)
        if (self.source_proposal is None) == (self.source_proposal_ref is None):
            raise ValueError('必须新建提案草稿，或明确引用一个固定提案版本')
        if self.source_proposal and (not self.source_proposal.title.strip() or not self.source_proposal.text.strip()):
            raise ValueError('提案标题与正文不能为空')
        return self


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS asset_design_requests (
        request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, actor_id TEXT NOT NULL,
        request_hash TEXT NOT NULL, canvas_id TEXT NOT NULL, graph_request_id TEXT NOT NULL UNIQUE,
        design_id TEXT NOT NULL, source_proposal_id TEXT NOT NULL, source_proposal_revision INTEGER NOT NULL,
        group_id TEXT NOT NULL,
        text_node_id TEXT NOT NULL, image_node_id TEXT NOT NULL, graph_operation_id TEXT,
        state TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL)''',
    '''CREATE INDEX IF NOT EXISTS asset_design_canvas_idx
        ON asset_design_requests(project_id, canvas_id, state)''',
)


def _read_only(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _group_descendant_ids(nodes, group_id):
    by_id = {node.get('id'): node for node in nodes}
    descendants = {group_id}
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if node.get('parentId') in descendants and node.get('id') not in descendants:
                descendants.add(node['id'])
                changed = True
    return sorted(identifier for identifier in descendants if identifier in by_id)


_ASSET_GROUP_PADDING = {'left': 20, 'top': 34, 'right': 20, 'bottom': 20}
_ASSET_DESIGN_TEXT_SIZE = {'width': 440, 'height': 320}
_ASSET_DESIGN_IMAGE_SIZE = {'width': 580, 'height': 360}
_ASSET_DESIGN_CHILD_GAP = 40
_ASSET_LAYOUT_CLEARANCE = 64
_ASSET_LAYOUT_GRID = 40
_ASSET_LAYOUT_MAX_POSITION = 10_000_000
_CANVAS_NODE_SIZE_FALLBACKS = {
    'groupNode': (220, 140),
    'textAnnotationNode': (440, 320),
    'imageGenNode': (580, 360),
    'uploadNode': (320, 350),
}


def _layout_number(value, *, fallback):
    return value if type(value) in {int, float} and math.isfinite(value) else fallback


def _node_position(node):
    position = node.get('position') if isinstance(node.get('position'), dict) else {}
    return (_layout_number(position.get('x'), fallback=0),
            _layout_number(position.get('y'), fallback=0))


def _node_size(node):
    fallback_width, fallback_height = _CANVAS_NODE_SIZE_FALLBACKS.get(
        node.get('type'), (320, 200))
    style = node.get('style') if isinstance(node.get('style'), dict) else {}
    measured = node.get('measured') if isinstance(node.get('measured'), dict) else {}

    def dimension(name, default):
        for value in (measured.get(name), node.get(name), style.get(name)):
            if type(value) in {int, float} and math.isfinite(value) and value > 0:
                return value
        return default

    return dimension('width', fallback_width), dimension('height', fallback_height)


def _asset_layout_rects(nodes, parent_group_id=None):
    """Return occupied rectangles in canvas or selected-group local coordinates."""
    by_id = {node.get('id'): node for node in nodes if isinstance(node, dict)
             and isinstance(node.get('id'), str)}
    children_by_parent = {}
    for node in by_id.values():
        parent_id = node.get('parentId')
        if isinstance(parent_id, str) and parent_id in by_id:
            children_by_parent.setdefault(parent_id, []).append(node)

    def bounds(node_id, active=frozenset()):
        node = by_id[node_id]
        width, height = _node_size(node)
        rect = [0.0, 0.0, width, height]
        if node.get('type') != 'groupNode' or node_id in active:
            return tuple(rect)
        next_active = active | {node_id}
        for child in sorted(children_by_parent.get(node_id, ()), key=lambda item: item['id']):
            if child['id'] in next_active:
                continue
            child_x, child_y = _node_position(child)
            child_left, child_top, child_right, child_bottom = bounds(child['id'], next_active)
            rect[0] = min(rect[0], child_x + child_left)
            rect[1] = min(rect[1], child_y + child_top)
            rect[2] = max(rect[2], child_x + child_right + _ASSET_GROUP_PADDING['right'])
            rect[3] = max(rect[3], child_y + child_bottom + _ASSET_GROUP_PADDING['bottom'])
        return tuple(rect)

    if parent_group_id is not None:
        siblings = children_by_parent.get(parent_group_id, ())
        # Legacy groups may have children at (0, 0) or partly overlapping. Keep
        # their persisted local rectangles as occupied space and place the new
        # group around them. fitGroupToChildren translates the parent by -shift
        # and every direct child by +shift, preserving world positions and all
        # pairwise spacing while restoring the padding.
    else:
        siblings = [node for node in by_id.values()
                    if not node.get('parentId') or node.get('parentId') not in by_id]

    rects = []
    for node in sorted(siblings, key=lambda item: item['id']):
        x, y = _node_position(node)
        left, top, right, bottom = bounds(node['id'])
        rects.append((x + left, y + top, x + right, y + bottom))
    return rects


def _asset_layout_position(rects, width, height, *, start_x, start_y):
    def align_after(value, origin):
        steps = max(0, math.ceil((value - origin) / _ASSET_LAYOUT_GRID))
        return int(origin + steps * _ASSET_LAYOUT_GRID)

    x_candidates = {int(start_x)}
    y_candidates = {int(start_y)}
    for left, top, right, bottom in rects:
        x_candidates.add(align_after(right + _ASSET_LAYOUT_CLEARANCE, start_x))
        y_candidates.add(align_after(bottom + _ASSET_LAYOUT_CLEARANCE, start_y))

    candidates = sorted(
        ((abs(x - start_x) + abs(y - start_y), y, x)
         for y in y_candidates for x in x_candidates),
    )
    for _, y, x in candidates:
        right, bottom = x + width, y + height
        if all(right + _ASSET_LAYOUT_CLEARANCE <= left
               or occupied_right + _ASSET_LAYOUT_CLEARANCE <= x
               or bottom + _ASSET_LAYOUT_CLEARANCE <= top
               or occupied_bottom + _ASSET_LAYOUT_CLEARANCE <= y
               for left, top, occupied_right, occupied_bottom in rects):
            if x > _ASSET_LAYOUT_MAX_POSITION or y > _ASSET_LAYOUT_MAX_POSITION:
                continue
            return {'x': x, 'y': y}
    raise OperationError('layout_conflict', '画布没有可用的资产设计位置，请先整理画布')


def _asset_design_layout(nodes, parent_group_id=None):
    text_width, text_height = (_ASSET_DESIGN_TEXT_SIZE['width'],
                               _ASSET_DESIGN_TEXT_SIZE['height'])
    image_width, image_height = (_ASSET_DESIGN_IMAGE_SIZE['width'],
                                 _ASSET_DESIGN_IMAGE_SIZE['height'])
    text_position = {'x': _ASSET_GROUP_PADDING['left'], 'y': _ASSET_GROUP_PADDING['top']}
    image_position = {
        'x': text_position['x'] + text_width + _ASSET_DESIGN_CHILD_GAP,
        'y': text_position['y'],
    }
    group_width = image_position['x'] + image_width + _ASSET_GROUP_PADDING['right']
    group_height = max(text_height, image_height) + text_position['y'] + _ASSET_GROUP_PADDING['bottom']
    rects = _asset_layout_rects(nodes, parent_group_id)
    group_position = _asset_layout_position(
        rects, group_width, group_height,
        start_x=text_position['x'] if parent_group_id else 0,
        start_y=text_position['y'] if parent_group_id else 0,
    )
    return {
        'group': {'position': group_position, 'width': group_width, 'height': group_height},
        'text': {'position': text_position, **_ASSET_DESIGN_TEXT_SIZE},
        'image': {'position': image_position, **_ASSET_DESIGN_IMAGE_SIZE},
    }


class AssetGeneration:
    """Server-owned design objects and graph projection orchestration."""

    def __init__(self, ctx):
        if not ctx.project_id or not ctx.requester_user_id:
            raise OperationError('scope_violation', '缺少授权项目或操作者')
        self.ctx = ctx
        self.state_dir = Path(ctx.state_dir)
        self.output_dir = Path(ctx.output_dir)
        self.project_id = ctx.project_id
        self.actor_id = ctx.requester_user_id
        self.objects = ObjectStore(self.state_dir)
        self.db_path = self.objects.db_path
        self.asset_references = AssetReferences(self.state_dir, self.output_dir, self.project_id)

    def _ensure_schema(self):
        with self.objects.transaction() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    def _workspace(self, conn, canvas_id):
        row = conn.execute('''SELECT w.*, o.state AS operation_state FROM canvas_workspaces w
            JOIN operations o ON o.id=w.operation_id WHERE w.canvas_id=?''', (canvas_id,)).fetchone()
        if row is None or row['kind'] != 'concept' or row['operation_state'] != 'applied':
            raise OperationError('scope_violation', '资产设计只允许写入已登记的概念画布', canvas_id=canvas_id)
        canvas = read_canvas(self.state_dir, canvas_id)
        if not canvas or canvas.get('project_id') != self.project_id:
            raise OperationError('scope_violation', '目标画布不属于当前项目', canvas_id=canvas_id)
        return row, canvas

    def _normalize_references(self, conn, canvas_id, references):
        if not isinstance(references, list) or len(references) > 100:
            raise OperationError('invalid_contract', '参考输入数量无效')
        canvas = read_canvas(self.state_dir, canvas_id)
        canvas_nodes = {node['id']: node for node in (canvas or {}).get('nodes', [])}
        normalized = []
        fixed_image_references = 0
        for value in references:
            if not isinstance(value, dict):
                raise OperationError('invalid_contract', '参考输入格式无效')
            kind = value.get('kind')
            if kind == 'canvas_node':
                allowed = {'kind', 'node_id', 'canvas_id'}
                if set(value) - allowed or not isinstance(value.get('node_id'), str):
                    raise OperationError('invalid_contract', '画布参考字段无效')
                source_canvas = value.get('canvas_id', canvas_id)
                if source_canvas != canvas_id or value['node_id'] not in canvas_nodes:
                    raise OperationError('scope_violation', '参考节点必须属于当前冻结画布')
                node = canvas_nodes[value['node_id']]
                frozen_fingerprint = self._canvas_reference_fingerprint(
                    self.ctx, self.project_id, canvas_id, node)
                normalized.append({'kind': 'canvas_node', 'canvas_id': canvas_id,
                                   'node_id': node['id'], 'node_type': node['type'],
                                   'input_fingerprint': frozen_fingerprint})
            elif kind == 'asset':
                allowed = {'kind', 'asset_ref', 'slot_id', 'role'}
                if set(value) - allowed or not isinstance(value.get('asset_ref'), dict):
                    raise OperationError('invalid_contract', '固定资产参考字段无效')
                try:
                    ref = AssetRef.model_validate(value['asset_ref'])
                    obj, mapping = AssetReferences.fixed(conn, ref)
                except (AssetReferenceError, ValueError, TypeError) as exc:
                    raise OperationError('scope_violation', '固定资产版本不属于当前项目或不存在') from exc
                item = {'kind': 'asset', 'asset_ref': ref.model_dump(),
                        'object_kind': obj['kind'], 'object_id': obj['id'], 'revision': obj['revision'],
                        'mapping': {key: mapping[key] for key in ('object_id', 'kind', 'source_name', 'parent_id')}}
                slot_id, role = value.get('slot_id'), value.get('role')
                if (slot_id is None) != (role is None):
                    raise OperationError('invalid_contract', '固定资产参考必须同时指定素材槽和角色')
                if slot_id is None:
                    raise OperationError('invalid_contract', '固定资产参考必须指定可用于出图的图片素材槽')
                if not isinstance(slot_id, str) or not isinstance(role, str):
                    raise OperationError('invalid_contract', '固定资产素材选择无效')
                media = next((media for media in obj['payload'].get('_media', [])
                              if media.get('slot_id') == slot_id
                              and self.asset_references.role(media) == role), None)
                if media is None:
                    raise OperationError('scope_violation', '固定资产版本中没有所选素材')
                # Validate the exact immutable image slot now. The image node
                # supports one direct AssetRef URL; more than one must not be
                # silently truncated by the provider adapter.
                self.asset_references.image_url(ref, {'slot_id': slot_id, 'role': role})
                item.update(slot_id=slot_id, role=role)
                fixed_image_references += 1
                normalized.append(item)
            else:
                raise OperationError('invalid_contract', '仅支持当前画布节点或固定资产版本作为参考')
        if len({packed(item) for item in normalized}) != len(normalized):
            raise OperationError('invalid_contract', '参考输入不能重复')
        if fixed_image_references > 1:
            raise OperationError('invalid_contract', '当前出图节点一次只能使用一个固定资产图片参考')
        return normalized

    @staticmethod
    def _canvas_reference_fingerprint(ctx, project_id, canvas_id, node):
        """Freeze a same-canvas node's verified image output, not just its inputs."""
        result = frozen_node_result(node)
        image_url = result.get('imageUrl') if isinstance(result, dict) else None
        if not isinstance(image_url, str) or not image_url.strip():
            raise OperationError('dependency_input_unavailable', '画布参考节点没有已保存的图片输出',
                                 node_id=node.get('id'))
        try:
            evidence = verify_media(Path(ctx.output_dir), project_id, image_url, 'image')
        except OrchestrationError as exc:
            code = 'scope_violation' if exc.code == 'foreign_media' else 'dependency_input_unavailable'
            raise OperationError(code, '画布参考必须是当前项目中可验证的本地图片',
                                 node_id=node.get('id')) from exc
        return fingerprint({
            'canvas_id': canvas_id,
            'node_id': node.get('id'),
            'node_type': node.get('type'),
            'input_fingerprint': input_fingerprint(node),
            'image_url': evidence['url'],
            'image_sha256': evidence['sha256'],
            'image_bytes': evidence['bytes'],
        })

    @staticmethod
    def _reference_edge_id(project_id, request_id, source_id, target_id):
        return 'asset-design-reference-' + digest([project_id, request_id, source_id, target_id])[:32]

    def _canvas_reference_edges(self, references, canvas, image_node_id, request_id):
        canvas_id = canvas.get('canvas_id')
        nodes = {node.get('id'): node for node in canvas.get('nodes', [])}
        edges = []
        for reference in references:
            if reference.get('kind') != 'canvas_node':
                continue
            source_id = reference['node_id']
            source = nodes.get(source_id)
            if (reference.get('canvas_id') != canvas_id or source is None
                    or source.get('type') != reference.get('node_type')):
                raise OperationError('scope_violation', '画布参考节点已离开冻结范围', node_id=source_id)
            current_fingerprint = self._canvas_reference_fingerprint(
                self.ctx, self.project_id, canvas_id, source)
            if current_fingerprint != reference.get('input_fingerprint'):
                raise OperationError('stale_input', '画布参考的图片版本已变化，请重新准备设计',
                                     node_id=source_id)
            edges.append({'id': self._reference_edge_id(
                              self.project_id, request_id, source_id, image_node_id),
                          'source': source_id, 'target': image_node_id})
        return edges

    def _base_snapshot(self, conn, kind, ref):
        if ref is None:
            return None
        try:
            obj, mapping = AssetReferences.fixed(conn, ref)
        except (AssetReferenceError, ValueError, TypeError) as exc:
            raise OperationError('scope_violation', '基础资产必须是当前项目的精确固定版本') from exc
        if obj['kind'] != kind or mapping['kind'] != kind:
            raise OperationError('invalid_contract', '基础资产类型必须与设计类型一致')
        return {'asset_ref': ref.model_dump(), 'kind': obj['kind'], 'revision': obj['revision'],
                'payload_fingerprint': digest(obj['payload'])}

    def _existing(self, request, request_hash):
        if not self.db_path.is_file():
            return None
        with closing(_read_only(self.db_path)) as conn:
            if not table(conn, 'asset_design_requests'):
                return None
            row = conn.execute('SELECT * FROM asset_design_requests WHERE request_id=?',
                               (request.request_id,)).fetchone()
            if row is None:
                return None
            if row['project_id'] != self.project_id or row['actor_id'] != self.actor_id or row['request_hash'] != request_hash:
                raise OperationError('idempotency_conflict', '请求编号已用于不同设计内容或操作者')
            return dict(row)

    def _create_candidate(self, request, request_hash, *, group_id, expected_canvas_revision,
                          source_proposal_provenance=None):
        now = datetime.now(timezone.utc).isoformat()
        with self.objects.transaction() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)
            existing = conn.execute('SELECT * FROM asset_design_requests WHERE request_id=?',
                                    (request.request_id,)).fetchone()
            if existing is not None:
                if (existing['project_id'], existing['actor_id'], existing['request_hash']) != (
                        self.project_id, self.actor_id, request_hash):
                    raise OperationError('idempotency_conflict', '请求编号已用于不同设计内容或操作者')
                return dict(existing)
            workspace, canvas = self._workspace(conn, request.canvas_id)
            if expected_canvas_revision != canvas['revision']:
                # Preserve the semantic candidate but never write over a newer
                # human canvas edit. Caller records the conflict after creation.
                initial_state = 'candidate_conflict'
            else:
                initial_state = 'candidate'
            if group_id:
                parent = next((node for node in canvas['nodes'] if node.get('id') == group_id), None)
                if parent is None or parent.get('type') != 'groupNode':
                    raise OperationError('scope_violation', '选中的内容组已不存在或不是分组')
                prior_descendant_ids = _group_descendant_ids(canvas['nodes'], group_id)
            else:
                prior_descendant_ids = []
            canvas_layout = _asset_design_layout(canvas['nodes'], group_id)
            base_snapshot = self._base_snapshot(conn, request.kind, request.base_asset_ref)
            references = self._normalize_references(conn, request.canvas_id, request.references)
            if request.source_proposal is not None:
                proposal_ref = create_text_object(conn, self.actor_id, kind='story_fragment',
                    data={'title': request.source_proposal.title, 'text': request.source_proposal.text},
                    canvas_id=request.canvas_id,
                    episode_id=workspace['episode_id'])
                proposal_origin = {'kind': 'operation_created'}
            else:
                proposal_ref = {'id': request.source_proposal_ref.id,
                                'revision': request.source_proposal_ref.revision,
                                'kind': request.source_proposal_ref.kind}
                proposal = ObjectStore._read(conn, proposal_ref['id'], proposal_ref['revision'])
                if (proposal is None or proposal['kind'] != 'story_fragment'
                        or proposal['payload'].get('canvas_id') != request.canvas_id):
                    raise OperationError('scope_violation', '引用提案不是当前画布可访问的固定故事提案')
                proposal_origin = {'kind': 'fixed_input',
                                   'payload_fingerprint': digest(proposal['payload'])}
                if source_proposal_provenance is not None:
                    proposal_origin['provenance'] = deepcopy(source_proposal_provenance)
            design_id, group_node_id, text_node_id, image_node_id = (uuid4().hex for _ in range(4))
            purpose = _asset_design_usage(request.kind, request.purpose)
            aspect_ratio = request.aspect_ratio or _ASSET_DESIGN_RATIO_BY_PURPOSE[purpose]
            camera_selection = _asset_design_camera_selection(purpose, request.camera_selection)
            generation_prompt = _asset_design_prompt(
                request.kind, purpose, request.name, request.description, request.prompt, aspect_ratio)
            image_model = _configured_image_node_model()
            graph_request_id = 'asset-design-graph:' + digest([self.project_id, self.actor_id, request.request_id])[:48]
            data = {'project_id': self.project_id, 'canvas_id': request.canvas_id,
                    'design_status': 'draft', 'kind': request.kind, 'name': request.name,
                    'purpose': purpose, 'aspect_ratio': aspect_ratio, 'image_model': image_model,
                    'camera_selection': camera_selection,
                    'description': request.description, 'prompt': generation_prompt,
                    'style_template_id': request.style_template_id,
                    'base_asset_ref': request.base_asset_ref.model_dump() if request.base_asset_ref else None,
                    'base_asset_snapshot': base_snapshot, 'references': references,
                    'source_proposal_ref': proposal_ref,
                    'source_proposal_origin': proposal_origin,
                    'canvas_group_id': group_node_id, 'parent_group_id': group_id,
                    'canvas_layout': canvas_layout,
                    'prior_descendant_ids': prior_descendant_ids,
                    'text_node_id': text_node_id, 'image_node_id': image_node_id,
                    'request_id': request.request_id, 'graph_request_id': graph_request_id,
                    'expected_canvas_revision': expected_canvas_revision}
            conn.execute('INSERT INTO objects (id,kind,base_asset_id,variant_id,current_revision) VALUES (?,?,NULL,NULL,1)',
                         (design_id, request.kind))
            conn.execute('INSERT INTO object_versions VALUES (?,1,?,?,?)',
                         (design_id, packed(data), self.actor_id, now))
            conn.execute('INSERT INTO relations VALUES (?,?,?,?,?)',
                         (uuid4().hex, design_id, proposal_ref['id'], 'asset_design_source_proposal',
                          packed({'revision': proposal_ref['revision'], 'request_id': request.request_id})))
            result = {'request_id': request.request_id, 'operation_id': None,
                      'design_ref': {'id': design_id, 'revision': 1, 'kind': request.kind},
                      'source_proposal_ref': {**proposal_ref, 'kind': 'story_fragment'},
                      'group_id': group_node_id, 'text_node_id': text_node_id,
                      'image_node_id': image_node_id, 'canvas_revision': canvas['revision'],
                      'projection_status': 'conflict' if initial_state == 'candidate_conflict' else 'pending',
                      'prepared_execution': None}
            conn.execute('''INSERT INTO asset_design_requests
                (request_id,project_id,actor_id,request_hash,canvas_id,graph_request_id,design_id,
                 source_proposal_id,source_proposal_revision,group_id,text_node_id,image_node_id,state,result,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (request.request_id, self.project_id, self.actor_id, request_hash, request.canvas_id,
                 graph_request_id, design_id, proposal_ref['id'], proposal_ref['revision'], group_node_id, text_node_id,
                 image_node_id, initial_state, packed(result), now))
            return dict(conn.execute('SELECT * FROM asset_design_requests WHERE request_id=?',
                                     (request.request_id,)).fetchone())

    def _nodes(self, request, row, *, selected_group_id, references, proposal_payload,
               canvas_layout, image_model=None, camera_selection=None, prompt=None,
               purpose=None, aspect_ratio=None):
        object_id = row['design_id']
        ref = {'id': object_id, 'revision': 1, 'kind': request.kind}
        purpose = purpose or _asset_design_usage(request.kind, request.purpose)
        aspect_ratio = aspect_ratio or request.aspect_ratio or _ASSET_DESIGN_RATIO_BY_PURPOSE[purpose]
        image_model = image_model or _configured_image_node_model()
        prompt = prompt or _asset_design_prompt(
            request.kind, purpose, request.name, request.description, request.prompt, aspect_ratio)
        binding = {'design_ref': ref, 'kind': request.kind, 'name': request.name,
                   'description': request.description, 'prompt': prompt,
                   'style_template_id': request.style_template_id,
                   'base_asset_ref': request.base_asset_ref.model_dump() if request.base_asset_ref else None,
                   'references': references,
                   'source_proposal_ref': {'id': row['source_proposal_id'],
                                           'revision': row['source_proposal_revision'],
                                           'kind': 'story_fragment'},
                   'target_canvas_id': request.canvas_id,
                   'parent_group_id': selected_group_id,
                   'request_id': request.request_id,
                   'operation_request_id': row['graph_request_id']}
        # Keep generator inputs explicit and provider-facing, while the nested
        # binding remains the immutable design identity used by provenance.
        reference_url = None
        for item in references:
            if item.get('kind') != 'asset' or not item.get('slot_id'):
                continue
            url = self.asset_references.image_url(item['asset_ref'],
                {'slot_id': item['slot_id'], 'role': item['role']})
            if url:
                reference_url = reference_url or url
        group_layout = canvas_layout['group']
        text_layout = canvas_layout['text']
        image_layout = canvas_layout['image']
        group = {'id': row['group_id'], 'type': 'groupNode',
                 'data': {'displayName': request.name, 'description': request.description},
                 'position': deepcopy(group_layout['position']),
                 'width': group_layout['width'], 'height': group_layout['height'],
                 'style': {'width': group_layout['width'], 'height': group_layout['height']}}
        if selected_group_id:
            group['parentId'] = selected_group_id
        text_node = {'id': row['text_node_id'], 'type': 'textAnnotationNode',
                     'data': {'title': proposal_payload.get('title') or request.name,
                              'content': proposal_payload['text'],
                              'description': request.description},
                     'position': deepcopy(text_layout['position']),
                     'width': text_layout['width'], 'height': text_layout['height'],
                     'parentId': row['group_id']}
        image_data = {'prompt': prompt, 'operation': 'generate',
                      'model': image_model,
                      'size': '1K',
                      'aspectRatio': aspect_ratio,
                      'designBinding': binding}
        if camera_selection is not None:
            image_data['cameraSelection'] = deepcopy(camera_selection)
        if request.style_template_id:
            image_data['styleTemplateId'] = request.style_template_id
        if reference_url:
            image_data['referenceImageUrl'] = reference_url
        image_node = {'id': row['image_node_id'], 'type': 'imageGenNode', 'data': image_data,
                      'position': deepcopy(image_layout['position']),
                      'width': image_layout['width'], 'height': image_layout['height'],
                      'parentId': row['group_id']}
        return [group, text_node, image_node]

    def _save_result(self, request_id, graph_operation_id, state, result):
        with self.objects.transaction() as conn:
            conn.execute('UPDATE asset_design_requests SET graph_operation_id=?,state=?,result=? WHERE request_id=?',
                         (graph_operation_id, state, packed(result), request_id))

    def _graph_operation(self, graph_request_id):
        with closing(_read_only(self.db_path)) as conn:
            row = conn.execute('''SELECT o.id,o.state,p.patch FROM operations o
                LEFT JOIN projection_outbox p ON p.operation_id=o.id WHERE o.request_id=?''',
                (graph_request_id,)).fetchone()
            return dict(row) if row else None

    def _finalize_projection_conflict(self, operation, result, canvas_revision):
        if operation.get('state') == 'pending_projection' and operation.get('patch'):
            projected = ProjectionService(self.objects)._conflict(
                operation['id'], json.loads(operation['patch']),
                'Canvas revision changed before asset design projection')
            result['candidate_id'] = projected.candidate_id
        result.update(operation_id=operation['id'], projection_status='conflict',
                      canvas_revision=canvas_revision, prepared_execution=None)
        return result

    def prepare(self, actor_id, body, *, selected_group_id=None, source_proposal_provenance=None):
        """Create the draft object and project its three-node content group."""
        if actor_id != self.actor_id:
            raise OperationError('scope_violation', '设计操作者与项目身份不匹配')
        request = body if isinstance(body, AssetDesignPrepare) else AssetDesignPrepare.model_validate(body)
        if selected_group_id is not None:
            if request.group_id not in (None, selected_group_id):
                raise OperationError('scope_violation', '分组必须来自服务端冻结范围')
            selected_group_id = selected_group_id
        else:
            selected_group_id = request.group_id
        hashed_request = request.model_dump(mode='json')
        hashed_request['group_id'] = selected_group_id
        request_hash = digest([self.project_id, actor_id, hashed_request, source_proposal_provenance])
        row = self._existing(request, request_hash)
        if row is None:
            row = self._create_candidate(request, request_hash, group_id=selected_group_id,
                                         expected_canvas_revision=request.expected_canvas_revision,
                                         source_proposal_provenance=source_proposal_provenance)
        result = json.loads(row['result'])
        if row['state'] == 'candidate_conflict':
            canvas = read_canvas(self.state_dir, request.canvas_id)
            result.update(canvas_revision=(canvas or {}).get('revision'), projection_status='conflict',
                          prepared_execution=None)
            self._save_result(request.request_id, row['graph_operation_id'], row['state'], result)
            return result

        # An applied or terminal conflict receipt is immutable on replay.
        if row['state'] in {'applied', 'conflict'}:
            return result

        with closing(_read_only(self.db_path)) as conn:
            design = ObjectStore._read(conn, row['design_id'], 1)
            if design is None or design['payload'].get('project_id') != self.project_id:
                raise OperationError('scope_violation', '草案对象证据缺失')
            design_payload = design['payload']
            references = design_payload['references']
            group_id = design_payload.get('parent_group_id')
            proposal = ObjectStore._read(conn, row['source_proposal_id'], row['source_proposal_revision'])
            if proposal is None or proposal['kind'] != 'story_fragment':
                raise OperationError('scope_violation', '来源提案固定版本缺失')
            proposal_payload = proposal['payload']
        current_canvas = read_canvas(self.state_dir, request.canvas_id)
        if not current_canvas or current_canvas.get('project_id') != self.project_id:
            raise OperationError('scope_violation', '目标画布不属于当前项目')
        canvas_layout = design_payload.get('canvas_layout')
        nodes = None
        if not isinstance(canvas_layout, dict):
            # An older request may already have a durable operation patch but no
            # layout evidence in its design object. Reuse that patch verbatim so
            # a safe retry does not change its idempotency payload.
            existing_graph = self._graph_operation(row['graph_request_id'])
            if existing_graph and existing_graph.get('patch'):
                try:
                    stored_operations = json.loads(existing_graph['patch']).get('operations', [])
                    stored_nodes = {
                        item['node'].get('id'): item['node']
                        for item in stored_operations
                        if isinstance(item, dict) and item.get('op') == 'add_node'
                        and isinstance(item.get('node'), dict)
                    }
                    expected_ids = {row['group_id'], row['text_node_id'], row['image_node_id']}
                    expected_types = {
                        row['group_id']: 'groupNode',
                        row['text_node_id']: 'textAnnotationNode',
                        row['image_node_id']: 'imageGenNode',
                    }
                    if (set(stored_nodes) == expected_ids
                            and all(stored_nodes[node_id].get('type') == node_type
                                    for node_id, node_type in expected_types.items())):
                        nodes = [stored_nodes[row['group_id']], stored_nodes[row['text_node_id']],
                                 stored_nodes[row['image_node_id']]]
                except (TypeError, ValueError, AttributeError):
                    nodes = None
            if nodes is None:
                # Recover older, not-yet-projected candidates without changing
                # their immutable design payload. New candidates persist layout.
                canvas_layout = _asset_design_layout(current_canvas.get('nodes', []), group_id)
        if nodes is None:
            frozen_purpose = design_payload.get('purpose')
            if frozen_purpose is None:
                # Candidates created before purpose was persisted used half_body
                # as the character default. Keep their retry payload stable.
                requested_purpose = request.purpose
                if requested_purpose is None and request.kind == 'character':
                    requested_purpose = 'half_body'
                frozen_purpose = _asset_design_usage(request.kind, requested_purpose)
            else:
                frozen_purpose = _asset_design_usage(request.kind, frozen_purpose)
            frozen_aspect_ratio = design_payload.get('aspect_ratio')
            if frozen_aspect_ratio is None:
                frozen_aspect_ratio = (request.aspect_ratio
                    or _ASSET_DESIGN_RATIO_BY_PURPOSE[frozen_purpose])
            nodes = self._nodes(request, row, selected_group_id=group_id, references=references,
                                proposal_payload=proposal_payload, canvas_layout=canvas_layout,
                                image_model=design_payload.get('image_model'),
                                camera_selection=design_payload.get('camera_selection'),
                                prompt=design_payload.get('prompt'),
                                purpose=frozen_purpose, aspect_ratio=frozen_aspect_ratio)
        if current_canvas['revision'] != request.expected_canvas_revision:
            existing_operation = self._graph_operation(row['graph_request_id'])
            if existing_operation and existing_operation['state'] == 'applied':
                projected = ProjectionService(self.objects).apply(existing_operation['id'])
                result.update(operation_id=existing_operation['id'],
                              canvas_revision=projected.canvas_revision or current_canvas['revision'],
                              projection_status=projected.status, prepared_execution=None)
                if projected.status == 'applied':
                    result['prepared_execution'] = self._candidate(result, request.canvas_id, row['graph_request_id'])
                state = 'applied' if projected.status == 'applied' else 'conflict'
                self._save_result(request.request_id, existing_operation['id'], state, result)
                return result
            if existing_operation and existing_operation['state'] == 'pending_projection':
                marker = ((current_canvas.get('metadata') or {}).get('creation_projection_receipts') or {}).get(
                    existing_operation['id'])
                if marker:
                    projected = ProjectionService(self.objects).apply(existing_operation['id'])
                    result.update(operation_id=existing_operation['id'],
                                  canvas_revision=projected.canvas_revision or current_canvas['revision'],
                                  projection_status=projected.status, prepared_execution=None)
                    if projected.status == 'applied':
                        result['prepared_execution'] = self._candidate(
                            result, request.canvas_id, row['graph_request_id'])
                    state = 'applied' if projected.status == 'applied' else 'conflict'
                    self._save_result(request.request_id, existing_operation['id'], state, result)
                    return result
                self._finalize_projection_conflict(existing_operation, result, current_canvas['revision'])
                self._save_result(request.request_id, existing_operation['id'], 'conflict', result)
                return result
            result.update(canvas_revision=current_canvas['revision'], projection_status='conflict',
                          prepared_execution=None)
            self._save_result(request.request_id, row['graph_operation_id'], 'conflict', result)
            return result
        reference_edges = self._canvas_reference_edges(
            references, current_canvas, row['image_node_id'], row['graph_request_id'])
        graph_operations = [{'op': 'add_node', 'node': node} for node in nodes]
        graph_operations.extend({'op': 'add_edge', 'edge': edge} for edge in reference_edges)
        scoped_node_ids = list(dict.fromkeys(
            [node['id'] for node in nodes] + [edge['source'] for edge in reference_edges]))
        try:
            operation = OperationService(self.objects, project_id=self.project_id,
                output_dir=self.output_dir,
                authorize=lambda candidate, target: candidate == actor_id and target == self.project_id).apply(
                    actor_id, OperationRequest(request_id=row['graph_request_id'], kind='graph.patch',
                        scope=Scope(project_id=self.project_id, episode_id=None, canvas_id=request.canvas_id,
                                    node_ids=scoped_node_ids),
                        expected_revisions={request.canvas_id: request.expected_canvas_revision},
                        payload={'operations': graph_operations}))
        except OperationError as exc:
            if exc.code == 'revision_conflict':
                canvas = read_canvas(self.state_dir, request.canvas_id)
                result.update(canvas_revision=(canvas or {}).get('revision'), projection_status='conflict',
                              prepared_execution=None)
                self._save_result(request.request_id, None, 'conflict', result)
                return result
            raise
        operation_id = operation.operation_id
        self._save_result(request.request_id, operation_id, 'pending', result)
        projected = ProjectionService(self.objects).apply(operation_id)
        result['operation_id'] = operation_id
        result['canvas_revision'] = projected.canvas_revision or request.expected_canvas_revision
        result['projection_status'] = projected.status
        result['prepared_execution'] = None
        state = 'applied' if projected.status == 'applied' else 'conflict' if projected.status == 'conflict' else 'pending'
        if state == 'applied':
            result['prepared_execution'] = self._candidate(result, request.canvas_id, row['graph_request_id'])
        self._save_result(request.request_id, operation_id, state, result)
        return result

    @staticmethod
    def _candidate(result, canvas_id, graph_request_id):
        return {'canvas_id': canvas_id, 'node_id': result['image_node_id'],
                'node_type': 'imageGenNode', 'operation': 'generate', 'max_node_runs': 1,
                'source_operation_id': result['operation_id'], 'request_id': graph_request_id}

    def get_design(self, design_id, revision=None):
        if not self.db_path.is_file():
            raise OperationError('asset_design_not_found', '设计草案不存在')
        with closing(_read_only(self.db_path)) as conn:
            if not table(conn, 'asset_design_requests'):
                raise OperationError('asset_design_not_found', '设计草案不存在')
            row = conn.execute('''SELECT * FROM asset_design_requests WHERE design_id=? AND project_id=?''',
                               (design_id, self.project_id)).fetchone()
            if row is None:
                raise OperationError('asset_design_not_found', '设计草案不存在')
            actual_revision = 1 if revision is None else revision
            obj = ObjectStore._read(conn, design_id, actual_revision)
            if obj is None or obj['kind'] not in {'character', 'scene'}:
                raise OperationError('asset_design_not_found', '设计版本不存在')
            return {'design_ref': {'id': design_id, 'revision': obj['revision'], 'kind': obj['kind']},
                    'design': obj['payload'], 'request': json.loads(row['result'])}

    def status(self, request_id):
        if not self.db_path.is_file():
            raise OperationError('asset_design_request_not_found', '设计请求不存在')
        with closing(_read_only(self.db_path)) as conn:
            if not table(conn, 'asset_design_requests'):
                raise OperationError('asset_design_request_not_found', '设计请求不存在')
            row = conn.execute('''SELECT * FROM asset_design_requests WHERE request_id=? AND project_id=?''',
                               (request_id, self.project_id)).fetchone()
            if row is None:
                raise OperationError('asset_design_request_not_found', '设计请求不存在')
            row = dict(row)
        result = json.loads(row['result'])
        if row['state'] != 'pending' or not row['graph_operation_id']:
            return result
        canvas = read_canvas(self.state_dir, row['canvas_id'])
        expected_revision = self._expected_canvas_revision(row['design_id'])
        if canvas and expected_revision and canvas['revision'] != expected_revision:
            operation = self._graph_operation(row['graph_request_id'])
            marker = ((canvas.get('metadata') or {}).get('creation_projection_receipts') or {}).get(
                operation['id']) if operation else None
            if operation and operation['state'] == 'pending_projection' and not marker:
                self._finalize_projection_conflict(operation, result, canvas['revision'])
                self._save_result(request_id, operation['id'], 'conflict', result)
                return result
        projected = ProjectionService(self.objects).apply(row['graph_operation_id'])
        result['operation_id'] = row['graph_operation_id']
        result['projection_status'] = projected.status
        result['canvas_revision'] = projected.canvas_revision or result.get('canvas_revision')
        state = 'applied' if projected.status == 'applied' else 'conflict' if projected.status == 'conflict' else 'pending'
        result['prepared_execution'] = (self._candidate(result, row['canvas_id'], row['graph_request_id'])
                                        if state == 'applied' else None)
        self._save_result(request_id, row['graph_operation_id'], state, result)
        return result

    def _expected_canvas_revision(self, design_id):
        with closing(_read_only(self.db_path)) as conn:
            obj = ObjectStore._read(conn, design_id, 1)
            return obj['payload'].get('expected_canvas_revision') if obj else None

    @classmethod
    def verified_prepared_execution(cls, ctx, candidate):
        """Return a candidate only when its original applied receipt and node match."""
        nodes = cls.verified_design_nodes(ctx, candidate)
        if nodes is None:
            return None
        return deepcopy(candidate)

    @classmethod
    def verified_candidate_for_node(cls, ctx, canvas_id, node):
        """Resolve a node's design-run candidate only from its applied server receipt."""
        if (not isinstance(canvas_id, str) or not isinstance(node, dict)
                or node.get('type') != 'imageGenNode' or not isinstance(node.get('id'), str)):
            return None
        db_path = Path(ctx.state_dir) / 'creation' / 'objects.sqlite3'
        if not db_path.is_file():
            return None
        with closing(_read_only(db_path)) as conn:
            if not table(conn, 'asset_design_requests'):
                return None
            row = conn.execute('''SELECT state,result FROM asset_design_requests
                WHERE project_id=? AND actor_id=? AND canvas_id=? AND image_node_id=?''',
                (ctx.project_id, ctx.requester_user_id, canvas_id, node['id'])).fetchone()
            if row is None or row['state'] != 'applied':
                return None
            try:
                result = json.loads(row['result'])
            except (TypeError, json.JSONDecodeError):
                return None
        candidate = result.get('prepared_execution') if isinstance(result, dict) else None
        if (not isinstance(candidate, dict) or candidate.get('canvas_id') != canvas_id
                or candidate.get('node_id') != node['id']):
            return None
        verified = cls.verified_prepared_execution(ctx, candidate)
        if verified is None:
            return None
        canvas = read_canvas(ctx.state_dir, canvas_id)
        current = next((item for item in (canvas or {}).get('nodes', [])
                        if item.get('id') == node['id']), None)
        if (not isinstance(current, dict)
                or current.get('data', {}).get('designBinding')
                != node.get('data', {}).get('designBinding')):
            return None
        return verified

    @classmethod
    def verified_design_nodes(cls, ctx, candidate):
        """Return the exact server-created group members for a durable receipt."""
        if not isinstance(candidate, dict) or set(candidate) != {
                'canvas_id', 'node_id', 'node_type', 'operation', 'max_node_runs',
                'source_operation_id', 'request_id'}:
            return None
        if (candidate['node_type'], candidate['operation'], candidate['max_node_runs']) != (
                'imageGenNode', 'generate', 1):
            return None
        # ObjectStore.__init__ opens a write transaction to initialize/backfill
        # its schema. Verification must remain read-only because callers may
        # already hold the project's creation database write transaction.
        db_path = Path(ctx.state_dir) / 'creation' / 'objects.sqlite3'
        if not db_path.is_file():
            return None
        with closing(_read_only(db_path)) as conn:
            op = conn.execute('''SELECT o.state,o.payload,o.request_id,p.canvas_id,p.patch FROM operations o
                JOIN projection_outbox p ON p.operation_id=o.id WHERE o.id=?''',
                (candidate['source_operation_id'],)).fetchone()
            ledger = conn.execute('''SELECT * FROM asset_design_requests WHERE graph_operation_id=?
                AND project_id=? AND actor_id=? AND graph_request_id=?''',
                (candidate['source_operation_id'], ctx.project_id, ctx.requester_user_id,
                 candidate['request_id'])).fetchone() if table(conn, 'asset_design_requests') else None
            if op is None or ledger is None or op['state'] != 'applied' or op['canvas_id'] != candidate['canvas_id']:
                return None
            if ledger['image_node_id'] != candidate['node_id'] or ledger['state'] != 'applied':
                return None
            request = json.loads(op['payload'])
            if request.get('request_id') != candidate['request_id'] or request.get('kind') != 'graph.patch':
                return None
            design = ObjectStore._read(conn, ledger['design_id'], 1)
            if design is None or design['payload'].get('project_id') != ctx.project_id:
                return None
            design_ref = {'id': design['id'], 'revision': design['revision'], 'kind': design['kind']}
            source_proposal = ObjectStore._read(conn, ledger['source_proposal_id'],
                                                ledger['source_proposal_revision'])
            if source_proposal is None or source_proposal['kind'] != 'story_fragment':
                return None
            patch = json.loads(op['patch'])
            graph_operations = patch.get('operations', [])
            added = {item.get('node', {}).get('id'): item['node'] for item in graph_operations
                     if isinstance(item, dict) and item.get('op') == 'add_node'
                     and isinstance(item.get('node'), dict)}
            ids = {ledger['group_id'], ledger['text_node_id'], ledger['image_node_id']}
            if set(added) != ids:
                return None
            expected_reference_edges = []
            for reference in design['payload'].get('references', []):
                if reference.get('kind') != 'canvas_node':
                    continue
                if reference.get('canvas_id') != candidate['canvas_id']:
                    return None
                source_id = reference.get('node_id')
                if not isinstance(source_id, str) or not source_id:
                    return None
                expected_reference_edges.append({
                    'id': cls._reference_edge_id(ctx.project_id, candidate['request_id'],
                                                 source_id, candidate['node_id']),
                    'source': source_id, 'target': candidate['node_id'],
                })
            actual_reference_edges = [item.get('edge') for item in graph_operations
                                      if isinstance(item, dict) and item.get('op') == 'add_edge']
            if (len(graph_operations) != len(ids) + len(expected_reference_edges)
                    or any(not isinstance(item, dict) or item.get('op') not in {'add_node', 'add_edge'}
                           for item in graph_operations)
                    or actual_reference_edges != expected_reference_edges):
                return None
            if ({identifier: added[identifier].get('type') for identifier in ids} != {
                    ledger['group_id']: 'groupNode', ledger['text_node_id']: 'textAnnotationNode',
                    ledger['image_node_id']: 'imageGenNode'}):
                return None
            canvas_layout = design['payload'].get('canvas_layout')
            if canvas_layout is not None:
                if not isinstance(canvas_layout, dict) or set(canvas_layout) != {'group', 'text', 'image'}:
                    return None
                layout_nodes = {
                    'group': (ledger['group_id'], True),
                    'text': (ledger['text_node_id'], False),
                    'image': (ledger['image_node_id'], False),
                }
                for layout_key, (node_id, is_group) in layout_nodes.items():
                    expected_layout = canvas_layout.get(layout_key)
                    if not isinstance(expected_layout, dict):
                        return None
                    node = added[node_id]
                    if (node.get('position') != expected_layout.get('position')
                            or node.get('width') != expected_layout.get('width')
                            or node.get('height') != expected_layout.get('height')):
                        return None
                    if is_group and node.get('style') != {
                            'width': expected_layout.get('width'),
                            'height': expected_layout.get('height')}:
                        return None
            expected_parent = design['payload'].get('parent_group_id')
            if added[ledger['group_id']].get('parentId') != expected_parent:
                return None
            if (added[ledger['text_node_id']].get('parentId') != ledger['group_id']
                    or added[ledger['image_node_id']].get('parentId') != ledger['group_id']):
                return None
        canvas = read_canvas(ctx.state_dir, candidate['canvas_id'])
        if not canvas or canvas.get('project_id') != ctx.project_id:
            return None
        canvas_nodes = {node.get('id'): node for node in canvas.get('nodes', [])}
        group = canvas_nodes.get(ledger['group_id'])
        text_node = canvas_nodes.get(ledger['text_node_id'])
        node = canvas_nodes.get(candidate['node_id'])
        for reference in design['payload'].get('references', []):
            if reference.get('kind') != 'canvas_node':
                continue
            source_id = reference.get('node_id')
            source = canvas_nodes.get(source_id)
            if (reference.get('canvas_id') != candidate['canvas_id'] or source is None
                    or source.get('type') != reference.get('node_type')):
                return None
            try:
                current_fingerprint = cls._canvas_reference_fingerprint(
                    ctx, ctx.project_id, candidate['canvas_id'], source)
            except OperationError:
                return None
            if current_fingerprint != reference.get('input_fingerprint'):
                return None
        current_reference_edges = [edge for edge in canvas.get('edges', [])
                                   if edge.get('target') == candidate['node_id']
                                   and not (edge.get('data') or {}).get('contextLink')]
        if current_reference_edges != expected_reference_edges:
            return None
        binding = (node or {}).get('data', {}).get('designBinding')
        design_payload = design['payload']
        expected_binding = {'design_ref': design_ref, 'kind': design_payload['kind'],
            'name': design_payload['name'], 'description': design_payload['description'],
            'prompt': design_payload['prompt'], 'style_template_id': design_payload['style_template_id'],
            'base_asset_ref': design_payload['base_asset_ref'], 'references': design_payload['references'],
            'source_proposal_ref': {'id': ledger['source_proposal_id'],
                'revision': ledger['source_proposal_revision'], 'kind': 'story_fragment'},
            'target_canvas_id': candidate['canvas_id'], 'parent_group_id': design_payload.get('parent_group_id'),
            'request_id': design_payload['request_id'], 'operation_request_id': candidate['request_id']}
        node_data = (node or {}).get('data', {})
        usage_matches = True
        if 'purpose' in design_payload:
            usage_matches = (
                node_data.get('aspectRatio') == design_payload.get('aspect_ratio')
                and node_data.get('model') == design_payload.get('image_model')
                and ('camera_selection' not in design_payload or
                     node_data.get('cameraSelection') == design_payload.get('camera_selection'))
            )
        if (not group or group.get('type') != 'groupNode' or not text_node
                or text_node.get('type') != 'textAnnotationNode' or not node
                or node.get('type') != 'imageGenNode' or binding != expected_binding or not usage_matches
                or group.get('parentId') != design['payload'].get('parent_group_id')
                or text_node.get('parentId') != ledger['group_id']
                or node.get('parentId') != ledger['group_id']):
            return None
        proposal_payload = source_proposal['payload']
        if (text_node.get('data', {}).get('content') != proposal_payload.get('text')
                or text_node.get('data', {}).get('title') != (proposal_payload.get('title') or design['payload'].get('name'))):
            return None
        return {'group_id': ledger['group_id'], 'text_node_id': ledger['text_node_id'],
                'image_node_id': ledger['image_node_id'], 'parent_group_id': expected_parent,
                'prior_descendant_ids': list(design['payload'].get('prior_descendant_ids', [])),
                'design_ref': design_ref}

