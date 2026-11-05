import json
import sqlite3
from types import SimpleNamespace

from PIL import Image
import pytest

from novelvideo.creation.asset_generation import AssetGeneration
from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace
from novelvideo.creation.operations import OperationError
from novelvideo.creation.projection import ProjectionService
from novelvideo.creation.store import ObjectStore
from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.freezone.orchestration.adapters.requests import build_request
from novelvideo.freezone.orchestration.models import frozen_node_result
from novelvideo.freezone.paths import canvas_path


def _workspace(tmp_path, image_url='/static/projects/p/assets/reference.png'):
    state_dir = tmp_path / 'state'
    output_dir = tmp_path / 'media'
    state_dir.mkdir()
    output_dir.mkdir()
    ctx = SimpleNamespace(project_id='p', requester_user_id='alice',
                          state_dir=state_dir, output_dir=output_dir)
    image_path = output_dir / 'assets' / 'reference.png'
    image_path.parent.mkdir()
    Image.new('RGB', (12, 12), color=(30, 90, 140)).save(image_path)

    objects = ObjectStore(state_dir)
    created = CanvasWorkspace(objects, project_id='p').create(
        'alice', CanvasCreate(request_id='reference-canvas', kind='concept', title='资产设计'))
    ProjectionService(objects).apply(created.operation_id)
    canvas_id = created.object_refs[0]['canvas_id']
    canvas = read_canvas(state_dir, canvas_id)
    canvas['nodes'] = [{'id': 'reference-image', 'type': 'uploadNode',
                        'data': {'imageUrl': image_url}}]
    canvas['edges'] = []
    canvas['revision'] += 1
    canvas_path(state_dir, canvas_id).write_text(json.dumps(canvas), encoding='utf-8')
    return ctx, canvas_id, image_path


def _prepare(ctx, canvas_id, request_id='asset-design-reference'):
    canvas = read_canvas(ctx.state_dir, canvas_id)
    return AssetGeneration(ctx).prepare('alice', {
        'request_id': request_id,
        'canvas_id': canvas_id,
        'expected_canvas_revision': canvas['revision'],
        'kind': 'character',
        'name': '林岚',
        'description': '夜班记者',
        'prompt': '雨夜站台中的记者肖像',
        'source_proposal': {'title': '人物设定', 'text': '林岚是夜班记者。'},
        'references': [{'kind': 'canvas_node', 'node_id': 'reference-image'}],
    })


def test_canvas_image_references_become_verified_generation_inputs(tmp_path):
    ctx, canvas_id, image_path = _workspace(tmp_path)

    result = _prepare(ctx, canvas_id)

    assert result['projection_status'] == 'applied'
    candidate = result['prepared_execution']
    canvas = read_canvas(ctx.state_dir, canvas_id)
    assert {'source': 'reference-image', 'target': candidate['node_id']} in [
        {key: edge[key] for key in ('source', 'target')} for edge in canvas['edges']]
    source = next(node for node in canvas['nodes'] if node['id'] == 'reference-image')
    target = next(node for node in canvas['nodes'] if node['id'] == candidate['node_id'])

    _, body = build_request(target, 'generate', [
        {'node_id': source['id'], 'result': frozen_node_result(source)},
    ], canvas_id=canvas_id, attempt_id='attempt')
    assert body['reference_urls'] == ['/static/projects/p/assets/reference.png']
    assert AssetGeneration.verified_prepared_execution(ctx, candidate) == candidate
    assert AssetGeneration.verified_candidate_for_node(ctx, canvas_id, target) == candidate
    forged = {**target, 'data': {**target['data'], 'designBinding': {
        **target['data']['designBinding'], 'purpose': 'scene',
    }}}
    assert AssetGeneration.verified_candidate_for_node(ctx, canvas_id, forged) is None

    # The selected node identity is not enough: replacing bytes at the same URL
    # invalidates the prepared design's frozen image reference.
    Image.new('RGB', (12, 12), color=(200, 10, 20)).save(image_path)
    assert AssetGeneration.verified_prepared_execution(ctx, candidate) is None


@pytest.mark.parametrize(("kind", "purpose", "expected_ratio"), [
    ("character", "avatar", "1:1"),
    ("character", "body", "2:3"),
    ("character", "half_body", "2:3"),
    ("character", "full_body", "2:3"),
    ("character", "character_sheet", "16:9"),
    ("character", None, "16:9"),
    ("scene", "scene", "16:9"),
    ("scene", None, "16:9"),
])
def test_asset_design_usage_ratio_is_frozen_and_used_by_image_request(
    tmp_path, kind, purpose, expected_ratio,
):
    ctx, canvas_id, _ = _workspace(tmp_path)
    canvas = read_canvas(ctx.state_dir, canvas_id)
    result = AssetGeneration(ctx).prepare('alice', {
        'request_id': f'asset-design-{kind}-{purpose}',
        'canvas_id': canvas_id,
        'expected_canvas_revision': canvas['revision'],
        'kind': kind,
        **({'purpose': purpose} if purpose is not None else {}),
        'name': '居民楼门厅' if kind == 'scene' else '林岚',
        'description': '雨夜停电时的门厅' if kind == 'scene' else '陈奶奶70岁、灰白发、栗色开衫',
        'prompt': '宽幅居民楼门厅' if kind == 'scene' else '展示已确认的角色造型',
        'source_proposal': {'title': '设定', 'text': '用于验证画幅用途。'},
    })

    projected = read_canvas(ctx.state_dir, canvas_id)
    image_node = next(node for node in projected['nodes'] if node['id'] == result['image_node_id'])
    binding = image_node['data']['designBinding']
    assert image_node['data']['size'] == '1K'
    design = ObjectStore(ctx.state_dir).read(binding['design_ref']['id'], 1)
    assert binding['kind'] == kind
    expected_purpose = ('character_sheet' if kind == 'character' and purpose is None
                        else 'half_body' if kind == 'character' and purpose == 'body'
                        else purpose or 'scene')
    assert design['payload']['purpose'] == expected_purpose
    assert design['payload']['aspect_ratio'] == expected_ratio
    assert image_node['data']['aspectRatio'] == expected_ratio
    if expected_purpose == 'character_sheet':
        prompt = design['payload']['prompt']
        assert image_node['data']['prompt'] == prompt
        for required in ('出图比例为16:9', '横排四列', '最左侧区域', '脸部特写', '正面全身', '侧面全身',
                         '背面全身', '共用同一基线', '沿用当前角色设定', '栗色开衫',
                         '中性浅色纯背景', '不得出现文字'):
            assert required in prompt
        assert '蓝灰工装' not in prompt and '蓝灰色工装' not in prompt

    _, body = build_request(image_node, 'generate', [], canvas_id=canvas_id, attempt_id='asset-attempt')
    assert body['aspect_ratio'] == expected_ratio
    if expected_purpose == 'character_sheet':
        assert body['prompt'] == image_node['data']['prompt']
    explicitly_resized = {**image_node, 'data': {**image_node['data'], 'requestAspectRatio': '21:9'}}
    _, explicit_body = build_request(explicitly_resized, 'generate', [],
                                     canvas_id=canvas_id, attempt_id='asset-explicit-ratio')
    assert explicit_body['aspect_ratio'] == '21:9'


@pytest.mark.parametrize(("kind", "purpose", "focal_length", "aperture"), [
    ("character", "avatar", 75, "f/4"),
    ("character", "half_body", 50, "f/4"),
    ("character", "full_body", 50, "f/5.6"),
    ("character", "character_sheet", 50, "f/8"),
    ("character", "body", 50, "f/4"),
    ("scene", "scene", 35, "f/8"),
])
def test_asset_design_camera_defaults_are_frozen_on_node_and_reach_provider_request(
    tmp_path, kind, purpose, focal_length, aperture,
):
    ctx, canvas_id, _ = _workspace(tmp_path)
    canvas = read_canvas(ctx.state_dir, canvas_id)
    result = AssetGeneration(ctx).prepare('alice', {
        'request_id': f'asset-camera-{kind}-{purpose}',
        'canvas_id': canvas_id,
        'expected_canvas_revision': canvas['revision'],
        'kind': kind,
        'purpose': purpose,
        'name': '居民楼门厅' if kind == 'scene' else '林岚',
        'description': '雨夜停电时的门厅' if kind == 'scene' else '夜班记者',
        'prompt': '环境与人物外观设定',
        'source_proposal': {'title': '设定', 'text': '相机选择按用途固定为可编辑初值。'},
    })

    image_node = next(node for node in read_canvas(ctx.state_dir, canvas_id)['nodes']
                      if node['id'] == result['image_node_id'])
    expected = {
        'cameraBodyId': 'arri_alexa_65',
        'lensId': 'arri_signature_prime',
        'focalLengthMm': focal_length,
        'aperture': aperture,
    }
    assert image_node['data']['cameraSelection'] == expected
    design = ObjectStore(ctx.state_dir).read(image_node['data']['designBinding']['design_ref']['id'], 1)
    assert design['payload']['camera_selection'] == expected

    _, body = build_request(image_node, 'generate', [], canvas_id=canvas_id, attempt_id='asset-camera')
    assert body['camera'] == {
        'camera_body': 'ARRI ALEXA 65',
        'lens': 'Arri Signature Prime',
        'focal_length_mm': focal_length,
        'aperture': aperture,
    }


def test_explicit_asset_design_camera_fields_override_usage_defaults_and_reject_illegal_values(tmp_path):
    ctx, canvas_id, _ = _workspace(tmp_path)
    canvas = read_canvas(ctx.state_dir, canvas_id)
    result = AssetGeneration(ctx).prepare('alice', {
        'request_id': 'asset-camera-explicit',
        'canvas_id': canvas_id,
        'expected_canvas_revision': canvas['revision'],
        'kind': 'scene',
        'purpose': 'scene',
        'camera_selection': {'focalLengthMm': 24, 'aperture': 'f/5.6'},
        'name': '居民楼门厅',
        'description': '雨夜停电时的门厅',
        'prompt': '门厅纵深构图',
        'source_proposal': {'title': '场景设定', 'text': '纵向空间构图。'},
    })
    image_node = next(node for node in read_canvas(ctx.state_dir, canvas_id)['nodes']
                      if node['id'] == result['image_node_id'])
    assert image_node['data']['cameraSelection'] == {
        'cameraBodyId': 'arri_alexa_65',
        'lensId': 'arri_signature_prime',
        'focalLengthMm': 24,
        'aperture': 'f/5.6',
    }

    canvas = read_canvas(ctx.state_dir, canvas_id)
    with pytest.raises(Exception):
        AssetGeneration(ctx).prepare('alice', {
            'request_id': 'asset-camera-illegal-85mm',
            'canvas_id': canvas_id,
            'expected_canvas_revision': canvas['revision'],
            'kind': 'character',
            'purpose': 'avatar',
            'camera_selection': {'focalLengthMm': 85},
            'name': '林岚',
            'description': '夜班记者',
            'prompt': '人物肖像',
            'source_proposal': {'title': '人物设定', 'text': '合法镜头档位校验。'},
        })


def test_explicit_asset_design_ratio_overrides_purpose_default(tmp_path):
    ctx, canvas_id, _ = _workspace(tmp_path)
    canvas = read_canvas(ctx.state_dir, canvas_id)
    result = AssetGeneration(ctx).prepare('alice', {
        'request_id': 'asset-design-scene-explicit-ratio',
        'canvas_id': canvas_id,
        'expected_canvas_revision': canvas['revision'],
        'kind': 'scene',
        'purpose': 'scene',
        'aspect_ratio': '2:3',
        'name': '居民楼门厅',
        'description': '雨夜停电时的门厅',
        'prompt': '门厅纵深构图',
        'source_proposal': {'title': '场景设定', 'text': '纵向空间构图。'},
    })

    projected = read_canvas(ctx.state_dir, canvas_id)
    image_node = next(node for node in projected['nodes'] if node['id'] == result['image_node_id'])
    design = ObjectStore(ctx.state_dir).read(image_node['data']['designBinding']['design_ref']['id'], 1)
    assert design['payload']['aspect_ratio'] == '2:3'
    assert image_node['data']['aspectRatio'] == '2:3'
    _, body = build_request(image_node, 'generate', [], canvas_id=canvas_id, attempt_id='asset-attempt')
    assert body['aspect_ratio'] == '2:3'


def test_character_sheet_keeps_explicit_ratio_and_camera_overrides(tmp_path):
    ctx, canvas_id, _ = _workspace(tmp_path)
    canvas = read_canvas(ctx.state_dir, canvas_id)
    result = AssetGeneration(ctx).prepare('alice', {
        'request_id': 'asset-character-sheet-explicit-camera',
        'canvas_id': canvas_id,
        'expected_canvas_revision': canvas['revision'],
        'kind': 'character',
        'purpose': 'character_sheet',
        'aspect_ratio': '21:9',
        'camera_selection': {'focalLengthMm': 35, 'aperture': 'f/5.6'},
        'name': '林岚',
        'description': '夜班记者，蓝灰工装',
        'prompt': '保持短发、冷静神态',
        'source_proposal': {'title': '人物设定', 'text': '角色设定表参数可显式调整。'},
    })
    image_node = next(node for node in read_canvas(ctx.state_dir, canvas_id)['nodes']
                      if node['id'] == result['image_node_id'])
    assert image_node['data']['aspectRatio'] == '21:9'
    binding = image_node['data']['designBinding']
    design = ObjectStore(ctx.state_dir).read(binding['design_ref']['id'], binding['design_ref']['revision'])
    assert '出图比例为21:9' in design['payload']['prompt']
    assert '出图比例为16:9' not in design['payload']['prompt']
    assert image_node['data']['cameraSelection'] == {
        'cameraBodyId': 'arri_alexa_65',
        'lensId': 'arri_signature_prime',
        'focalLengthMm': 35,
        'aperture': 'f/5.6',
    }
    _, body = build_request(image_node, 'generate', [], canvas_id=canvas_id,
                            attempt_id='asset-character-sheet-explicit-camera')
    assert body['aspect_ratio'] == '21:9'
    assert body['camera']['focal_length_mm'] == 35
    assert body['camera']['aperture'] == 'f/5.6'


def test_pending_legacy_design_replay_uses_its_frozen_purpose_ratio_and_prompt(tmp_path, monkeypatch):
    from novelvideo.creation import asset_generation as asset_generation_module

    ctx, canvas_id, _ = _workspace(tmp_path)
    canvas = read_canvas(ctx.state_dir, canvas_id)
    body = {
        'request_id': 'legacy-pending-half-body',
        'canvas_id': canvas_id,
        'expected_canvas_revision': canvas['revision'],
        'kind': 'character',
        'name': '林岚',
        'description': '旧版半身角色设计',
        'prompt': '旧版半身图提示词',
        'source_proposal': {'title': '人物设定', 'text': '旧版本已持久化的来源。'},
    }
    service = AssetGeneration(ctx)
    original_apply = asset_generation_module.OperationService.apply

    def fail_before_projection(*args, **kwargs):
        raise OperationError('temporary_projection_failure', '模拟投影前中断')

    monkeypatch.setattr(asset_generation_module.OperationService, 'apply', fail_before_projection)
    with pytest.raises(OperationError):
        service.prepare('alice', body)

    with sqlite3.connect(service.db_path) as conn:
        design_id = conn.execute(
            'SELECT design_id FROM asset_design_requests WHERE request_id=?',
            (body['request_id'],),
        ).fetchone()[0]
    with service.objects.transaction() as conn:
        design = ObjectStore._read(conn, design_id, 1)
        frozen = design['payload']
        frozen.update(
            purpose='half_body', aspect_ratio='2:3',
            camera_selection={
                'cameraBodyId': 'arri_alexa_65', 'lensId': 'arri_signature_prime',
                'focalLengthMm': 50, 'aperture': 'f/4',
            },
            prompt='旧版本已冻结的人物半身生成提示词',
        )
        conn.execute('UPDATE object_versions SET payload=? WHERE object_id=? AND revision=1',
                     (json.dumps(frozen, ensure_ascii=False), design_id))

    monkeypatch.setattr(asset_generation_module.OperationService, 'apply', original_apply)
    result = service.prepare('alice', body)
    image_node = next(node for node in read_canvas(ctx.state_dir, canvas_id)['nodes']
                      if node['id'] == result['image_node_id'])

    assert result['projection_status'] == 'applied'
    assert result['prepared_execution'] is not None
    assert image_node['data']['aspectRatio'] == '2:3'
    assert image_node['data']['cameraSelection']['focalLengthMm'] == 50
    assert image_node['data']['cameraSelection']['aperture'] == 'f/4'
    assert image_node['data']['prompt'] == '旧版本已冻结的人物半身生成提示词'


def test_asset_design_freezes_the_configured_grsai_model_without_secret_resolution(tmp_path, monkeypatch):
    from novelvideo import config
    from novelvideo.media_capabilities.models import ProviderAccount
    from novelvideo.media_capabilities.store import MediaCapabilityStore

    settings_root = tmp_path / 'app-state'
    settings_path = settings_root / 'local' / 'settings.db'
    monkeypatch.setattr(config, 'STATE_DIR', str(settings_root))
    store = MediaCapabilityStore(settings_path)
    store.save_provider(ProviderAccount(
        id='grsai-main', provider_type='grsai', base_url='https://grsai.invalid',
        model='nano-banana', credential_ref='env://GRSAI_API_KEY'))
    ctx, canvas_id, _ = _workspace(tmp_path)

    result = _prepare(ctx, canvas_id, request_id='asset-design-configured-model')

    image_node = next(node for node in read_canvas(ctx.state_dir, canvas_id)['nodes']
                      if node['id'] == result['image_node_id'])
    assert image_node['data']['model'] == 'grsai/nano-banana'
    design = ObjectStore(ctx.state_dir).read(image_node['data']['designBinding']['design_ref']['id'], 1)
    assert design['payload']['image_model'] == 'grsai/nano-banana'
    _, body = build_request(image_node, 'generate', [], canvas_id=canvas_id, attempt_id='asset-attempt')
    assert body['provider'] == 'grsai'
    assert body['model'] == 'nano-banana'


def test_canvas_image_reference_rejects_foreign_url_before_projection(tmp_path):
    ctx, canvas_id, _ = _workspace(tmp_path, image_url='https://example.invalid/reference.png')

    with pytest.raises(OperationError, match='当前项目中可验证的本地图片'):
        _prepare(ctx, canvas_id)

    canvas = read_canvas(ctx.state_dir, canvas_id)
    assert len(canvas['nodes']) == 1


def test_canvas_node_without_typed_image_output_is_not_silently_ignored(tmp_path):
    ctx, canvas_id, _ = _workspace(tmp_path)
    canvas = read_canvas(ctx.state_dir, canvas_id)
    canvas['nodes'][0] = {'id': 'reference-image', 'type': 'textAnnotationNode',
                          'data': {'content': 'not an image'}}
    canvas['revision'] += 1
    canvas_path(ctx.state_dir, canvas_id).write_text(json.dumps(canvas), encoding='utf-8')

    with pytest.raises(OperationError, match='没有已保存的图片输出'):
        _prepare(ctx, canvas_id)
