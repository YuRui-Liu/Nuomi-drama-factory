import type { TeamOverview } from './types';
import { useEffect, useState } from 'react';
import { TemplatePanel } from './template-panel';
import { ResourceLibrary } from './resource-library';
import { TrialPanel } from './trial-panel';
export interface TeamToolsProps { project: string; overview: TeamOverview; disabled: boolean; selectedRole: string; selectedSubtask: string; onChanged: () => void; onEditingChange?: (editing: boolean) => void }
export function TeamTools(props: TeamToolsProps) {
  const [tab, setTab] = useState('resources');
  const [resourceEditing, setResourceEditing] = useState(false);
  const [templateEditing, setTemplateEditing] = useState(false);
  const [trialEditing, setTrialEditing] = useState(false);
  useEffect(() => { props.onEditingChange?.(resourceEditing || templateEditing || trialEditing); }, [resourceEditing, templateEditing, trialEditing, props.onEditingChange]);
  return <div className="space-y-4 rounded-xl border border-zinc-800 p-4"><div role="tablist" aria-label="团队工具" className="flex flex-wrap gap-2">{[['resources','内置方法与资源'],['templates','团队模板'],['trials','方法试运行']].map(([id,label]) => <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)} className={`rounded px-3 py-2 text-sm ${tab === id ? 'bg-lime-300 text-zinc-950' : 'bg-zinc-900 text-zinc-400'}`}>{label}</button>)}</div>{props.disabled && <p className="text-xs text-amber-200">请先保存方法草稿并解决版本冲突，再应用模板、资源或提交试运行。</p>}<div role="tabpanel" key={`${props.project}:${props.selectedRole}:${props.selectedSubtask}`}><div hidden={tab !== 'resources'}><ResourceLibrary {...props} onEditingChange={setResourceEditing} /></div><div hidden={tab !== 'templates'}><TemplatePanel {...props} onEditingChange={setTemplateEditing} /></div><div hidden={tab !== 'trials'}><TrialPanel {...props} onEditingChange={setTrialEditing} /></div></div></div>;
}
