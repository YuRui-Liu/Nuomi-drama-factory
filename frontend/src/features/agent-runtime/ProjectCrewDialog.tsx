import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { AgentAdoption, AgentConfiguration } from '@/api/reusableAgents';
import type { BuiltinAgent } from '@/api/agentRuns';
import type { useAgentRuntime } from './useAgentRuntime';

type Runtime = ReturnType<typeof useAgentRuntime>;

function MemberAspects({ configuration }: { configuration: AgentConfiguration }) {
  const items = [
    ['职责', configuration.responsibilities],
    ['方法', configuration.methods],
    ['交付要求', (configuration.delivery_requirements ?? []).join('；') || '未设置'],
    ['协作规则', (configuration.collaboration_rules ?? []).join('；') || '未设置'],
    [
      '所需节点操作',
      (configuration.required_node_operations ?? [])
        .map((item) => `${item.node_type} · ${item.operation}`)
        .join('；') || '未设置',
    ],
  ];
  return (
    <dl className="grid gap-x-3 gap-y-1 sm:grid-cols-[7rem_1fr]">
      {items.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="font-medium text-muted-foreground">{label}</dt>
          <dd className="whitespace-pre-wrap">{value}</dd>
        </div>
      ))}
      {!!configuration.examples?.length && (
        <div className="sm:col-span-2">
          <dt className="font-medium text-muted-foreground">公开示例</dt>
          <dd className="flex flex-wrap gap-2">
            {configuration.examples.map((example, index) => (
              <a
                key={`${example.source_url}:${index}`}
                href={example.source_url}
                target="_blank"
                rel="noreferrer"
                className="underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              >
                示例 {index + 1} · Prompt{example.prompt_public ? '公开' : '未公开'}
                · 输入{example.inputs_public ? '公开' : '未公开'}
                · 流程{example.workflow_public ? '公开' : '未公开'}
              </a>
            ))}
          </dd>
        </div>
      )}
    </dl>
  );
}

function builtinAdoption(adoptions: AgentAdoption[], builtin: BuiltinAgent) {
  return adoptions.find(
    (adoption) =>
      adoption.source.kind === 'builtin' &&
      adoption.source.ref.id === builtin.source.ref.id,
  );
}

function ProjectCrewContent({ runtime: r }: { runtime: Runtime }) {
  const [choice, setChoice] = useState('');
  const builtinIds = new Set(r.builtins.map((builtin) => builtin.source.ref.id));
  const nonBuiltinMembers = r.adoptions.filter(
    (adoption) => adoption.source.kind !== 'builtin' || !builtinIds.has(adoption.source.ref.id),
  );
  return (
    <div className="space-y-4 text-sm">
      {r.error && <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{r.error}</p>}
      <section aria-label="项目主创" className="space-y-2">
        <div>
          <p className="font-medium">
            项目主创：{r.main?.current_agent?.effective.name || '尚未选择'}
          </p>
          {r.main?.current_agent && (
            <p className="text-xs text-muted-foreground">
              {r.adoptions.find((a) => a.adoption_id === r.main?.adoption_id)
                ?.source.kind === 'builtin' ? '来自内置 Agent' : '来自个人 Agent'}
              {' '}· 项目修订 {r.main.current_agent.revision}
            </p>
          )}
        </div>
        <label className="grid gap-1 text-xs text-muted-foreground">
          更换项目主创
          <select
            aria-label="选择项目主创"
            value={choice}
            onChange={(event) => setChoice(event.target.value)}
            className="min-h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            disabled={r.working || r.intent?.phase === 'pending'}
          >
            <option value="">选择项目配置或明确采用一个版本</option>
            <optgroup label="项目已采用">
              {r.adoptions.map((adoption) => (
                <option key={adoption.adoption_id} value={`project:${adoption.adoption_id}`}>
                  {adoption.effective.name} · 项目修订 {adoption.revision}
                </option>
              ))}
            </optgroup>
            <optgroup label="内置 Agent">
              {r.builtins.map((agent) => (
                <option key={agent.source.ref.id} value={`builtin:${agent.source.ref.id}`}>
                  {agent.configuration.name} · 内置 v{agent.source.ref.revision}
                </option>
              ))}
            </optgroup>
            <optgroup label="个人固定版本">
              {r.personal.map((agent) => (
                <option key={agent.id} value={`personal:${agent.id}`}>
                  {agent.configuration.name} · 个人 v{agent.revision}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={!choice || r.working || r.intent?.phase === 'pending'}
            onClick={() => void r.selectMain(choice)}
          >
            设为项目主创
          </Button>
          <p className="text-xs text-muted-foreground">
            配置只影响后续明确发起的任务。
          </p>
        </div>
      </section>

      <section aria-label="项目机组" className="space-y-3 border-t border-white/10 pt-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="font-medium">项目机组</p>
            <p className="text-xs text-muted-foreground">
              {r.adoptions.length} 位已加入成员 · {r.builtins.length} 个内置岗位
            </p>
          </div>
          <Button size="sm" variant="outline" disabled={r.working} onClick={() => void r.refresh()}>
            重新读取
          </Button>
        </div>
        <details className="group">
          <summary className="cursor-pointer rounded-md px-1 py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
            浏览 {r.builtins.length} 个内置岗位与专业配置
          </summary>
          <div className="space-y-2 pt-2">
            {r.builtins.map((builtin) => {
              const adoption = builtinAdoption(r.adoptions, builtin);
              return (
                <details key={builtin.source.ref.id} className="border-b border-white/10 pb-2">
                  <summary className="cursor-pointer py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
                    {builtin.configuration.name}{' '}
                    <span className="text-xs text-muted-foreground">
                      {adoption ? '已加入项目机组' : '内置岗位 · 可加入项目'}
                    </span>
                  </summary>
                  <div className="space-y-2 px-1 pb-2 text-xs">
                    <MemberAspects configuration={adoption?.effective ?? builtin.configuration} />
                    {adoption ? (
                      <p className="text-muted-foreground">
                        项目固定版本 v{adoption.revision} · {adoption.source.kind === 'builtin' ? '内置来源' : '个人来源'}
                      </p>
                    ) : (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={r.working || r.intent?.phase === 'pending'}
                        onClick={() => void r.adoptMember(builtin.source.ref.id)}
                      >
                        加入项目机组：{builtin.configuration.name}
                      </Button>
                    )}
                  </div>
                </details>
              );
            })}
          </div>
        </details>
        {nonBuiltinMembers.map((adoption) => (
          <details key={adoption.adoption_id} className="border-b border-white/10 pb-2">
            <summary className="cursor-pointer py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
              {adoption.effective.name} · 已加入项目机组
            </summary>
            <div className="space-y-2 px-1 pb-2 text-xs">
              <MemberAspects configuration={adoption.effective} />
              <p className="text-muted-foreground">项目固定版本 v{adoption.revision}</p>
            </div>
          </details>
        ))}
      </section>
    </div>
  );
}

export function ProjectCrewDialog({
  runtime,
  open,
  onOpenChange,
  embedded = false,
}: {
  runtime: Runtime;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  embedded?: boolean;
}) {
  if (embedded) return <ProjectCrewContent runtime={runtime} />;
  return (
    <Dialog open={!!open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[min(80vh,44rem)] w-[min(34rem,calc(100vw-2rem))] max-w-none flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-white/10 px-5 py-4">
          <DialogTitle>项目机组</DialogTitle>
          <p className="text-sm text-muted-foreground">管理项目主创和已加入的专业成员。</p>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <ProjectCrewContent runtime={runtime} />
        </div>
      </DialogContent>
    </Dialog>
  );
}
