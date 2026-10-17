import { useMemo, useState } from 'react';
import { apiCall } from '@/api/client';
import { Button } from '@/components/ui/button';
import { CANVAS_NODE_TYPES } from '../domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { useProductionAction } from './useProductionAction';

type RecordValue = Record<string, unknown>;
type Command = Record<string, unknown>;

function seconds(group: RecordValue | undefined): number {
  const shots = Array.isArray(group?.shots) ? group.shots as RecordValue[] : [];
  return shots.reduce((sum, shot) => sum + Math.max(0, Number(shot.duration_seconds) || 0), 0);
}

function sourceIds(group: RecordValue | undefined): string[] {
  return Array.isArray(group?.source_span_ids)
    ? (group.source_span_ids as unknown[]).filter((value): value is string => typeof value === 'string')
    : [];
}

function dialogueIds(group: RecordValue | undefined): string[] {
  const shots = Array.isArray(group?.shots) ? group.shots as RecordValue[] : [];
  return [...new Set(shots.flatMap(shot => Array.isArray(shot.dialogue_source_ids)
    ? (shot.dialogue_source_ids as unknown[]).filter((value): value is string => typeof value === 'string')
    : []))];
}

function downstreamLabels(groupIds: string[], revisionId: string): string[] {
  const state = useCanvasStore.getState() as unknown as {
    nodes: Array<{ id: string; type: string; data: Record<string, unknown> }>;
    edges: Array<{ source: string; target: string }>;
  };
  const related = state.nodes.filter(node => groupIds.includes(String(node.data.groupId ?? ''))
    && node.data.directorRevisionId === revisionId);
  const relatedIds = new Set(related.map(node => node.id));
  const composeIds = new Set<string>();
  for (const edge of state.edges) {
    const linked = relatedIds.has(edge.source) ? edge.source : relatedIds.has(edge.target) ? edge.target : '';
    const otherId = linked === edge.source ? edge.target : linked ? edge.source : '';
    if (state.nodes.find(node => node.id === otherId)?.type === CANVAS_NODE_TYPES.videoCompose) composeIds.add(otherId);
  }
  return [
    ...related.map(node => String(node.data.displayName ?? node.id)),
    ...[...composeIds].map(id => `剪辑节点 ${id}`),
  ];
}

export function DirectorGroupStructureEditor({ project, episode, revisionId, groups, group, onSaved }: {
  project: string;
  episode: number;
  revisionId: string;
  groups: RecordValue[];
  group: RecordValue;
  onSaved: (revision: RecordValue) => void;
}) {
  const groupId = String(group.id ?? group.group_id ?? '');
  const groupIndex = groups.findIndex(item => String(item.id ?? item.group_id ?? '') === groupId);
  const nextGroup = groups[groupIndex + 1];
  const shots = Array.isArray(group.shots) ? group.shots as RecordValue[] : [];
  const [splitBeforeShot, setSplitBeforeShot] = useState('');
  const [review, setReview] = useState<{ title: string; command: Command; effects: string[] } | null>(null);
  const { busy, error, perform } = useProductionAction();
  const splitIndex = shots.findIndex(shot => String(shot.id ?? '') === splitBeforeShot);
  const leftShots = splitIndex > 0 ? shots.slice(0, splitIndex) : [];
  const rightShots = splitIndex > 0 ? shots.slice(splitIndex) : [];
  const splitEffects = useMemo(() => {
    if (!splitBeforeShot) return [];
    const left = { shots: leftShots };
    const right = { shots: rightShots };
    const idsForShots = (items: RecordValue[], field: 'source_span_ids' | 'dialogue_source_ids') => [...new Set(items.flatMap(shot => Array.isArray(shot[field]) ? shot[field] as string[] : []))];
    const references = downstreamLabels([groupId], revisionId);
    return [
      `拆为 ${leftShots.length} 镜头 / ${seconds(left).toFixed(1)} 秒 与 ${rightShots.length} 镜头 / ${seconds(right).toFixed(1)} 秒。`,
      `来源范围：左组 ${idsForShots(leftShots, 'source_span_ids').join('、') || '无'}；右组 ${idsForShots(rightShots, 'source_span_ids').join('、') || '无'}。原组 ID 保留，新增组将获得新 ID。`,
      `对白引用：左组 ${idsForShots(leftShots, 'dialogue_source_ids').join('、') || '无'}；右组 ${idsForShots(rightShots, 'dialogue_source_ids').join('、') || '无'}。`,
      references.length ? `现有下游节点：${references.join('、')}。它们仍固定在旧导演版本与旧组 ID。` : '当前画布没有找到绑定该版本和组的下游节点。',
      '已有分镜图、视频和剪辑引用保持绑定旧导演版本；候选采用与媒体重生成分开。',
    ];
  }, [groupId, leftShots, revisionId, rightShots, splitBeforeShot]);

  const submit = (title: string, command: Command, effects: string[]) => setReview({ title, command, effects });
  const save = () => {
    if (!review) return;
    const pending = review;
    void perform(`director-structure:${revisionId}:${JSON.stringify(pending.command)}`, async () => {
      const candidate = await apiCall<RecordValue>(
        `projects/${encodeURIComponent(project)}/episodes/${episode}/director-plans/${encodeURIComponent(revisionId)}/edits`,
        { method: 'post', retry: 0, json: pending.command },
      );
      setReview(null);
      onSaved(candidate);
    });
  };

  return <section className="mt-3 space-y-3 rounded-lg border border-dashed p-3" aria-label={`${groupId} 组结构编辑`}>
    <h4 className="text-sm font-medium">叙事组拆分与合并</h4>
    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
      <label className="grid gap-1 text-sm">新组从哪个镜头开始
        <select className="rounded border bg-background p-2" value={splitBeforeShot} onChange={event => setSplitBeforeShot(event.target.value)}>
          <option value="">选择拆分点</option>
          {shots.slice(1).map((shot, index) => <option key={String(shot.id)} value={String(shot.id)}>第 {index + 2} 镜 · {String(shot.id)}</option>)}
        </select>
      </label>
      <Button className="self-end" variant="outline" disabled={busy || !splitBeforeShot || splitIndex < 1} onClick={() => submit(
        '拆分叙事组',
        { kind: 'split_group', group_id: groupId, before_shot_id: splitBeforeShot },
        splitEffects,
      )}>审查拆分影响</Button>
    </div>
    {nextGroup && <Button variant="outline" disabled={busy} onClick={() => submit(
      '合并相邻叙事组',
      { kind: 'merge_adjacent_groups', left_group_id: groupId, right_group_id: String(nextGroup.id ?? nextGroup.group_id ?? '') },
      [
        `合并 ${groupId} 与 ${String(nextGroup.id ?? nextGroup.group_id ?? '')}，共 ${shots.length + (Array.isArray(nextGroup.shots) ? nextGroup.shots.length : 0)} 个镜头、${(seconds(group) + seconds(nextGroup)).toFixed(1)} 秒。`,
        `来源范围合并：${[...new Set([...sourceIds(group), ...sourceIds(nextGroup)])].join('、') || '无'}；场景或创意契约不兼容时服务会拒绝。`,
        `对白来源合并：${[...new Set([...dialogueIds(group), ...dialogueIds(nextGroup)])].join('、') || '无'}。`,
        downstreamLabels([groupId, String(nextGroup.id ?? nextGroup.group_id ?? '')], revisionId).length
          ? `现有下游节点：${downstreamLabels([groupId, String(nextGroup.id ?? nextGroup.group_id ?? '')], revisionId).join('、')}。它们仍固定在旧导演版本与旧组 ID。`
          : '当前画布没有找到绑定这两个组的下游节点。',
        '两组已有图像、视频与剪辑片段继续固定旧版本，不会因候选保存而被替换。',
      ],
    )}>审查并合并下一相邻组</Button>}
    {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
    {review && <div className="space-y-2 rounded-lg border bg-muted/30 p-3" aria-live="polite">
      <h5 className="text-sm font-medium">{review.title} · 影响预览</h5>
      <ul className="list-disc space-y-1 pl-5 text-sm">{review.effects.map(effect => <li key={effect}>{effect}</li>)}</ul>
      <p className="text-xs text-muted-foreground">保存仅创建待审查导演候选版本，不会自动采用或重生成媒体。</p>
      <div className="flex gap-2"><Button disabled={busy} onClick={save}>创建候选版本</Button><Button variant="outline" disabled={busy} onClick={() => setReview(null)}>返回编辑</Button></div>
    </div>}
  </section>;
}
