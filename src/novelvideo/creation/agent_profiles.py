"""User-owned Agent configuration; no executor or implicit tool grants."""
import json
from urllib.parse import urlsplit

from .asset_adapter import registration_io
from novelvideo.production_workflow.coordination import asset_project_lock
from .asset_reference import readonly, table
from .library_references import digest
from .ontology import exact, integer, require, text
from .personal_library import ProjectLibrary


CAPABILITIES=(
    {'id':'context.read','title':'读取已授权创作上下文','runtime_permission':'requires_scoped_read'},
    {'id':'proposal.create','title':'提出创作修改建议','runtime_permission':'proposal_only'},
    {'id':'ontology.draft','title':'提出对象定义草案','runtime_permission':'draft_only'},
    {'id':'media.propose','title':'提出媒体生成方案','runtime_permission':'requires_separate_execution_authorization'},
    {'id':'node.patch','title':'提出节点局部修改','runtime_permission':'requires_scoped_edit_authorization'},
    {'id':'node.run','title':'申请受限节点操作','runtime_permission':'requires_scoped_execution_authorization'},
)
LEGACY_CAPABILITIES=CAPABILITIES[:4]
STUDIO_TEXT_FIELDS=frozenset({'description','instructions','method','notes','intent'})
_REQUIRED_FIELDS=frozenset({'name','responsibilities','methods','references','skill_notes',
    'capabilities','defaults','studio_refs'})
_PROFESSIONAL_FIELDS=frozenset({'required_node_operations','delivery_requirements',
    'examples','collaboration_rules'})


def portable_studio_configuration(data):
    """Use the same safe text subset for browsing and fixed publication."""
    return {key:value.strip() for key,value in data.items()
            if key in STUDIO_TEXT_FIELDS and isinstance(value,str)
            and 0<len(value.strip())<=12000}


class AgentProfiles(ProjectLibrary):
    domain='agent_profiles'
    kind='agent'
    source_fields=frozenset({'source'})
    editable=_REQUIRED_FIELDS|_PROFESSIONAL_FIELDS

    def builtin_catalog(self):
        from .builtin_agents import catalog
        with self.read():
            pass
        return catalog()

    def resolve_source(self,ref,*,source=None,old=None):
        require(source in (None,'personal','builtin'),'未知Agent来源')
        chosen=source or 'personal'
        if old is not None:
            origin='builtin' if old.get('source',{}).get('kind')=='builtin' else 'personal'
            require(chosen==origin,'升级不能切换Agent来源','source_conflict')
        if chosen=='builtin':
            from .builtin_agents import resolve
            fixed=resolve(ref)
            return fixed,fixed['source']
        return super().resolve_source(ref,source=source,old=old)

    def adoption_preview(self,ref,*,source=None):
        with self.read():
            pass
        return self.resolve_source(ref,source=source)[0]

    @staticmethod
    def capabilities(*,legacy=False):
        selected=LEGACY_CAPABILITIES if legacy else CAPABILITIES
        return {'items':[dict(item) for item in selected],'execution_enabled':False,
                'notice':'技能说明不授予工具权限；执行须由后续运行时核验范围和授权'}

    @staticmethod
    def _text_list(values,label,*,maximum=50,item_maximum=4000):
        require(isinstance(values,list) and len(values)<=maximum,f'{label}列表无效')
        for value in values:
            text(value,item_maximum)

    @staticmethod
    def _validate_node_operations(values):
        from novelvideo.freezone.orchestration.capabilities import catalog_by_type

        require(isinstance(values,list) and len(values)<=100,'节点操作需求列表无效')
        catalog=catalog_by_type()
        pairs=[]
        for item in values:
            exact(item,{'node_type','operation'})
            text(item['node_type'],100)
            text(item['operation'],100)
            require(item['node_type'] in catalog and item['operation'] in {
                operation['name'] for operation in catalog[item['node_type']]['operations']
            },'未知节点操作需求')
            pairs.append((item['node_type'],item['operation']))
        require(len(pairs)==len(set(pairs)),'节点操作需求不能重复')

    @staticmethod
    def _validate_examples(values):
        require(isinstance(values,list) and len(values)<=20,'示例列表无效')
        for item in values:
            exact(item,{'source_url','prompt_public','inputs_public','workflow_public'})
            source_url=text(item['source_url'],2048)
            try:
                parsed=urlsplit(source_url)
                hostname=parsed.hostname
            except ValueError:
                parsed=None
                hostname=None
            require(parsed is not None and parsed.scheme=='https' and bool(hostname) and parsed.username is None
                    and parsed.password is None,'示例来源须为 HTTPS 地址')
            for key in ('prompt_public','inputs_public','workflow_public'):
                require(type(item[key]) is bool,'示例公开程度必须显式标记')

    def studio_resources(self):
        with self.read():
            pass
        path=self.ctx.state_dir/'creative-studios.db'
        require(not path.is_symlink() and path.resolve().is_relative_to(self.ctx.state_dir.resolve()),
                'Studio资源路径不属于当前项目','scope_violation')
        if not path.is_file():
            return []
        with readonly(path) as conn:
            if not table(conn,'studio_versions'):
                return []
            result=[]
            for row in conn.execute('SELECT kind,id,revision,name,data FROM studio_versions ORDER BY kind,id,revision DESC'):
                if row['kind'] not in {'character','director','previs','intro'}:
                    continue
                data=json.loads(row['data'])
                fields=set(portable_studio_configuration(data))
                result.append({'kind':row['kind'],'id':row['id'],'revision':row['revision'],'name':row['name'],
                    'source':{'kind':'project_studio','project_id':self.ctx.project_id},
                    'portable':bool(fields),'reason':None if fields else '此版本没有可移植的文字配置',
                    'excluded_fields':sorted(set(data)-fields)})
            return result

    def preview(self,config):
        with self.read():
            pass  # Validate authorized project identity without initializing it.
        exact(config,_REQUIRED_FIELDS,_PROFESSIONAL_FIELDS)
        config={
            'required_node_operations':[], 'delivery_requirements':[],
            'examples':[], 'collaboration_rules':[], **config,
        }
        for key in ('name','responsibilities','methods'):
            text(config[key],200 if key=='name' else 12000)
        for key in ('references','skill_notes'):
            self._text_list(config[key],'参考或技能说明')
        require(isinstance(config['capabilities'],list)
                and all(isinstance(value,str) for value in config['capabilities'])
                and len(set(config['capabilities']))==len(config['capabilities'])
                and set(config['capabilities'])<={c['id'] for c in CAPABILITIES},'未知可信能力')
        self._validate_node_operations(config['required_node_operations'])
        self._text_list(config['delivery_requirements'],'交付要求')
        self._text_list(config['collaboration_rules'],'协作规则')
        self._validate_examples(config['examples'])
        require(isinstance(config['defaults'],dict) and set(config['defaults'])<={'style','technique'},'默认偏好类型无效')
        defaults={}
        for kind,selection in config['defaults'].items():
            exact(selection,{'id','proof'})
            entry=next((e for e in self.references.catalog(kind) if e['id']==selection['id'] and e['available']),None)
            require(entry is not None and entry['proof']==selection['proof'],'风格或手法选择已变化','catalog_conflict')
            source=({'kind':'project_custom_style','project_id':self.ctx.project_id}
                    if kind=='style' and entry.get('metadata',{}).get('library_type')=='custom'
                    else {'kind':'global_catalog'})
            defaults[kind]={**entry,'source':source}
        require(isinstance(config['studio_refs'],list) and len(config['studio_refs'])<=20,'Studio引用列表无效')
        resources=[]
        for ref in config['studio_refs']:
            exact(ref,{'kind','id','revision'})
            require(ref['kind'] in {'character','director','previs','intro'},'未知Studio资源')
            text(ref['id'],100);integer(ref['revision'],1)
            path=self.ctx.state_dir/'creative-studios.db'
            require(not path.is_symlink() and path.resolve().is_relative_to(self.ctx.state_dir.resolve()),
                    'Studio资源路径不属于当前项目','scope_violation')
            require(path.is_file(),'Studio资源不存在','scope_violation')
            with readonly(path) as conn:
                row=conn.execute('SELECT name,data FROM studio_versions WHERE kind=? AND id=? AND revision=?',
                    (ref['kind'],ref['id'],ref['revision'])).fetchone()
            require(row is not None,'Studio固定版本不存在','scope_violation')
            data=json.loads(row['data'])
            # Only explicitly portable textual instruction fields, never a raw
            # studio document (which can include credentials/media/local paths).
            portable=portable_studio_configuration(data)
            require(bool(portable),'此Studio版本没有可移植的文字配置','unsupported_reference')
            resources.append({'ref':ref,'name':row['name'],'configuration':portable,
                              'source':{'kind':'project_studio','project_id':self.ctx.project_id},
                              'excluded_fields':sorted(set(data)-set(portable))})
        frozen={**config,'defaults':defaults,'studio_refs':resources,
                'capability_policy':self.capabilities()}
        return {'configuration':frozen,'proof':digest(frozen),'execution_enabled':False}

    async def save(self,body):
        def build(config,proof):
            preview=self.preview(config)
            require(preview['proof']==proof,'发布预览已变化','source_conflict')
            return preview['configuration']
        async with asset_project_lock(self.ctx.state_dir):
            return await registration_io(self.library.apply,self.kind,body,build,self.build_patch)

    def dependencies(self,configuration):
        return {'defaults':configuration['defaults'],'studio_refs':configuration['studio_refs'],
                'capabilities':configuration['capabilities']}

    def personal_patch_preview(self,ref,changes):
        fixed=self.library.version(self.kind,ref)
        values=self.validate_override(changes)
        require(all(value is not None for value in values.values()),
                '个人字段修改请提供明确值，清空列表使用空列表')
        content={**fixed['configuration'],**values}
        for field in _PROFESSIONAL_FIELDS:
            content.setdefault(field,[])
        return {'configuration':content,'proof':digest([fixed,content])}

    def validate_override(self,changes):
        require(isinstance(changes,dict) and set(changes)<=self.editable,'覆盖字段不允许')
        skeleton={'name':'配置','responsibilities':'配置','methods':'配置','references':[],
                  'skill_notes':[],'capabilities':[],'defaults':{},'studio_refs':[],
                  'required_node_operations':[],'delivery_requirements':[],
                  'examples':[],'collaboration_rules':[]}
        raw={**skeleton,**{k:v for k,v in changes.items() if v is not None}}
        frozen=self.preview(raw)['configuration']
        return {k:None if v is None else frozen[k] for k,v in changes.items()}
