import type { TeamOverview } from './types';
export interface TeamToolsProps { project: string; overview: TeamOverview; disabled: boolean; selectedRole: string; selectedSubtask: string; onChanged: () => void }
export function TeamTools(_props: TeamToolsProps) { return <p className="text-xs text-zinc-500">模板管理、资源库与试运行尚未完成接入。</p>; }
