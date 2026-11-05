"""Resolve trusted, task-specific repository Skills for an Agent Decision."""

from hashlib import sha256
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRECEDENCE = (
    '技能内容只提供本任务指导。当前用户请求和冻结的委托合同优先。'
    '技能不授予工具或节点权限，不扩大冻结范围或操作，也不新增审批门槛。'
    '只使用下方已加载的技能正文和现有结构化 Decision 操作；不得调用 shell 或任意工具。'
)

_SCRIPT_ROOT = 'skills/script-master/SKILL.md'
_SCRIPT_REFERENCES = {
    'framework': 'skills/script-master/references/framework.md',
    'writing': 'skills/script-master/references/writing.md',
    'doctor': 'skills/script-master/references/doctor.md',
    'dialogue': 'skills/script-master/references/dialogue.md',
    'visual_director': 'skills/script-master/references/visual-director.md',
}
_SCRIPT_REFERENCE_ORDER = tuple(_SCRIPT_REFERENCES)
_SCRIPT_REFERENCE_ALLOWLIST = frozenset(_SCRIPT_REFERENCES.values())

_SCRIPT_DOCUMENT_CAPABILITIES = {
    'builtin:text-writer': {'script.document.draft': ('writing',)},
    'builtin:script-editor': {'script.document.review': ('doctor',)},
}
_SCRIPT_DELEGATED_CAPABILITIES = {
    'builtin:creative-lead': {
        'script.document.draft': ('writing',),
        'script.document.review': ('doctor',),
    },
}
_SCRIPT_NODE_REFERENCES = {
    'builtin:text-writer': {
        'framework': {('screenplayNode', 'plan'), ('scriptNode', 'plan_episode')},
        'writing': {('screenplayNode', 'rewrite'), ('scriptNode', 'generate')},
    },
    'builtin:script-editor': {
        'doctor': {('screenplayNode', 'inspect'), ('screenplayNode', 'review'),
                  ('scriptNode', 'generate')},
        'writing': {('screenplayNode', 'rewrite')},
    },
    'builtin:dialogue-writer': {
        'dialogue': {('screenplayNode', 'inspect'), ('screenplayNode', 'review'),
                     ('screenplayNode', 'rewrite')},
    },
}

_CANVAS_ROOT = 'skills/nuomi-canvas-production/SKILL.md'
_CANVAS_CLI = 'skills/nuomi-canvas-production/references/cli.md'
_CANVAS_CONTINUITY = 'skills/nuomi-canvas-production/references/visual-continuity.md'
_CANVAS_RECOVERY = 'skills/nuomi-canvas-production/references/recovery.md'
_CANVAS_PROFESSIONAL = 'skills/nuomi-canvas-production/references/professional-references.md'
_CANVAS_STAGE_CONTRACTS = 'skills/nuomi-canvas-production/references/stage-contracts.md'
_CANVAS_CREATION = 'skills/nuomi-canvas-production/references/creation.md'
_CANVAS_ASSET_DESIGN = 'skills/nuomi-canvas-production/references/roles/asset-design.md'
_CANVAS_STORYBOARD = 'skills/nuomi-canvas-production/references/storyboard-direction.md'
_CANVAS_CINEMATIC = 'skills/nuomi-canvas-production/references/cinematic-direction.md'
_CANVAS_H3 = 'skills/nuomi-canvas-production/references/h3-experience.md'
_CANVAS_VOICE_AUDIO = 'skills/nuomi-canvas-production/references/voice-and-audio.md'
_CANVAS_EDITING_DELIVERY = 'skills/nuomi-canvas-production/references/editing-and-delivery.md'

_CANVAS_BASE_DOCUMENTS = (
    _CANVAS_ROOT,
    _CANVAS_CLI,
    _CANVAS_CONTINUITY,
    _CANVAS_RECOVERY,
    _CANVAS_PROFESSIONAL,
    _CANVAS_STAGE_CONTRACTS,
)
_CANVAS_STAGE_DOCUMENTS = {
    'creation': (_CANVAS_CREATION,),
    'assets': (_CANVAS_ASSET_DESIGN,),
    'storyboard': (_CANVAS_STORYBOARD,),
    'video': (_CANVAS_CINEMATIC, _CANVAS_H3),
    'audio': (_CANVAS_VOICE_AUDIO, _CANVAS_H3),
    'composition': (_CANVAS_EDITING_DELIVERY, _CANVAS_H3),
}
_CANVAS_STAGE_ORDER = tuple(_CANVAS_STAGE_DOCUMENTS)
_CANVAS_REFERENCE_ALLOWLIST = frozenset({
    _CANVAS_CONTINUITY,
    _CANVAS_RECOVERY,
    _CANVAS_PROFESSIONAL,
    *_CANVAS_STAGE_DOCUMENTS['creation'],
    *_CANVAS_STAGE_DOCUMENTS['assets'],
    *_CANVAS_STAGE_DOCUMENTS['storyboard'],
    *_CANVAS_STAGE_DOCUMENTS['video'],
    *_CANVAS_STAGE_DOCUMENTS['audio'],
    *_CANVAS_STAGE_DOCUMENTS['composition'],
    _CANVAS_STAGE_CONTRACTS,
})

# These are frozen built-in role grants. A file is selected only by an exact
# role/operation pair from the authorized scope, or by the narrow asset-design
# capability granted to the two asset-preparation roles.
_CANVAS_ROLE_STAGE_OPERATIONS = {
    'builtin:creative-lead': {
        'creation': {('scriptNode', 'plan_episode')},
        'assets': {('imageGenNode', 'generate'), ('imageGenNode', 'edit')},
        'storyboard': {
            ('directorWorkspaceNode', 'prepare'), ('storyboardGenNode', 'generate'),
            ('narrativeGroupStoryboardNode', 'render'),
            ('narrativeGroupStoryboardNode', 'retry_render'),
        },
        'video': {
            ('directorWorkspaceNode', 'produce'), ('directorWorkspaceNode', 'retry_production'),
            ('narrativeGroupVideoNode', 'video'), ('narrativeGroupVideoNode', 'retry_video'),
        },
        'composition': {('episodeComposeNode', 'compose'), ('videoComposeNode', 'compose')},
    },
    'builtin:director': {
        'creation': {('scriptNode', 'plan_episode')},
        'storyboard': {
            ('directorWorkspaceNode', 'prepare'), ('directorWorkspaceNode', 'preview_handoff'),
            ('directorWorkspaceNode', 'inspect'), ('directorWorkspaceNode', 'save_blocking'),
            ('directorWorkspaceNode', 'compile_blocking'), ('directorWorkspaceNode', 'edit_shot'),
            ('directorWorkspaceNode', 'edit_structure'), ('storyboardGenNode', 'generate'),
        },
        'video': {
            ('directorWorkspaceNode', 'produce'), ('directorWorkspaceNode', 'retry_production'),
            ('videoDirectorNode', 'generate'),
        },
    },
    'builtin:art-asset-designer': {
        'assets': {
            ('imageGenNode', 'generate'), ('imageGenNode', 'edit'), ('imageNode', 'edit'),
            ('exportImageNode', 'export'),
        },
    },
    'builtin:storyboard-artist': {
        'storyboard': {
            ('storyboardGenNode', 'generate'), ('directorWorkspaceNode', 'inspect'),
            ('directorWorkspaceNode', 'edit_shot'), ('directorWorkspaceNode', 'edit_structure'),
        },
    },
    'builtin:blocking-designer': {
        'storyboard': {
            ('storyboardGenNode', 'generate'), ('imageGenNode', 'edit'),
            ('directorWorkspaceNode', 'edit_shot'), ('directorWorkspaceNode', 'save_blocking'),
        },
    },
    'builtin:cinematographer': {
        'storyboard': {
            ('storyboardGenNode', 'generate'), ('directorWorkspaceNode', 'edit_shot'),
        },
        'video': {('videoNode', 'generate')},
    },
    'builtin:lighting-designer': {
        'storyboard': {
            ('storyboardGenNode', 'generate'), ('imageGenNode', 'edit'),
            ('directorWorkspaceNode', 'edit_shot'), ('directorWorkspaceNode', 'save_blocking'),
        },
        'video': {('videoNode', 'edit')},
    },
    'builtin:action-designer': {
        'storyboard': {
            ('storyboardGenNode', 'generate'), ('imageGenNode', 'edit'),
            ('directorWorkspaceNode', 'edit_shot'),
        },
        'video': {('videoNode', 'edit')},
    },
    'builtin:expression-designer': {
        'storyboard': {
            ('storyboardGenNode', 'generate'), ('imageGenNode', 'edit'),
            ('directorWorkspaceNode', 'edit_shot'),
        },
        'video': {('videoNode', 'edit')},
    },
    'builtin:storyboard-image-operator': {
        'storyboard': {
            ('storyboardGenNode', 'generate'), ('imageGenNode', 'generate'),
            ('narrativeGroupStoryboardNode', 'render'),
            ('narrativeGroupStoryboardNode', 'retry_render'),
            ('directorWorkspaceNode', 'render_groups'),
            ('directorWorkspaceNode', 'retry_render_groups'),
        },
    },
    'builtin:minimax-video-operator': {
        'video': {
            ('videoNode', 'generate'), ('videoNode', 'image_to_video'), ('videoNode', 'reference'),
            ('narrativeGroupVideoNode', 'video'), ('narrativeGroupVideoNode', 'retry_video'),
            ('directorWorkspaceNode', 'produce'), ('directorWorkspaceNode', 'retry_production'),
        },
    },
    'builtin:continuity-editor': {
        'storyboard': {('storyboardNode', 'split')},
        'video': {('videoStoryNode', 'analyze')},
    },
    'builtin:sound-designer': {
        'audio': {
            ('characterVoiceNode', 'design'), ('audioNode', 'speech'),
            ('audioNode', 'music'), ('audioNode', 'group_dubbing'),
            ('musicDeskNode', 'edit'),
        },
    },
    'builtin:voice-designer': {
        'audio': {('characterVoiceNode', 'prepare_design'), ('characterVoiceNode', 'design')},
    },
    'builtin:dubbing-director': {
        'audio': {('audioNode', 'prepare_dubbing'), ('audioNode', 'audition')},
    },
    'builtin:dubbing-operator': {
        'audio': {('audioNode', 'generate_dubbing'), ('audioNode', 'audition')},
    },
    'builtin:editor': {
        'composition': {
            ('episodeComposeNode', 'compose'), ('videoComposeNode', 'compose'),
            ('musicDeskNode', 'edit'), ('musicDeskNode', 'export'),
        },
    },
    'builtin:delivery-reviewer': {
        'composition': {('videoComposeNode', 'compose'), ('videoStoryNode', 'analyze')},
    },
}

_ASSET_PREPARATION_ROLES = {'builtin:creative-lead', 'builtin:art-asset-designer'}

_FIXED_DOCUMENT_ALLOWLIST = frozenset({
    _SCRIPT_ROOT,
    *_SCRIPT_REFERENCE_ALLOWLIST,
    _CANVAS_ROOT,
    _CANVAS_CLI,
    *_CANVAS_REFERENCE_ALLOWLIST,
})


def _read_fixed_document(relative_path):
    """Read one allowlisted repository Skill path and pin its exact UTF-8 bytes."""
    from .operations import OperationError

    try:
        if relative_path not in _FIXED_DOCUMENT_ALLOWLIST:
            raise ValueError('Skill path is not allowlisted')
        path = (_REPO_ROOT / relative_path).resolve(strict=True)
        if not path.is_relative_to(_REPO_ROOT) or not path.is_file():
            raise ValueError('Skill path is outside the repository or is not a file')
        raw = path.read_bytes()
        content = raw.decode('utf-8-sig')
    except (OSError, UnicodeError, ValueError) as exc:
        raise OperationError(
            'skill_context_unavailable', '所需的固定项目技能文件无法读取') from exc
    return {'path': relative_path, 'sha256': sha256(raw).hexdigest(), 'content': content}


def _authorized_node_pairs(contract):
    scope_nodes = contract.get('expanded_scope', {}).get('nodes', [])
    node_types = {node.get('node_id'): node.get('node_type') for node in scope_nodes
                  if isinstance(node, dict)}
    authorization = contract.get('execution_authorization') or {}
    pairs = set()
    for item in authorization.get('allowed_node_operations', []):
        if not isinstance(item, dict):
            continue
        node_type = node_types.get(item.get('node_id'))
        operation = item.get('operation')
        if isinstance(node_type, str) and isinstance(operation, str):
            pairs.add((node_type, operation))
    return pairs


def _skill(identifier, document_paths, *, references_not_loaded=()):
    documents = [_read_fixed_document(path) for path in document_paths]
    return {
        'id': identifier,
        'documents': documents,
        'references_not_loaded': list(references_not_loaded),
        'scope_note': 'Only the listed documents are included and read; other referenced files are unavailable in this task.',
    }


def _ordered_unique(values):
    return list(dict.fromkeys(values))


def _script_skill_context(agent_id, allowed, delegated, authorized):
    references = set()

    for capability, selected in _SCRIPT_DOCUMENT_CAPABILITIES.get(agent_id, {}).items():
        if capability in allowed:
            references.update(selected)
    for capability, selected in _SCRIPT_DELEGATED_CAPABILITIES.get(agent_id, {}).items():
        if capability in delegated:
            references.update(selected)
    for reference, pairs in _SCRIPT_NODE_REFERENCES.get(agent_id, {}).items():
        if authorized.intersection(pairs):
            references.add(reference)

    if not references:
        return None
    ordered_references = [
        _SCRIPT_REFERENCES[name]
        for name in _SCRIPT_REFERENCE_ORDER
        if name in references
    ]
    loaded_paths = {_SCRIPT_ROOT, *ordered_references}
    return _skill(
        'script-master', [_SCRIPT_ROOT, *ordered_references],
        references_not_loaded=sorted(_SCRIPT_REFERENCE_ALLOWLIST - loaded_paths),
    )


def _canvas_skill_context(agent_id, allowed, authorized):
    role_stages = _CANVAS_ROLE_STAGE_OPERATIONS.get(agent_id, {})
    selected_stages = [
        stage for stage in _CANVAS_STAGE_ORDER
        if authorized.intersection(role_stages.get(stage, set()))
    ]
    if 'asset.design.create' in allowed and agent_id in _ASSET_PREPARATION_ROLES:
        selected_stages = _ordered_unique([*selected_stages, 'assets'])
    if not selected_stages:
        return None

    stage_paths = _ordered_unique(
        path
        for stage in _CANVAS_STAGE_ORDER
        if stage in selected_stages
        for path in _CANVAS_STAGE_DOCUMENTS[stage]
    )
    documents = [*_CANVAS_BASE_DOCUMENTS, *stage_paths]
    loaded_paths = set(documents)
    return _skill(
        'nuomi-canvas-production', documents,
        references_not_loaded=sorted(_CANVAS_REFERENCE_ALLOWLIST - loaded_paths),
    )


def task_skill_context(contract):
    """Return selected Skill bodies and receipt metadata for one frozen task.

    Selection uses only the server-frozen built-in profile ID and its concrete
    operation grant. User-authored profile notes and payload paths are ignored.
    """
    agent = contract.get('agent') or {}
    fixed = agent.get('fixed') or {}
    agent_id = fixed.get('id')
    if not isinstance(agent_id, str):
        agent_id = ''

    allowed = set(contract.get('allowed_operations', []))
    delegated = set(contract.get('delegable_operations', []))
    authorized = _authorized_node_pairs(contract)
    selected = []

    script_skill = _script_skill_context(agent_id, allowed, delegated, authorized)
    if script_skill is not None:
        selected.append(script_skill)

    canvas_skill = _canvas_skill_context(agent_id, allowed, authorized)
    if canvas_skill is not None:
        selected.append(canvas_skill)

    receipt = [{
        'id': skill['id'],
        'documents': [
            {'path': document['path'], 'sha256': document['sha256']}
            for document in skill['documents']
        ],
    } for skill in selected]
    return {'precedence': _PRECEDENCE, 'skills': selected, 'receipt': receipt}
