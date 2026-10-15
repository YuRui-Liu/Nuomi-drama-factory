"""Trusted, versioned starter configurations; never executor permissions."""
from copy import deepcopy

from .library_references import digest
from .ontology import exact, integer, require, text


BUILTIN_AGENTS=(
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
    {'id':'builtin:ideation','revision':1,'configuration':{
        'name':'立意策划','responsibilities':'依据用户给定的题材、受众、角色、时代、看点、画风、篇幅与故事种子，生成三个可比较的原创故事方向。',
        'methods':'整理用户提供的创作要素，围绕不同核心冲突和叙事钩子提出三个有区分度的方向。调用 proposal.create 时，text 必须是无 Markdown 代码围栏的纯 JSON，根对象仅含 schema_version 和 candidates，严格符合 {"schema_version":1,"candidates":[...]}；schema_version 必须是整数 1，candidates 恰好三条。每条候选仅含 id、title、premise、protagonist_goal、conflict、hook、expansion；id 是非空字符串，且在三条候选之间唯一，其余六项均为非空字符串。候选始终作为建议交由用户选择或修改，不自动采纳，也不执行媒体制作。',
        'references':[],'skill_notes':['proposal.create 的 text 必须严格遵循 methods 中的 JSON 契约。立意候选只供比较与用户确认，不自动采纳或执行媒体制作。'],
        'capabilities':['context.read','proposal.create'],'defaults':{},'studio_refs':[]}},
)


def _fixed(entry):
    from .agent_profiles import AgentProfiles
    fixed=deepcopy(entry)
    fixed['kind']='agent'
    fixed['configuration']['capability_policy']=AgentProfiles.capabilities()
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
