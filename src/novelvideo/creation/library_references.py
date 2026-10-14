"""Trusted, immutable style/technique references, independent of canvas JSON.

Only explicit writes create these tables. A binding and its replay receipt share
one SQL commit; no canvas projection, model execution or remote catalog lookup is
part of this operation. Consumers must use snapshot IDs, never resolve latest.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from novelvideo.freezone.canvas_lock import CanvasLockBusy, canvas_write_lock
from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.production_workflow.coordination import asset_project_lock
from novelvideo.project_config import load_project_config_file_from_state_dir
from .asset_adapter import registration_io
from .asset_reference import readonly, table
from .canvas_workspaces import list_canvases
from .models import StrictModel
from .operations import OperationError


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(packed(value).encode()).hexdigest()


def fail(code, message):
    raise OperationError(code, message)


class DirectionScope(StrictModel):
    level: Literal['project', 'canvas', 'group', 'node']
    canvas_id: str | None = Field(default=None, min_length=1, max_length=64)
    group_id: str | None = Field(default=None, min_length=1, max_length=256)
    node_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode='after')
    def exact_target(self):
        fields = {key for key in ('canvas_id', 'group_id', 'node_id') if getattr(self, key) is not None}
        expected = {'project':set(), 'canvas':{'canvas_id'},
                    'group':{'canvas_id','group_id'}, 'node':{'canvas_id','node_id'}}[self.level]
        if fields != expected:
            raise ValueError('作用域必须明确指定且只指定对应目标')
        return self

    def value(self):
        return self.model_dump(exclude_none=True)


class Selection(StrictModel):
    id: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    proof: str = Field(min_length=1, max_length=128)


class Target(StrictModel):
    scope: DirectionScope
    expected_revision: int = Field(ge=0, strict=True)
    scope_proof: str = Field(min_length=1, max_length=128)


class DirectionApply(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    kind: Literal['style', 'technique']
    action: Literal['apply', 'unset', 'disable']
    selection: Selection | None = None
    scope: DirectionScope | None = None
    expected_revision: int | None = Field(default=None, ge=0, strict=True)
    scope_proof: str | None = None
    targets: list[Target] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def explicit_input(self):
        if (self.action == 'apply') != (self.selection is not None):
            raise ValueError('仅应用操作需要目录选择证据')
        if self.targets is not None:
            if any(value is not None for value in (self.scope, self.expected_revision, self.scope_proof)):
                raise ValueError('批量目标不可与单一作用域混用')
            if any(target.scope.level != 'node' for target in self.targets):
                raise ValueError('批量操作须明确列出节点，不推测分组')
        elif any(value is None for value in (self.scope, self.expected_revision, self.scope_proof)):
            raise ValueError('需要目标及并发版本证据')
        keys = [packed(t.scope.value()) for t in self.all_targets()]
        if len(keys) != len(set(keys)):
            raise ValueError('目标不能重复')
        return self

    def all_targets(self):
        return self.targets if self.targets is not None else [Target(
            scope=self.scope, expected_revision=self.expected_revision, scope_proof=self.scope_proof)]


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS direction_snapshots (
       project_id TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
       PRIMARY KEY(project_id,id))''',
    '''CREATE TABLE IF NOT EXISTS direction_bindings (
       project_id TEXT NOT NULL, scope TEXT NOT NULL, kind TEXT NOT NULL,
       revision INTEGER NOT NULL, state TEXT NOT NULL, snapshot_id TEXT,
       actor_id TEXT NOT NULL, request_id TEXT NOT NULL,
       PRIMARY KEY(project_id,scope,kind))''',
    '''CREATE TABLE IF NOT EXISTS direction_requests (
       project_id TEXT NOT NULL, request_id TEXT NOT NULL, actor_id TEXT NOT NULL,
       request_hash TEXT NOT NULL, payload TEXT NOT NULL, result TEXT NOT NULL,
       PRIMARY KEY(project_id,request_id))''',
)


def _checkpoint(phase):
    """Fault-injection boundary; a committed receipt always wins on retry."""


class DirectionReferences:
    def __init__(self, ctx):
        if not ctx.project_id or not ctx.requester_user_id:
            fail('scope_violation', '缺少授权项目或操作者')
        self.ctx = ctx
        self.state_dir = Path(ctx.state_dir)
        self.db = self.state_dir / 'creation' / 'objects.sqlite3'

    @contextmanager
    def _read(self, conn=None):
        if conn is not None:
            self._identity(conn)
            yield conn
        elif self.db.is_file():
            with readonly(self.db) as connection:
                self._identity(connection)
                yield connection
        else:
            yield None

    def _identity(self, conn):
        # Authenticated context is authoritative for an empty project. Existing
        # workspace/receipt evidence additionally fences accidental root reuse.
        project_ids = set()
        if table(conn, 'operations'):
            project_ids.update(row[0] for row in conn.execute("""SELECT DISTINCT
                COALESCE(json_extract(payload,'$.scope.project_id'), json_extract(payload,'$.project_id'))
                FROM operations WHERE json_valid(payload)""") if row[0])
        if table(conn, 'direction_requests'):
            project_ids.update(row[0] for row in conn.execute('SELECT DISTINCT project_id FROM direction_requests'))
        if project_ids - {self.ctx.project_id}:
            fail('scope_violation', '项目存储身份不匹配')

    def catalog(self, kind):
        if kind not in {'style', 'technique'}:
            fail('invalid_contract', '未知风格或手法类型')
        entries = []
        if kind == 'style':
            from novelvideo.services.style_service import StyleService
            custom = load_project_config_file_from_state_dir(self.state_dir).get('custom_styles') or {}
            # Scope custom styles to the authorized state root. Reuse the library
            # compiler without global cache mutations or inferred output paths.
            class ScopedStyles(StyleService):
                _preset_cache = {}

                @classmethod
                def _load_project_custom_style_map(cls, *args, **kwargs):
                    return deepcopy(custom) if isinstance(custom, dict) else {}

            for item in ScopedStyles.list_all_styles():
                frozen = ScopedStyles.resolve_style_snapshot(item['id']).model_dump(mode='json')
                entries.append({'id':item['id'], 'version':frozen['style_version'],
                    'title':item.get('label') or item.get('name') or item['id'], 'available':True,
                    'configuration':frozen, 'metadata':{'library_type':item['type']}})
        else:
            from novelvideo.freezone.video_director.techniques import list_techniques, project_technique
            for card in list_techniques():
                entries.append({'id':card.id, 'version':card.version, 'title':card.title,
                    'available':card.status == 'active', 'configuration':project_technique(card),
                    'metadata':{'card':card.model_dump(mode='json', exclude={'content_hash'})}})
        for entry in entries:
            entry['kind'] = kind
            entry['proof'] = digest(entry)
        return entries

    def _chain(self, scope):
        chain = [{'level':'project'}]
        if scope.level == 'project':
            return chain, {'project_id':self.ctx.project_id}
        workspace = next((w for w in list_canvases(self.state_dir)
                          if w['canvas_id'] == scope.canvas_id and w['status'] == 'applied'), None)
        canvas = read_canvas(self.state_dir, scope.canvas_id) if workspace else None
        if canvas is None or canvas.get('project_id') != self.ctx.project_id:
            fail('scope_violation', '画布不属于当前项目或尚未创建完成')
        chain.append({'level':'canvas','canvas_id':scope.canvas_id})
        evidence = {'project_id':self.ctx.project_id, 'canvas_id':scope.canvas_id,
                    'canvas_revision':canvas.get('revision'), 'members':[]}
        if scope.level == 'canvas':
            return chain, evidence
        nodes = canvas.get('nodes', [])
        by_id = {n['id']:n for n in nodes}
        if len(by_id) != len(nodes):
            fail('scope_violation', '画布包含重复节点')
        target_id = scope.group_id if scope.level == 'group' else scope.node_id
        target = by_id.get(target_id)
        if target is None or scope.level == 'group' and target.get('type') != 'groupNode':
            fail('scope_violation', '目标节点或分组不存在')
        cursor, seen, parents = target, set(), []
        while cursor is not None:
            if cursor['id'] in seen:
                fail('scope_violation', '分组成员关系存在循环')
            seen.add(cursor['id'])
            evidence['members'].append({k:cursor.get(k) for k in ('id','parentId','type')})
            parent_id = cursor.get('parentId')
            if not parent_id:
                break
            cursor = by_id.get(parent_id)
            if cursor is None or cursor.get('type') != 'groupNode':
                fail('scope_violation', '节点父分组不存在或无效')
            parents.append(cursor['id'])
        for parent in reversed(parents):
            chain.append({'level':'group','canvas_id':scope.canvas_id,'group_id':parent})
        chain.append(scope.value())
        return chain, evidence

    def _binding(self, conn, scope, kind):
        row = None
        if conn is not None and table(conn, 'direction_bindings'):
            row = conn.execute('SELECT * FROM direction_bindings WHERE project_id=? AND scope=? AND kind=?',
                (self.ctx.project_id, packed(scope), kind)).fetchone()
        return {'revision':row['revision'], 'state':row['state'], 'snapshot_id':row['snapshot_id']} if row else {
            'revision':0, 'state':'inherit', 'snapshot_id':None}

    def snapshot(self, snapshot_id, *, conn=None):
        with self._read(conn) as connection:
            row = connection.execute('SELECT payload FROM direction_snapshots WHERE project_id=? AND id=?',
                (self.ctx.project_id, snapshot_id)).fetchone() if connection is not None and table(connection, 'direction_snapshots') else None
        if row is None:
            fail('snapshot_missing', '固定风格或手法版本不存在')
        return json.loads(row['payload'])

    def inspect(self, scope, *, mode=None, duration_seconds=None, conn=None):
        scope = DirectionScope.model_validate(scope)
        chain, evidence = self._chain(scope)
        effective, locals_, revisions = {}, {}, []
        with self._read(conn) as connection:
            for kind in ('style','technique'):
                resolved = {'state':'unset', 'origin_scope':None, 'snapshot_ref':None,
                            'configuration':None, 'applicability':{'status':'not_evaluated','compatible':None,'reasons':[]}}
                for ancestor in chain:
                    binding = self._binding(connection, ancestor, kind)
                    revisions.append({'scope':ancestor,'kind':kind, **binding})
                    if binding['state'] == 'inherit':
                        continue
                    resolved = {'state':binding['state'], 'origin_scope':ancestor,
                        'binding_revision':binding['revision'], 'snapshot_ref':None, 'configuration':None,
                        'applicability':{'status':'not_evaluated','compatible':None,'reasons':[]}}
                    if binding['state'] == 'enabled':
                        frozen = self.snapshot(binding['snapshot_id'], conn=connection)
                        resolved.update(snapshot_ref=frozen['snapshot_ref'], configuration=frozen['configuration'],
                                        title=frozen['title'], version=frozen['version'])
                        if kind == 'technique':
                            if mode is None or duration_seconds is None:
                                resolved['applicability'] = {'status':'not_evaluated','compatible':None,
                                    'reasons':[{'code':'context_required','message':'需提供具体视频模式及时长后评估'}]}
                            else:
                                from novelvideo.freezone.video_director.techniques import TechniqueCard, check_applicability
                                evaluation = check_applicability(TechniqueCard.model_validate(frozen['metadata']['card']), mode, duration_seconds)
                                resolved['applicability'] = {**evaluation, 'status':'compatible' if evaluation['compatible'] else 'incompatible'}
                effective[kind] = resolved
                locals_[kind] = self._binding(connection, scope.value(), kind)
        return {'scope':scope.value(), 'local':locals_, 'effective':effective,
                'proof':digest({'graph':evidence,'bindings':revisions}), 'chain':chain}

    def receipt(self, request_id, *, conn=None):
        with self._read(conn) as connection:
            row = connection.execute('SELECT * FROM direction_requests WHERE project_id=? AND request_id=?',
                (self.ctx.project_id, request_id)).fetchone() if connection is not None and table(connection,'direction_requests') else None
        return dict(row) if row else None

    async def apply(self, body):
        request = DirectionApply.model_validate(body)
        async with asset_project_lock(self.state_dir):
            try:
                return await registration_io(self._apply, request)
            except CanvasLockBusy as exc:
                raise OperationError('canvas_busy', '画布正在保存，请使用原请求重试') from exc

    def _replay(self, request, row):
        if row['request_hash'] != digest(request.model_dump(mode='json')) or row['actor_id'] != self.ctx.requester_user_id:
            fail('idempotency_conflict', '此请求编号已用于不同操作')
        return json.loads(row['result'])

    def _apply(self, request):
        previous = self.receipt(request.request_id)
        if previous:
            return self._replay(request, previous)
        targets = request.all_targets()
        # Validate first: invalid inputs do not create lock files or business DBs.
        for target in targets:
            self._chain(target.scope)
        selected = None
        if request.selection:
            selected = next((e for e in self.catalog(request.kind) if e['id'] == request.selection.id
                             and e['version'] == request.selection.version), None)
            if not selected or not selected['available'] or selected['proof'] != request.selection.proof:
                fail('catalog_stale', '目录内容已变化或下架，请重新选择')
        with ExitStack() as locks:
            for canvas_id in sorted({t.scope.canvas_id for t in targets if t.scope.canvas_id}):
                locks.enter_context(canvas_write_lock(self.state_dir, canvas_id))
            for target in targets:
                self._check_target(target, request.kind)
            self.db.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.db, timeout=30) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute('BEGIN IMMEDIATE')
                for sql in _SCHEMA:
                    conn.execute(sql)
                previous = self.receipt(request.request_id, conn=conn)
                if previous:
                    return self._replay(request, previous)
                for target in targets:
                    self._check_target(target, request.kind, conn=conn)
                snapshot_ref = None
                if selected:
                    snapshot_id = digest({'project_id':self.ctx.project_id,'selection':selected})
                    snapshot_ref = {'snapshot_id':snapshot_id,'kind':request.kind,'revision':1,
                                    'content_hash':selected['proof']}
                    frozen = {**selected, 'snapshot_ref':snapshot_ref}
                    conn.execute('INSERT OR IGNORE INTO direction_snapshots VALUES (?,?,?)',
                        (self.ctx.project_id, snapshot_id, packed(frozen)))
                changed = []
                for target in targets:
                    revision = target.expected_revision + 1
                    state = {'apply':'enabled','unset':'inherit','disable':'disabled'}[request.action]
                    conn.execute('INSERT OR REPLACE INTO direction_bindings VALUES (?,?,?,?,?,?,?,?)',
                        (self.ctx.project_id, packed(target.scope.value()), request.kind, revision, state,
                         snapshot_ref['snapshot_id'] if snapshot_ref else None, self.ctx.requester_user_id, request.request_id))
                    changed.append({'scope':target.scope.value(),'revision':revision,'state':state})
                result = {'request_id':request.request_id,'operation_id':uuid4().hex,'status':'applied',
                          'kind':request.kind,'snapshot_ref':snapshot_ref,'bindings':changed,
                          'effective':[self.inspect(t.scope.value(), conn=conn) for t in targets]}
                conn.execute('INSERT INTO direction_requests VALUES (?,?,?,?,?,?)',
                    (self.ctx.project_id, request.request_id, self.ctx.requester_user_id,
                     digest(request.model_dump(mode='json')), packed(request.model_dump(mode='json')), packed(result)))
                _checkpoint('before_commit')
            _checkpoint('committed')
            return result

    def _check_target(self, target, kind, *, conn=None):
        current = self.inspect(target.scope.value(), conn=conn)
        if current['proof'] != target.scope_proof or current['local'][kind]['revision'] != target.expected_revision:
            fail('direction_conflict', '作用域、成员关系或配置版本已变化，请重新比较')
