"""User-owned Agent configuration; no executor or implicit tool grants."""
import json

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
)
STUDIO_TEXT_FIELDS=frozenset({'description','instructions','method','notes','intent'})


def portable_studio_configuration(data):
    """Use the same safe text subset for browsing and fixed publication."""
    return {key:value.strip() for key,value in data.items()
            if key in STUDIO_TEXT_FIELDS and isinstance(value,str)
            and 0<len(value.strip())<=12000}


class AgentProfiles(ProjectLibrary):
    domain='agent_profiles'
    kind='agent'
    editable=frozenset({'name','responsibilities','methods','references','skill_notes','capabilities','defaults','studio_refs'})

    @staticmethod
    def capabilities():
        return {'items':list(CAPABILITIES),'execution_enabled':False,
                'notice':'技能说明不授予工具权限；执行须由后续运行时核验范围和授权'}

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
        exact(config,{'name','responsibilities','methods','references','skill_notes','capabilities','defaults','studio_refs'})
        for key in ('name','responsibilities','methods'):
            text(config[key],200 if key=='name' else 12000)
        for key in ('references','skill_notes'):
            require(isinstance(config[key],list) and len(config[key])<=50,'参考或技能说明列表无效')
            for value in config[key]:
                text(value,4000)
        require(isinstance(config['capabilities'],list) and len(set(config['capabilities']))==len(config['capabilities'])
                and set(config['capabilities'])<={c['id'] for c in CAPABILITIES},'未知可信能力')
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

    def validate_override(self,changes):
        require(isinstance(changes,dict) and set(changes)<=self.editable,'覆盖字段不允许')
        skeleton={'name':'配置','responsibilities':'配置','methods':'配置','references':[],
                  'skill_notes':[],'capabilities':[],'defaults':{},'studio_refs':[]}
        raw={**skeleton,**{k:v for k,v in changes.items() if v is not None}}
        frozen=self.preview(raw)['configuration']
        return {k:None if v is None else frozen[k] for k,v in changes.items()}
