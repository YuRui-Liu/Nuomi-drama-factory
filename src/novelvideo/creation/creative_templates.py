"""Portable, fixed project templates with explicit draft installation."""
import json
import re
from uuid import uuid4

from novelvideo.production_workflow.coordination import asset_project_lock
from .agent_profiles import AgentProfiles
from .asset_adapter import registration_io
from .asset_reference import table
from .creative_constraints import CreativeConstraints, _SCHEMA as CONSTRAINT_SCHEMA
from .library_references import digest, packed
from .ontology import (Ontology, DomainCommand, _SCHEMA as ONTOLOGY_SCHEMA,
                       exact, integer, require, text, validate_definition)
from .personal_library import ProjectLibrary, _ADOPTION_SCHEMA


def portable(value):
    """Examples are simple values, not project media/credential locators."""
    if isinstance(value,dict):
        for key,item in value.items():
            require(not re.search(r'(password|secret|token|api_key|credential)',key,re.I),
                    '示例包含不可移植凭据字段','unsupported_reference')
            portable(item)
    elif isinstance(value,list):
        for item in value:
            portable(item)
    elif isinstance(value,str):
        require(not re.search(r'(^|\s)(/|~[/\\]|\.\.[/\\]|assets/|director_worlds/|[A-Za-z]:\\|https?://|file:|s3:|data:)',value),
                '示例包含路径或外部引用，请移除后发布','unsupported_reference')


class CreativeTemplates(ProjectLibrary):
    domain='creative_templates'
    kind='template'
    editable=frozenset({'name','description'})
    schema=(*_ADOPTION_SCHEMA,*ONTOLOGY_SCHEMA,*CONSTRAINT_SCHEMA)

    def preview(self,selection):
        exact(selection,{'name','description','definitions','constraints','agents','examples','relations'})
        text(selection['name'],200);text(selection['description'],12000)
        for key in ('definitions','constraints','agents','examples','relations'):
            require(isinstance(selection[key],list) and len(selection[key])<=100,'模板选择列表无效')
        ontology=Ontology(self.ctx);constraints=CreativeConstraints(self.ctx)
        definitions={};examples=[];relations=[];agreements=[];agents=[]
        with self.read() as conn:
            def definition(name,revision):
                value=ontology._definition(conn,name,integer(revision,1))
                key=f'{name}@{revision}'
                definitions[key]={'key':key,'definition':value['definition'],
                    'origin':{'project_id':self.ctx.project_id,'name':name,'revision':revision},
                    'was_confirmed':bool(value['confirmations'])}
                return key
            for ref in selection['definitions']:
                exact(ref,{'name','revision'});definition(ref['name'],ref['revision'])
            for ref in selection['examples']:
                exact(ref,{'id','revision'})
                obj=ontology._object(conn,ref['id'],integer(ref['revision'],1))
                payload=obj['payload'];portable(payload['data'])
                key=definition(payload['definition_name'],payload['schema_revision'])
                require(not any(e['origin']['id']==ref['id'] for e in examples),'同一示例不能选择多个版本')
                examples.append({'key':ref['id'],'definition_key':key,'data':payload['data'],
                    'origin':{'project_id':self.ctx.project_id,**ref}})
            selected_ids={e['key'] for e in examples}
            for identifier in selection['relations']:
                text(identifier,100)
                row=conn.execute('''SELECT r.*,o.definition_name,o.schema_revision FROM relations r
                    JOIN ontology_relations o ON r.id=o.relation_id WHERE r.id=? AND o.project_id=?''',
                    (identifier,self.ctx.project_id)).fetchone() if conn and table(conn,'ontology_relations') else None
                require(row is not None,'仅支持明确选择的自定义关系示例','unsupported_reference')
                require(row['source_id'] in selected_ids and row['target_id'] in selected_ids,
                        '关系端点须同时明确选择为自定义示例','unsupported_reference')
                key=definition(row['definition_name'],row['schema_revision'])
                data=json.loads(row['payload'])['data'];portable(data)
                relations.append({'key':identifier,'definition_key':key,'source_key':row['source_id'],
                    'target_key':row['target_id'],'data':data,'origin':{'project_id':self.ctx.project_id,'id':identifier}})
            for ref in selection['constraints']:
                exact(ref,{'id','revision'})
                record=constraints._record(conn,ref['id'],integer(ref['revision'],1))
                require(record['scope']=={'level':'project'},'模板默认约定仅支持项目范围，请先显式整理','unsupported_reference')
                portable(record['value'])
                item={'key':record['key'],'value':record['value'],'suggested_level':record['level'],
                    'citation':record['citation'],'origin':{'project_id':self.ctx.project_id,
                    'constraint_id':ref['id'],'revision':ref['revision'],'source':record['source']}}
                if record.get('rule'):
                    rule=record['rule']
                    item['rule_key']=definition(rule['kind'].removeprefix('custom:'),rule['schema_revision'])
                    item['rule']={k:rule[k] for k in ('field','operator')}
                agreements.append(item)
            for ref in selection['agents']:
                exact(ref,{'id','revision'})
                adopted=AgentProfiles(self.ctx,self.library)._adoption(conn,ref['id'],integer(ref['revision'],1))
                agents.append({'configuration':adopted['effective'],
                    'origin':{'project_id':self.ctx.project_id,'adoption_id':ref['id'],'revision':ref['revision']}})
            # Relation declarations need all custom endpoint schemas. A version
            # must be explicit if multiple histories of that name are selected.
            for item in list(definitions.values()):
                d=item['definition']
                if d['category']=='relation':
                    for field in ('source_kind','target_kind'):
                        kind=d[field]
                        require(kind.startswith('custom:'),'模板关系只支持可移植自定义端点','unsupported_reference')
                        name=kind.removeprefix('custom:')
                        matches=[v for v in definitions.values() if v['definition']['name']==name]
                        if not matches:
                            current=ontology._definition(conn,name,None)
                            definition(name,current['active_revision'] or current['revision'])
        frozen={'name':selection['name'],'description':selection['description'],
                'definitions':list(definitions.values()),'constraints':agreements,'agents':agents,
                'examples':examples,'relations':relations}
        return {'configuration':frozen,'proof':digest(frozen),
                'supported_examples':['custom_object','custom_relation'],
                'excluded_by_default':['script','media','run_history','unselected_objects'],
                'adoption_policy':'definitions_and_agreements_remain_draft'}

    async def save(self,body):
        def build(selection,proof):
            preview=self.preview(selection)
            require(preview['proof']==proof,'模板发布预览已变化','source_conflict')
            return preview['configuration']
        async with asset_project_lock(self.ctx.state_dir):
            return await registration_io(self.library.apply,self.kind,body,build,self.build_patch)

    def dependencies(self,config):
        return {k:config[k] for k in ('definitions','constraints','agents')}

    def _plan(self,conn,fixed,names):
        require(isinstance(names,dict),'定义映射无效')
        source=fixed['configuration']['definitions'];keys={d['key'] for d in source}
        original_names={d['definition']['name'] for d in source}
        require(set(names)<=keys|original_names,'定义映射包含未知来源')
        mapping={d['key']:names.get(d['key'],names.get(d['definition']['name'],d['definition']['name'])) for d in source}
        require(len(set(mapping.values()))==len(mapping),'多个历史定义须显式映射不同名称','template_definition_conflict')
        conflicts=[];definitions=[]
        for item in source:
            d={**item['definition'],'name':mapping[item['key']]}
            if d['category']=='relation':
                for field in ('source_kind','target_kind'):
                    old=d[field].removeprefix('custom:')
                    targets=[v for k,v in mapping.items() if k.rsplit('@',1)[0]==old]
                    require(len(targets)==1,'关系端点历史版本有歧义，请拆分模板','unsupported_reference')
                    d[field]='custom:'+targets[0]
            validate_definition(d)
            existing=conn.execute('SELECT current_revision,active_revision FROM ontology_definitions WHERE name=? AND project_id=?',
                (d['name'],self.ctx.project_id)).fetchone() if conn and table(conn,'ontology_definitions') else None
            expected=existing['current_revision'] if existing else 0
            same=False
            if existing:
                old=Ontology(self.ctx)._definition(conn,d['name'],expected)
                same=old['definition']==d
                if not same:
                    conflicts.append({'source_key':item['key'],'target_name':d['name'],'existing_revision':expected})
            definitions.append({'key':item['key'],'definition':d,'expected_revision':expected,
                                'revision':expected if same else expected+1,'reuse':same,
                                'active_revision':existing['active_revision'] if existing else 0})
        plan={'ref':{'id':fixed['id'],'revision':fixed['revision']},'fixed_proof':fixed['proof'],
              'definition_names':names,'definitions':definitions,'conflicts':conflicts}
        plan['proof']=digest(plan)
        return plan

    def adoption_preview(self,ref,definition_names=None):
        fixed=self.library.version(self.kind,ref)
        with self.read() as conn:
            return self._plan(conn,fixed,definition_names or {})

    def _upgrade_preview(self,old,new,definition_names=None):
        preview=super()._upgrade_preview(old,new,definition_names)
        with self.read() as conn:
            preview['installation']=self._plan(conn,new,definition_names or {})
            preview['existing_constraint_drafts']=[CreativeConstraints(self.ctx)._record(conn,i)
                                                   for i in old.get('constraint_drafts',[])]
        preview['notice']='升级保留原项目约定与已实例化示例；新内容以独立草案加入，需明确确认后生效'
        preview['proof']=digest({k:v for k,v in preview.items() if k!='proof'})
        return preview

    def validate_adoption_proof(self,conn,fixed,payload):
        plan=self._plan(conn,fixed,payload.get('definition_names',{}))
        require(plan['proof']==payload['proof'],'模板采用预览已变化','source_conflict')
        require(not plan['conflicts'],'同名定义内容不同，请显式重命名映射','template_definition_conflict',conflicts=plan['conflicts'])

    def install(self,conn,content,operation_id,payload):
        plan=self._plan(conn,content['fixed'],payload.get('definition_names',{}))
        require(not plan['conflicts'],'同名定义内容不同，请显式重命名映射','template_definition_conflict',conflicts=plan['conflicts'])
        ontology=Ontology(self.ctx,authority='user');mapping={}
        for d in plan['definitions']:
            if not d['reuse']:
                ontology.dispatch(conn,DomainCommand(request_id='internal',action='definition.draft',payload={
                    'definition':d['definition'],'expected_revision':d['expected_revision']}),operation_id)
            mapping[d['key']]={'name':d['definition']['name'],'revision':d['revision']}
        config=content['fixed']['configuration'];constraint_ids=[]
        for record in config['constraints']:
            identifier=uuid4().hex
            item={'constraint_id':identifier,'revision':1,'key':record['key'],'value':record['value'],
                  'level':'draft','scope':{'level':'project'},'citation':record['citation'],
                  'source':{'kind':'template','actor_id':self.ctx.requester_user_id},'changed_by':self.source(),
                  'confirmation':None,'suggested_level':record['suggested_level'],'origin':record['origin']}
            if 'rule_key' in record:
                target=mapping[record['rule_key']]
                item['rule']={**record['rule'],'kind':'custom:'+target['name'],'schema_revision':target['revision']}
            conn.execute('INSERT INTO creative_constraints VALUES (?,?,1)',(identifier,self.ctx.project_id))
            conn.execute('INSERT INTO creative_constraint_versions VALUES (?,1,?,?)',(identifier,packed(item),operation_id))
            constraint_ids.append(identifier)
        ids={e['key']:uuid4().hex for e in config['examples']}
        previous={k:content[k] for k in ('constraint_drafts','example_drafts','relation_drafts','instantiated') if k in content}
        content={**content,'definition_mapping':mapping,'constraint_drafts':constraint_ids,
                 'previous_installations':[*content.get('previous_installations',[]),*([previous] if previous else [])],
                 'example_drafts':[{'id':ids[e['key']],'definition':mapping[e['definition_key']],
                     'data':e['data'],'origin':e['origin'],'state':'draft'} for e in config['examples']],
                 'relation_drafts':[{'id':uuid4().hex,'definition':mapping[r['definition_key']],
                     'source_id':ids[r['source_key']],'target_id':ids[r['target_key']],
                     'data':r['data'],'origin':r['origin'],'state':'draft'} for r in config['relations']]}
        content.pop('instantiated',None)
        return content

    def _activation_preview(self,conn,content):
        ontology=Ontology(self.ctx)
        values=[ontology._definition(conn,d['name'],d['revision']) for d in content['definition_mapping'].values()]
        preview={'adoption_id':content['adoption_id'],'expected_revision':content['revision'],
                 'definitions':values,'examples':content['example_drafts'],'relations':content['relation_drafts'],
                 'notice':'仅确认所列定义并实例化示例；默认约定保持草案，不启动生成'}
        preview['proof']=digest(preview)
        return preview

    def activation_preview(self,identifier):
        with self.read() as conn:
            return self._activation_preview(conn,self._adoption(conn,identifier))

    def dispatch(self,conn,request,operation_id):
        if request.action!='activate_examples':
            return super().dispatch(conn,request,operation_id)
        p=request.payload
        exact(p,{'adoption_id','expected_revision','definitions','basis','proof'})
        old=self._adoption(conn,p['adoption_id'])
        require(old['revision']==integer(p['expected_revision'],1),'项目采用已变化','revision_conflict')
        require(not old.get('instantiated'),'示例已实例化，请使用原请求恢复','revision_conflict')
        require(self._activation_preview(conn,old)['proof']==p['proof'],'定义确认预览已变化','schema_conflict')
        text(p['basis'],4000)
        require(isinstance(p['definitions'],list),'请明确选择要启用的定义')
        expected=list(old['definition_mapping'].values())
        require(sorted(packed(d) for d in p['definitions'])==sorted(packed(d) for d in expected),
                '须明确确认示例及关系所需的全部固定定义','schema_conflict')
        ontology=Ontology(self.ctx,authority='user')
        values=[ontology._definition(conn,d['name'],d['revision']) for d in expected]
        # All confirmations and instances share the outer domain transaction.
        for d in sorted(values,key=lambda x:x['category']=='relation'):
            require(d['current_revision']==d['revision'],'待确认定义已变化','schema_conflict')
            ontology.dispatch(conn,DomainCommand(request_id='internal',action='definition.confirm',payload={
                'name':d['name'],'revision':d['revision'],'expected_active_revision':d['active_revision'],'basis':p['basis']}),operation_id)
        objects={};relations={}
        for draft in old['example_drafts']:
            d=draft['definition']
            result=ontology.dispatch(conn,DomainCommand(request_id='internal',action='object.create',payload={
                'definition':d['name'],'schema_revision':d['revision'],'scope':{'level':'project'},'data':draft['data']}),operation_id)
            require(result.get('status')!='conflict','示例与项目硬约定冲突','creative_constraint_conflict',
                    hard_refs=result.get('exception_proposal',{}).get('hard_refs',[]))
            objects[draft['id']]=result['object_id']
        for draft in old['relation_drafts']:
            d=draft['definition']
            result=ontology.dispatch(conn,DomainCommand(request_id='internal',action='relation.create',payload={
                'definition':d['name'],'schema_revision':d['revision'],'scope':{'level':'project'},
                'source_id':objects[draft['source_id']],'target_id':objects[draft['target_id']],'data':draft['data']}),operation_id)
            relations[draft['id']]=result['relation_id']
        content={**old,'revision':old['revision']+1,'instantiated':{'objects':objects,'relations':relations,
            'operation_id':operation_id,'basis':p['basis']}}
        conn.execute('UPDATE creative_adoptions SET current_revision=? WHERE id=?',(content['revision'],content['adoption_id']))
        conn.execute('INSERT INTO creative_adoption_versions VALUES (?,?,?)',(content['adoption_id'],content['revision'],packed(content)))
        return {'adoption_id':content['adoption_id'],'revision':content['revision'],'instantiated':content['instantiated']}
