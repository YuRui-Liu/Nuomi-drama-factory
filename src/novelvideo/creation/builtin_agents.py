"""Trusted, versioned starter configurations; never executor permissions."""
from copy import deepcopy

from .library_references import digest
from .ontology import exact, integer, require, text
from .professional_agents import PROFESSIONAL_AGENTS


_LEGACY_BUILTIN_AGENTS=(
    {'id':'builtin:creative-lead','revision':1,'configuration':{
        'name':'通用主创','responsibilities':'理解用户创作目标，协调局部创作建议，维护人物、场景与叙事一致性。',
        'methods':'依据用户题材、已确认约定和当前授权范围组织工作。先明确意图与已有素材，再提出可比较的局部方案；不强制线性制作阶段，不将草案视为已确认事实。',
        'references':[],'skill_notes':['把修改建议与事实确认分开；媒体方案须另行授权执行。'],
        'capabilities':['context.read','proposal.create','ontology.draft','media.propose'],
        'defaults':{},'studio_refs':[]}},
    {'id':'builtin:text-writer','revision':1,'configuration':{
        'name':'文本编剧','responsibilities':'在明确授权的片段中提出情节、人物行动与对白建议。',
        'methods':'遵循用户题材、人物动机和已确认约定，以可修改的文本候选呈现方案。保留未决问题，不擅自扩展范围或改变已确认事实。',
        'references':[],'skill_notes':['文本建议不代表用户已接受，不自动生成媒体。'],
        'capabilities':['context.read','proposal.create'], 'defaults':{},'studio_refs':[]}},
)
BUILTIN_AGENTS=(*_LEGACY_BUILTIN_AGENTS,*PROFESSIONAL_AGENTS)


def _fixed(entry):
    from .agent_profiles import AgentProfiles
    fixed=deepcopy(entry)
    fixed['kind']='agent'
    legacy_v1=(fixed['id'],fixed['revision']) in {
        ('builtin:creative-lead',1),('builtin:text-writer',1),
    }
    fixed['configuration']['capability_policy']=AgentProfiles.capabilities(legacy=legacy_v1)
    content_hash=digest(fixed)
    fixed['source']={'kind':'builtin','ref':{'id':fixed['id'],'revision':fixed['revision']},
                     'content_hash':content_hash}
    fixed['content_hash']=content_hash
    fixed['proof']=digest(fixed)
    return fixed


def resolve(ref):
    exact(ref,{'id','revision'})
    text(ref['id'],100);integer(ref['revision'],1)
    entry=next((e for e in BUILTIN_AGENTS if e['id']==ref['id'] and e['revision']==ref['revision']),None)
    require(entry is not None,'内置Agent版本不存在','source_conflict')
    return _fixed(entry)


def catalog():
    latest={}
    for entry in BUILTIN_AGENTS:
        if entry['revision']>latest.get(entry['id'],{}).get('revision',0):
            latest[entry['id']]=entry
    return [_fixed(entry) for entry in latest.values()]
