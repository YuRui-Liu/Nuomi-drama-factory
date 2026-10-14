"""Confirmed project definitions and typed custom-object domain operations.

No runtime code, adapters or client supplied confirmation flags are executed.
All writes, immutable versions and the request receipt share one SQL commit.
"""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import json
import math
import re
import sqlite3
from uuid import uuid4

from pydantic import Field

from novelvideo.freezone.canvas_lock import CanvasLockBusy, canvas_write_lock
from novelvideo.production_workflow.coordination import asset_project_lock
from .asset_adapter import registration_io
from .asset_reference import readonly, table
from .canvas_workspaces import list_canvases
from .library_references import DirectionReferences, DirectionScope, digest, packed
from .models import OBJECT_KINDS, StrictModel
from .operations import OperationError
from .store import ObjectStore


def require(value, message, code='invalid_contract', **details):
    if not value:
        raise OperationError(code, message, **details)


def exact(payload, required, optional=()):
    require(isinstance(payload, dict) and set(required) <= payload.keys()
            and payload.keys() <= set(required) | set(optional), '操作字段不符合约定')


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, '版本必须为整数')
    return value


def text(value, maximum=2000):
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum, '文本字段无效')
    return value


class DomainCommand(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=80)
    payload: dict


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS creative_domain_receipts (
        request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, domain TEXT NOT NULL,
        request_hash TEXT NOT NULL, result TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ontology_definitions (
        name TEXT PRIMARY KEY, project_id TEXT NOT NULL, category TEXT NOT NULL,
        current_revision INTEGER NOT NULL, active_revision INTEGER NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS ontology_definition_versions (
        name TEXT NOT NULL, revision INTEGER NOT NULL, definition TEXT NOT NULL,
        source TEXT NOT NULL, PRIMARY KEY(name,revision))''',
    '''CREATE TABLE IF NOT EXISTS ontology_confirmations (
        name TEXT NOT NULL, revision INTEGER NOT NULL, operation_id TEXT NOT NULL,
        actor_id TEXT NOT NULL, basis TEXT NOT NULL, PRIMARY KEY(name,revision,operation_id))''',
    '''CREATE TABLE IF NOT EXISTS ontology_instances (
        object_id TEXT PRIMARY KEY REFERENCES objects(id), project_id TEXT NOT NULL,
        definition_name TEXT NOT NULL, scope TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ontology_relations (
        relation_id TEXT PRIMARY KEY REFERENCES relations(id), project_id TEXT NOT NULL,
        definition_name TEXT NOT NULL, schema_revision INTEGER NOT NULL, scope TEXT NOT NULL,
        operation_id TEXT NOT NULL)''',
)


def _checkpoint(phase):
    """Tests inject interruption before or after the single durable commit."""


class ProjectDomain:
    domain = 'ontology'
    schema = _SCHEMA

    def __init__(self, ctx, *, authority='agent'):
        require(authority in {'user', 'agent'}, '未知可信调用来源')
        self.ctx, self.authority = ctx, authority
        self.references = DirectionReferences(ctx)
        self.db = self.references.db

    @contextmanager
    def read(self):
        with self.references._read() as conn:
            yield conn

    def receipt(self, request_id):
        with self.read() as conn:
            if conn is None or not table(conn, 'creative_domain_receipts'):
                return None
            row = conn.execute('SELECT * FROM creative_domain_receipts WHERE request_id=? AND project_id=? AND domain=?',
                               (request_id, self.ctx.project_id, self.domain)).fetchone()
            return json.loads(row['result']) if row else None

    def user_only(self):
        require(self.authority == 'user', '只有可信用户明确操作才能确认或调整约定', 'scope_violation')

    def source(self):
        return {'kind':self.authority, 'actor_id':self.ctx.requester_user_id}

    def scope(self, value):
        scope = DirectionScope.model_validate(value)
        chain, evidence = self.references._chain(scope)
        return scope.value(), chain, evidence

    async def apply(self, body):
        request = DomainCommand.model_validate(body)
        async with asset_project_lock(self.ctx.state_dir):
            try:
                return await registration_io(self._apply, request)
            except CanvasLockBusy as exc:
                raise OperationError('canvas_busy','画布正在保存，请使用原请求重试') from exc

    def _apply(self, request):
        request_hash = digest([self.domain, self.ctx.project_id, self.ctx.requester_user_id,
                               self.authority, request.model_dump(mode='json')])
        with self.read() as conn:
            replay = self._replay(conn, request, request_hash)
            if replay is not None:
                return replay
        # The project lock belongs to the original async Task. File locks and
        # SQL are entirely synchronous in this worker, with no await between them.
        with ExitStack() as locks:
            for canvas in sorted(list_canvases(self.ctx.state_dir), key=lambda c:c['canvas_id']):
                locks.enter_context(canvas_write_lock(self.ctx.state_dir, canvas['canvas_id']))
            objects = ObjectStore(self.ctx.state_dir)
            with objects.transaction() as conn:
                self.references._identity(conn)
                for statement in (*_SCHEMA, *self.schema):
                    conn.execute(statement)
                replay = self._replay(conn, request, request_hash)
                if replay is not None:
                    return replay
                operation_id = uuid4().hex
                result = {'operation_id':operation_id, 'request_id':request.request_id,
                          'status':'applied', **self.dispatch(conn, request, operation_id)}
                conn.execute('INSERT INTO creative_domain_receipts VALUES (?,?,?,?,?)',
                             (request.request_id,self.ctx.project_id,self.domain,request_hash,packed(result)))
                conn.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)',
                    (operation_id,request.request_id,request_hash,result['status'],
                     packed({'kind':f'{self.domain}.{request.action}', 'scope':{'project_id':self.ctx.project_id},
                             'request':request.model_dump(mode='json'), 'source':self.source()}),
                     packed({'operation_id':operation_id,'status':result['status'],'object_refs':[result]}),
                     self.ctx.requester_user_id))
                _checkpoint('before_commit')
            _checkpoint('after_commit')
            return result

    def _replay(self, conn, request, request_hash):
        if conn is None or not table(conn, 'operations'):
            return None
        existing = conn.execute('SELECT * FROM operations WHERE request_id=?',(request.request_id,)).fetchone()
        if not existing:
            return None
        require(existing['request_hash'] == request_hash, '请求编号已用于其他内容或调用者', 'idempotency_conflict')
        row = conn.execute('SELECT result FROM creative_domain_receipts WHERE request_id=? AND project_id=? AND domain=?',
                           (request.request_id,self.ctx.project_id,self.domain)).fetchone() if table(conn,'creative_domain_receipts') else None
        require(row is not None, '请求编号已用于其他操作', 'idempotency_conflict')
        return json.loads(row['result'])


_RESERVED = OBJECT_KINDS | {'asset_binding','asset_sources','asset','identity','variant','canvas','operation','proposal','constraint'}
_PROTECTED = {'id','kind','project_id','episode_id','canvas_id','node_id','base_asset_id','variant_id',
              'identity_id','source_name','parent_id','assetRef','asset_ref','assetMediaSelection','source_path',
              'canonical_path','output_dir','state_dir','revision','schema_revision','status','actor_id',
              'confirmed','confirmation','provenance','source_attempt_id','adoption_status'}
_PROTECTED |= {'path','url','locator','scope','definition_name'}


def validate_definition(value):
    exact(value, {'name','title','category','fields'}, {'source_kind','target_kind'})
    name = text(value['name'],64)
    require(re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) and name not in _RESERVED
            and not name.startswith(('asset_', 'creation_', 'ontology_', 'production_')), '对象定义名称是保留名称')
    text(value['title'],200)
    require(value['category'] in {'object','relation'}, '定义类别无效')
    if value['category'] == 'relation':
        for key in ('source_kind','target_kind'):
            require(isinstance(value.get(key),str) and (value[key] in OBJECT_KINDS
                    or re.fullmatch(r'custom:[a-z][a-z0-9_]{0,63}',value[key])), '关系必须声明明确端点类型')
    else:
        require(not {'source_kind','target_kind'} & value.keys(), '对象定义不可声明关系端点')
    require(isinstance(value['fields'],dict) and len(value['fields']) <= 50, '属性定义无效')
    for key, rule in value['fields'].items():
        require(re.fullmatch(r'[a-z][a-z0-9_]{0,63}',key) and key not in _PROTECTED
                and not key.endswith(('_path','_url','_id')), '属性不可占用专业字段或定位字段')
        exact(rule, {'type'}, {'required','enum','title'})
        if 'title' in rule:
            text(rule['title'],200)
        require(rule['type'] in {'string','integer','number','boolean'}, '仅支持简单属性类型')
        require(type(rule.get('required',False)) is bool, 'required必须为布尔值')
        if 'enum' in rule:
            require(isinstance(rule['enum'],list) and 0 < len(rule['enum']) <= 100, '枚举无效')
            for item in rule['enum']:
                validate_value(item, rule['type'])
    return value


def validate_value(value, kind):
    valid = {'string':isinstance(value,str) and len(value) <= 100000,
             'integer':type(value) is int, 'number':type(value) in {int,float},
             'boolean':type(value) is bool}[kind]
    require(valid and (not isinstance(value,float) or math.isfinite(value)), '属性值类型不符合已确认定义')


def validate_data(data, definition):
    require(isinstance(data,dict) and data.keys() <= definition['fields'].keys(), '包含未定义属性')
    for key, rule in definition['fields'].items():
        require(key in data or not rule.get('required',False), f'缺少必填属性：{key}')
        if key in data:
            validate_value(data[key], rule['type'])
            require('enum' not in rule or data[key] in rule['enum'], f'属性不在允许枚举内：{key}')


class Ontology(ProjectDomain):
    def object_catalog(self):
        """Trusted picker metadata, never instance text or professional media."""
        with self.read() as conn:
            if conn is None or not table(conn,'ontology_instances'):
                return []
            result=[]
            for row in conn.execute('SELECT object_id FROM ontology_instances WHERE project_id=? ORDER BY rowid',
                                    (self.ctx.project_id,)):
                obj=self._object(conn,row['object_id'])
                payload=obj['payload']
                definition=self.definition(payload['definition_name'],payload['schema_revision'],conn)
                result.append({'object_id':obj['id'],'revision':obj['revision'],'kind':obj['kind'],
                               'definition_name':payload['definition_name'],'schema_revision':payload['schema_revision'],
                               'scope':payload['scope'],'title':definition['definition']['title']})
            return result

    def relation_catalog(self):
        with self.read() as conn:
            if conn is None or not table(conn,'ontology_relations'):
                return []
            result=[]
            for row in conn.execute('''SELECT o.*,r.source_id,r.target_id FROM ontology_relations o
                    JOIN relations r ON r.id=o.relation_id WHERE o.project_id=? ORDER BY o.rowid''',
                    (self.ctx.project_id,)):
                definition=self.definition(row['definition_name'],row['schema_revision'],conn)
                result.append({'relation_id':row['relation_id'],'source_id':row['source_id'],
                    'target_id':row['target_id'],'definition_name':row['definition_name'],
                    'schema_revision':row['schema_revision'],'scope':json.loads(row['scope']),
                    'title':definition['definition']['title']})
            return result

    def catalog(self):
        with self.read() as conn:
            if conn is None or not table(conn,'ontology_definitions'):
                return []
            return [dict(row) for row in conn.execute('SELECT * FROM ontology_definitions WHERE project_id=? ORDER BY name',(self.ctx.project_id,))]

    def definition(self, name, revision=None, conn=None):
        if conn is None:
            with self.read() as connection:
                return self._definition(connection,name,revision)
        return self._definition(conn,name,revision)

    def _definition(self, conn, name, revision):
        row = conn.execute('SELECT * FROM ontology_definitions WHERE name=? AND project_id=?',(name,self.ctx.project_id)).fetchone() if conn is not None and table(conn,'ontology_definitions') else None
        require(row is not None, '定义不存在', 'scope_violation')
        version = conn.execute('SELECT * FROM ontology_definition_versions WHERE name=? AND revision=?',
                               (name,revision if revision is not None else row['current_revision'])).fetchone()
        require(version is not None, '定义版本不存在', 'scope_violation')
        return {**dict(row), 'revision':version['revision'], 'definition':json.loads(version['definition']),
                'source':json.loads(version['source']),
                'confirmations':[dict(item) for item in conn.execute(
                    'SELECT operation_id,actor_id,basis FROM ontology_confirmations WHERE name=? AND revision=? ORDER BY rowid',
                    (name,version['revision']))]}

    def active(self, conn, name, revision, category):
        integer(revision,1)
        value = self.definition(name,revision,conn)
        require(value['active_revision'] == revision, '定义尚未确认或当前定义版本已变化', 'schema_conflict')
        require(value['category'] == category, '定义类别不匹配')
        return value['definition']

    def object(self, object_id, revision=None):
        with self.read() as conn:
            obj = self._object(conn,object_id,revision)
            return {**obj['payload'], 'object_id':object_id, 'revision':obj['revision'],
                    'definition':self.definition(obj['payload']['definition_name'],obj['payload']['schema_revision'],conn)['definition']}

    def _object(self, conn, object_id, revision=None):
        row = conn.execute('SELECT * FROM ontology_instances WHERE object_id=? AND project_id=?',(object_id,self.ctx.project_id)).fetchone() if conn is not None and table(conn,'ontology_instances') else None
        require(row is not None, '自定义对象不属于此项目', 'scope_violation')
        obj = ObjectStore._read(conn,object_id,revision)
        require(obj is not None and obj['payload'].get('project_id') == self.ctx.project_id, '对象归属无效','scope_violation')
        return obj

    def relation(self, relation_id):
        with self.read() as conn:
            row = conn.execute('SELECT r.*,o.schema_revision,o.definition_name FROM relations r JOIN ontology_relations o ON r.id=o.relation_id WHERE r.id=? AND o.project_id=?',(relation_id,self.ctx.project_id)).fetchone() if conn is not None and table(conn,'ontology_relations') else None
            require(row is not None,'关系不属于此项目','scope_violation')
            return {**dict(row), 'payload':json.loads(row['payload']),
                    'definition':self.definition(row['definition_name'],row['schema_revision'],conn)['definition']}

    def endpoint(self, conn, object_id, expected_kind, scope):
        obj = ObjectStore._read(conn,object_id,None)
        require(obj is not None and obj['kind'] == expected_kind, '关系端点类型或项目不匹配','scope_violation')
        if expected_kind.startswith('custom:'):
            obj = self._object(conn,object_id)
            owner = obj['payload']['scope']
            require(owner == scope or owner == {'level':'project'}, '关系端点超出操作范围','scope_violation')
        elif expected_kind in {'character','scene'}:
            require(table(conn,'asset_sources') and conn.execute('SELECT 1 FROM asset_sources WHERE object_id=?',(object_id,)).fetchone(), '资产端点必须来自可信注册','scope_violation')
        elif expected_kind == 'episode':
            workspace=next((w for w in list_canvases(self.ctx.state_dir) if w['episode_id']==object_id and w['status']=='applied'),None)
            require(workspace is not None, '集端点项目不匹配','scope_violation')
            self.scope({'level':'canvas','canvas_id':workspace['canvas_id']})
            require(scope.get('canvas_id') == workspace['canvas_id'], '集端点超出范围','scope_violation')
        else:
            require(obj['payload'].get('project_id') == self.ctx.project_id or
                    (scope.get('canvas_id') and obj['payload'].get('canvas_id') == scope['canvas_id']),
                    '端点缺少可信项目和范围证据','scope_violation')

    def dispatch(self, conn, request, operation_id):
        p, action = request.payload, request.action
        if action == 'definition.draft':
            exact(p, {'definition','expected_revision'})
            definition = validate_definition(p['definition']); expected = integer(p['expected_revision'])
            row=conn.execute('SELECT * FROM ontology_definitions WHERE name=?',(definition['name'],)).fetchone()
            require((row['current_revision'] if row else 0) == expected, '定义已被修改','revision_conflict')
            require(not row or row['category'] == definition['category'], '定义类别不可改变')
            revision=expected+1
            conn.execute('INSERT INTO ontology_definitions VALUES (?,?,?,?,0) ON CONFLICT(name) DO UPDATE SET current_revision=excluded.current_revision',
                         (definition['name'],self.ctx.project_id,definition['category'],revision))
            conn.execute('INSERT INTO ontology_definition_versions VALUES (?,?,?,?)',(definition['name'],revision,packed(definition),packed(self.source())))
            return {'name':definition['name'],'revision':revision,'state':'draft'}
        if action == 'definition.confirm':
            self.user_only();exact(p, {'name','revision','expected_active_revision','basis'})
            basis=text(p['basis']); value=self.definition(p['name'],integer(p['revision'],1),conn)
            require(value['active_revision'] == integer(p['expected_active_revision']), '启用定义已变化','revision_conflict')
            if value['category']=='relation':
                for endpoint in ('source_kind','target_kind'):
                    kind=value['definition'][endpoint]
                    if kind.startswith('custom:'):
                        target=self.definition(kind.removeprefix('custom:'),conn=conn)
                        require(target['category']=='object' and target['active_revision']>0, '关系端类型尚未确认','schema_conflict')
            conn.execute('UPDATE ontology_definitions SET active_revision=? WHERE name=?',(p['revision'],p['name']))
            conn.execute('INSERT INTO ontology_confirmations VALUES (?,?,?,?,?)',(p['name'],p['revision'],operation_id,self.ctx.requester_user_id,basis))
            return {'name':p['name'],'revision':p['revision'],'state':'confirmed'}
        if action in {'object.create','object.update'}:
            creating=action=='object.create'
            exact(p, {'definition','schema_revision','scope','data'} if creating else {'object_id','schema_revision','expected_revision','data'})
            previous=None if creating else self._object(conn,p['object_id'])
            name=p['definition'] if creating else previous['payload']['definition_name']
            definition=self.active(conn,name,p['schema_revision'],'object');validate_data(p['data'],definition)
            scope=self.scope(p['scope'] if creating else previous['payload']['scope'])[0]
            if not creating:
                require(previous['revision']==integer(p['expected_revision'],1),'对象已被修改','revision_conflict')
            from .creative_constraints import CreativeConstraints
            conflict=CreativeConstraints(self.ctx).guard_object(conn,'custom:'+name,p['data'],scope,
                previous['id'] if previous else None,operation_id,request)
            if conflict:
                return conflict
            object_id=uuid4().hex if creating else p['object_id'];revision=1 if creating else previous['revision']+1
            payload={'project_id':self.ctx.project_id,'scope':scope,'definition_name':name,'schema_revision':p['schema_revision'],'data':p['data'],'source':self.source()}
            if creating:
                conn.execute('INSERT INTO objects (id,kind,current_revision) VALUES (?,?,1)',(object_id,'custom:'+name))
                conn.execute('INSERT INTO ontology_instances VALUES (?,?,?,?)',(object_id,self.ctx.project_id,name,packed(scope)))
            else:
                conn.execute('UPDATE objects SET current_revision=? WHERE id=?',(revision,object_id))
            conn.execute('INSERT INTO object_versions VALUES (?,?,?,?,?)',(object_id,revision,packed(payload),self.ctx.requester_user_id,datetime.now(timezone.utc).isoformat()))
            return {'object_id':object_id,'revision':revision,'schema_revision':p['schema_revision']}
        if action == 'relation.create':
            exact(p,{'definition','schema_revision','scope','source_id','target_id','data'})
            definition=self.active(conn,p['definition'],p['schema_revision'],'relation');validate_data(p['data'],definition)
            scope=self.scope(p['scope'])[0]
            self.endpoint(conn,p['source_id'],definition['source_kind'],scope)
            self.endpoint(conn,p['target_id'],definition['target_kind'],scope)
            relation_id=uuid4().hex
            conn.execute('INSERT INTO relations VALUES (?,?,?,?,?)',(relation_id,p['source_id'],p['target_id'],'custom:'+p['definition'],packed({'data':p['data'],'scope':scope,'schema_revision':p['schema_revision']})))
            conn.execute('INSERT INTO ontology_relations VALUES (?,?,?,?,?,?)',(relation_id,self.ctx.project_id,p['definition'],p['schema_revision'],packed(scope),operation_id))
            return {'relation_id':relation_id,'schema_revision':p['schema_revision']}
        raise OperationError('invalid_contract','不支持的定义操作')
