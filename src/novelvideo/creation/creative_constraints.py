"""Versioned creative agreements: drafts, scoped preferences and user-locked facts."""
import json
from uuid import uuid4

from .asset_reference import table
from .library_references import digest, packed
from .ontology import Ontology, ProjectDomain, exact, integer, require, text, validate_data
from .store import ObjectStore


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS creative_constraints (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, current_revision INTEGER NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS creative_constraint_versions (
        constraint_id TEXT NOT NULL REFERENCES creative_constraints(id), revision INTEGER NOT NULL,
        payload TEXT NOT NULL, operation_id TEXT NOT NULL,
        PRIMARY KEY(constraint_id,revision))''',
    '''CREATE TABLE IF NOT EXISTS creative_exception_proposals (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL, operation_id TEXT NOT NULL)''',
)


class CreativeConstraints(ProjectDomain):
    domain = 'constraints'
    schema = _SCHEMA

    def scope(self, value):
        if isinstance(value,dict) and value.get('level') in {'object','episode'}:
            level=value['level'];field='object_id' if level=='object' else 'episode_id'
            exact(value, {'level',field})
            with self.read() as conn:
                obj=ObjectStore._read(conn,value[field],None) if conn is not None and table(conn,'objects') else None
                require(obj is not None,'约定对象不属于当前项目','scope_violation')
                if level=='episode':
                    from .canvas_workspaces import list_canvases
                    workspace=next((w for w in list_canvases(self.ctx.state_dir) if w['episode_id']==obj['id'] and w['status']=='applied'),None)
                    require(obj['kind']=='episode' and workspace is not None,'集归属无效','scope_violation')
                    super().scope({'level':'canvas','canvas_id':workspace['canvas_id']})
                elif obj['kind'] in {'character','scene'}:
                    require(table(conn,'asset_sources') and conn.execute('SELECT 1 FROM asset_sources WHERE object_id=?',(obj['id'],)).fetchone(),'必须引用已注册角色或场景','scope_violation')
                else:
                    require(obj['kind'].startswith('custom:') and obj['payload'].get('project_id')==self.ctx.project_id,'约定对象无可信归属','scope_violation')
            chain=self.scope(obj['payload']['scope'])[1] if obj['kind'].startswith('custom:') else [{'level':'project'}]
            return value,[*chain,value],{'project_id':self.ctx.project_id,'object_id':obj['id'],'revision':obj['revision']}
        scope,chain,evidence=super().scope(value)
        if scope.get('canvas_id'):
            from .canvas_workspaces import list_canvases
            workspace=next(w for w in list_canvases(self.ctx.state_dir) if w['canvas_id']==scope['canvas_id'])
            if workspace['episode_id']:
                chain.insert(1,{'level':'episode','episode_id':workspace['episode_id']})
        return scope,chain,evidence

    def record(self, constraint_id, revision=None):
        with self.read() as conn:
            return self._record(conn,constraint_id,revision)

    def _record(self, conn, constraint_id, revision=None):
        row=conn.execute('''SELECT v.payload FROM creative_constraints c JOIN creative_constraint_versions v
            ON v.constraint_id=c.id AND v.revision=COALESCE(?,c.current_revision)
            WHERE c.id=? AND c.project_id=?''',(revision,constraint_id,self.ctx.project_id)).fetchone() if conn is not None and table(conn,'creative_constraints') else None
        require(row is not None,'约定不属于此项目或版本不存在','scope_violation')
        return json.loads(row['payload'])

    def _all(self, conn):
        if conn is None or not table(conn,'creative_constraints'):
            return []
        return [json.loads(row[0]) for row in conn.execute('''SELECT v.payload FROM creative_constraints c
            JOIN creative_constraint_versions v ON v.constraint_id=c.id AND v.revision=c.current_revision
            WHERE c.project_id=? ORDER BY c.rowid''',(self.ctx.project_id,))]

    def catalog(self):
        with self.read() as conn:
            return [self._availability(r) for r in self._all(conn)]

    def _missing_scope(self, scope):
        """Recognize absent persisted targets; never suppress validation/DB errors."""
        if scope.get('level')=='episode':
            exact(scope,{'level','episode_id'})
            with self.read() as conn:
                obj=ObjectStore._read(conn,scope['episode_id'],None) if conn is not None and table(conn,'objects') else None
                require(obj is not None and obj['kind']=='episode','集归属无效','scope_violation')
                row=conn.execute('SELECT canvas_id FROM episode_canvases WHERE episode_id=?',(obj['id'],)).fetchone()
                require(row is not None,'集缺少可信画布映射','scope_violation')
            return self._missing_scope({'level':'canvas','canvas_id':row['canvas_id']})
        if scope.get('level')=='object':
            exact(scope,{'level','object_id'})
            with self.read() as conn:
                obj=ObjectStore._read(conn,scope['object_id'],None) if conn is not None and table(conn,'objects') else None
                require(obj is not None,'约定对象不属于当前项目','scope_violation')
                if obj['kind'].startswith('custom:'):
                    require(obj['payload'].get('project_id')==self.ctx.project_id,'约定对象无可信归属','scope_violation')
                    owner=obj['payload']['scope']
                    # Custom objects can only be owned by a validated graph scope,
                    # never another object. Reject damaged/cyclic ownership.
                    from .library_references import DirectionScope
                    DirectionScope.model_validate(owner)
                    row=conn.execute('SELECT project_id,scope FROM ontology_instances WHERE object_id=?',(obj['id'],)).fetchone()
                    require(row is not None and row['project_id']==self.ctx.project_id
                            and json.loads(row['scope'])==owner,'对象所属范围记录不一致','scope_violation')
                    return self._missing_scope(owner)
            return None
        if scope.get('level') not in {'canvas','node','group'}:
            return None
        from .canvas_workspaces import list_canvases
        from ..freezone.canvas_store import read_canvas
        # read() verifies project storage identity even when a target is absent.
        with self.read():
            pass
        workspace=next((w for w in list_canvases(self.ctx.state_dir)
                        if w['canvas_id']==scope['canvas_id'] and w['status']=='applied'),None)
        if workspace is None:
            return 'canvas_unavailable'
        canvas=read_canvas(self.ctx.state_dir,scope['canvas_id'])
        if canvas is None:
            return 'canvas_unavailable'
        require(canvas.get('project_id')==self.ctx.project_id,'画布项目归属无效','scope_violation')
        nodes=canvas.get('nodes',[])
        require(len({n['id'] for n in nodes})==len(nodes),'画布包含重复节点','scope_violation')
        if scope['level']!='canvas':
            target=next((n for n in nodes if n['id']==scope.get('node_id',scope.get('group_id'))),None)
            if target is None:
                return 'target_removed'
            if scope['level']=='group' and target.get('type')!='groupNode':
                return 'group_removed'
        return None

    def _availability(self, record):
        reason=self._missing_scope(record['scope'])
        if reason is None:
            self.scope(record['scope'])
        return {**record,'scope_status':'inactive' if reason else 'valid','scope_reason':reason}

    def resolve(self, scope):
        scope,chain,evidence=self.scope(scope)
        with self.read() as conn:
            return self._resolve(self._all(conn),scope,chain,evidence)

    def _resolve(self, records, scope, chain, evidence):
        keys=[packed(item) for item in chain]
        applicable=[r for r in records if packed(r['scope']) in keys]
        effective={};conflicts=[]
        for record in sorted(applicable,key=lambda r:keys.index(packed(r['scope']))):
            if record['level']=='draft':
                continue
            previous=effective.get(record['key'])
            if previous and previous['level']=='hard' and packed(previous['value'])!=packed(record['value']):
                conflicts.append({'key':record['key'],'hard_ref':self.ref(previous),'conflicting_ref':self.ref(record)})
            elif not previous or previous['level']!='hard':
                effective[record['key']]=record
        return {'scope':scope,'scope_proof':digest(evidence),'chain':chain,'effective':effective,
                'drafts':[r for r in applicable if r['level']=='draft'],'conflicts':conflicts,
                'not_evaluated':'对象约定仅在明确引用该对象的上下文中生效，不按画布位置推测角色或场景归属'}

    @staticmethod
    def ref(record):
        return {'constraint_id':record['constraint_id'],'revision':record['revision']}

    def proposal(self, proposal_id):
        with self.read() as conn:
            row=conn.execute('SELECT payload FROM creative_exception_proposals WHERE id=? AND project_id=?',(proposal_id,self.ctx.project_id)).fetchone() if conn is not None and table(conn,'creative_exception_proposals') else None
            require(row is not None,'例外提案不存在','scope_violation')
            return json.loads(row[0])

    def _conflict(self, conn, candidate, operation_id, request):
        if candidate['level']=='draft':
            return None
        _,chain,_=self.scope(candidate['scope'])
        conflicts=[]
        for existing in self._all(conn):
            same_rule = existing.get('rule') and candidate.get('rule') and all(existing['rule'][k]==candidate['rule'][k] for k in ('kind','field','operator'))
            if existing['constraint_id']==candidate['constraint_id'] or existing['level']=='draft' or (existing['key']!=candidate['key'] and not same_rule):
                continue
            if self._missing_scope(existing['scope']) is not None:
                continue
            _,other_chain,_=self.scope(existing['scope'])
            overlaps=existing['scope'] in chain or candidate['scope'] in other_chain
            locks_existing = candidate['level']=='hard' and candidate['scope'] in other_chain
            if overlaps and packed(existing['value'])!=packed(candidate['value']) and (existing['level']=='hard' or locks_existing or existing['scope']==candidate['scope']):
                conflicts.append(existing)
        if not conflicts:
            return None
        return self._proposal(conn,conflicts,candidate,candidate['scope'],operation_id,request)

    def _proposal(self,conn,conflicts,requested,scope,operation_id,request):
        proposal={'proposal_id':uuid4().hex,'state':'awaiting_user_decision',
                  'hard_refs':[self.ref(r) for r in conflicts if r['level']=='hard'],
                  'conflicting_refs':[self.ref(r) for r in conflicts], 'scope':scope,
                  'requested':requested,'input_hash':digest(request.model_dump(mode='json')),
                  'operation_id':operation_id, 'allowed_next_steps':['revise_with_cas','propose_scoped_exception'],
                  'approval_available':False}
        conn.execute('INSERT INTO creative_exception_proposals VALUES (?,?,?,?)',
                     (proposal['proposal_id'],self.ctx.project_id,packed(proposal),operation_id))
        return {'status':'conflict','code':'creative_constraint_conflict','exception_proposal':proposal}

    def validate_rule(self,conn,rule,value):
        exact(rule,{'kind','field','operator','schema_revision'})
        require(isinstance(rule['kind'],str) and rule['kind'].startswith('custom:') and rule['operator']=='equals',
                '仅支持自定义对象字段的equals声明规则')
        definition=Ontology(self.ctx).active(conn,rule['kind'].removeprefix('custom:'),rule['schema_revision'],'object')
        require(rule['field'] in definition['fields'],'规则字段不存在')
        validate_data({rule['field']:value},{'fields':{rule['field']:definition['fields'][rule['field']]}})

    def guard_object(self,conn,kind,data,scope,object_id,operation_id,request):
        if not table(conn,'creative_constraints'):
            return None
        _,chain,_=self.scope(scope)
        if object_id:
            chain.append({'level':'object','object_id':object_id})
        violated=[r for r in self._all(conn) if r['level']=='hard' and r['scope'] in chain
                  and r.get('rule',{}).get('kind')==kind
                  and (r['rule']['field'] not in data or packed(data[r['rule']['field']])!=packed(r['value']))]
        if violated:
            return self._proposal(conn,violated,request.model_dump(mode='json'),scope,operation_id,request)

    def dispatch(self, conn, request, operation_id):
        p=request.payload;action=request.action
        if action=='constraint.save':
            exact(p, {'key','value','level','scope','citation'}, {'constraint_id','expected_revision','rule'})
            text(p['key'],128);text(p['citation'],4000)
            require(p['level'] in {'draft','preference'},'硬约束必须通过用户明确确认')
            if p['level']=='preference':
                self.user_only()
            require(type(p['value']) in {str,int,float,bool} and len(packed(p['value']))<=10000,'约定仅支持简单声明值')
            scope=self.scope(p['scope'])[0]
            if 'rule' in p:
                self.validate_rule(conn,p['rule'],p['value'])
            previous=None
            if 'constraint_id' in p:
                previous=self._record(conn,p['constraint_id'])
                require(previous['revision']==integer(p.get('expected_revision'),1),'约定已被修改','revision_conflict')
                require(previous['level']!='hard','硬约束须通过明确确认操作修订或降级','confirmation_required')
                # Origin is historical evidence, not continuing edit authority.
                # User confirmation/editing retains ownership even after demotion.
                require(self.authority=='user' or (
                    previous['level']=='draft'
                    and previous['source']==self.source()
                    and previous.get('changed_by')==self.source()
                    and previous.get('confirmation') is None
                ),'仅可修改自身尚未经用户确认或修改的草案','scope_violation')
            else:
                require('expected_revision' not in p,'新约定不能提供对象版本')
            candidate={'constraint_id':previous['constraint_id'] if previous else uuid4().hex,
                       'revision':previous['revision']+1 if previous else 1,
                       'key':p['key'],'value':p['value'],'level':p['level'],'scope':scope,
                       'source':previous['source'] if previous else self.source(),
                       'citation':p['citation'],'changed_by':self.source(),'confirmation':None,
                       **({'rule':p['rule']} if 'rule' in p else {})}
        elif action=='constraint.confirm':
            self.user_only()
            exact(p,{'constraint_id','expected_revision','level','basis'}, {'value'})
            previous=self._record(conn,p['constraint_id'])
            require(previous['revision']==integer(p['expected_revision'],1),'约定已被修改','revision_conflict')
            require(p['level'] in {'hard','preference','draft'},'约定级别无效')
            text(p['basis'],4000)
            # Only an explicit user demotion without content changes can archive
            # a historical agreement whose target has disappeared.
            archival=(p['level']=='draft' and 'value' not in p
                      and self._missing_scope(previous['scope']) is not None)
            if not archival:
                self.scope(previous['scope'])
            value=p.get('value',previous['value'])
            require(type(value) in {str,int,float,bool} and len(packed(value))<=10000,'约定仅支持简单声明值')
            if 'rule' in previous and (p['level']=='hard' or 'value' in p):
                self.validate_rule(conn,previous['rule'],value)
            candidate={**previous,'revision':previous['revision']+1,'level':p['level'],'value':value,
                       'changed_by':self.source(),'confirmation':{'actor_id':self.ctx.requester_user_id,
                       'basis':p['basis'],'previous_revision':previous['revision'],'operation_id':operation_id}}
        else:
            require(False,'未知约定操作')
        conflict=self._conflict(conn,candidate,operation_id,request)
        if conflict:
            return conflict
        if previous is None:
            conn.execute('INSERT INTO creative_constraints VALUES (?,?,1)',(candidate['constraint_id'],self.ctx.project_id))
        else:
            conn.execute('UPDATE creative_constraints SET current_revision=? WHERE id=?',(candidate['revision'],candidate['constraint_id']))
        conn.execute('INSERT INTO creative_constraint_versions VALUES (?,?,?,?)',
                     (candidate['constraint_id'],candidate['revision'],packed(candidate),operation_id))
        return {'constraint_id':candidate['constraint_id'],'revision':candidate['revision'],'level':candidate['level']}
