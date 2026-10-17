import { useState } from 'react';
import { apiCall } from '@/api/client';
import { Button } from '@/components/ui/button';
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES } from '../domain/canvasNodes';
import { useProductionAction } from './useProductionAction';

type RecordValue = Record<string, unknown>;
type Command = Record<string, unknown>;
type MediaValue = { stages?: { render?: { status?: unknown }; video?: { status?: unknown } } };

const fields = { subject: '画面主体', action: '可见动作', visible_start_state: '起始状态', visible_end_state: '结束状态', shot_size: '景别', camera_angle: '摄影角度', composition: '构图', camera_motion: '摄影机运动' };
const cinemaFields = { axis: '动作轴线', camera_side: '摄影机所在侧', screen_direction: '画面运动方向', shadow_direction: '投影方向', exposure_priority: '曝光重点', transition_intent: '转场意图' };
const lightFields = { world_position: '光源位置', direction: '光向', color_temperature: '色温', relative_intensity: '相对强度', motivation: '光源动机' };
const blockingFields = { world_position: '场景位置', screen_position: '画面位置', facing: '身体朝向', gaze_target: '视线目标', motion_path: '行动路径' };
const propFields = { world_position: '场景位置', facing: '道具朝向', held_by: '持有角色', motion_path: '移动路径' };
const decisionFields: Record<string, { title: string; fields: Record<string, string> }> = {
  optics: { title: '焦段与焦点', fields: { purpose: '叙事目的', subject_distance: '主体距离', depth_of_field: '景深', focus_target: '焦点对象', focus_change: '焦点变化' } },
  framing: { title: '取景', fields: { purpose: '叙事目的', start_frame: '开始取景', end_frame: '结束取景' } },
  camera_position: { title: '机位', fields: { purpose: '叙事目的', landmark: '参照地标', position: '位置', height: '高度', distance: '距离', direction: '朝向', axis_side: '轴线侧' } },
  composition: { title: '构图', fields: { purpose: '叙事目的', attention_center: '注意中心', start_composition: '开始构图', end_composition: '结束构图', subject_placement: '主体位置', frame_occupancy: '画面占比', depth_layers: '纵深层次', occlusion: '遮挡', look_room: '视线空间' } },
  motion: { title: '运镜', fields: { purpose: '叙事目的', trigger: '触发节拍', path: '路径', speed_profile: '速度变化', end_condition: '结束条件', constraints: '约束' } },
  performance: { title: '人物动作与表演', fields: { purpose: '叙事目的' } },
  lighting: { title: '光影', fields: { purpose: '叙事目的', camera_relative_lighting: '当前机位受光', shadow: '阴影', quality: '光质', color: '色彩', subject_background_exposure: '主体与背景曝光', eye_light: '眼神光' } },
  handoff: { title: '镜头衔接', fields: { purpose: '叙事目的', cut_trigger: '切点', spatial_handoff: '空间交接', gaze_handoff: '视线交接', action_phase: '动作相位', held_object_handoff: '持物交接', emotional_handoff: '情绪延续', sound_bridge: '声音衔接' } },
};
const performanceFields = { start_behavior: '开始动作', end_behavior: '结束动作', goal: '角色目标', obstacle: '阻力', tactics: '行动策略', visible_behavior: '可见动作与表情', gaze: '视线', contact: '接触', held_objects: '持物', weight_and_inertia: '重量与惯性' };

function TextFields({ values, labels, onChange }: { values: Record<string, unknown>; labels: Record<string, string>; onChange: (key: string, value: string) => void }) {
  return <div className="grid gap-3 sm:grid-cols-2">{Object.entries(labels).map(([key, label]) => <label className="grid gap-1 text-sm" key={key}>{label}<textarea className="rounded border bg-background p-2" value={String(values[key] ?? '')} onChange={event => onChange(key, event.target.value)} /></label>)}</div>;
}

function groupDuration(group: RecordValue): number {
  const shots = Array.isArray(group.shots) ? group.shots as RecordValue[] : [];
  return shots.reduce((sum, item) => sum + Math.max(0, Number(item.duration_seconds) || 0), 0);
}

function relatedReferenceLabels(groupId: string, revisionId: string): string[] {
  const state = useCanvasStore.getState() as unknown as {
    nodes: Array<{ id: string; type: string; data: Record<string, unknown> }>;
    edges: Array<{ source: string; target: string }>;
  };
  const related = state.nodes.filter(node =>
    node.data.groupId === groupId && node.data.directorRevisionId === revisionId,
  );
  const ids = new Set(related.map(node => node.id));
  const composeIds = new Set<string>();
  for (const edge of state.edges) {
    const dependencyId = ids.has(edge.source) ? edge.source : ids.has(edge.target) ? edge.target : '';
    const otherId = dependencyId === edge.source ? edge.target : edge.source;
    const other = state.nodes.find(node => node.id === otherId);
    if (other?.type === CANVAS_NODE_TYPES.videoCompose) composeIds.add(other.id);
  }
  return [...related.map(node => String(node.data.displayName ?? node.id)), ...[...composeIds].map(id => `剪辑节点 ${id}`)];
}

export function DirectorShotEditor({ project, episode, revisionId, shot, group, shotIndex, media, onSaved }: {
  project: string;
  episode: number;
  revisionId: string;
  shot: RecordValue;
  group: RecordValue;
  shotIndex: number;
  media?: MediaValue;
  onSaved: (revision: RecordValue) => void;
}) {
  const [draft, setDraft] = useState(shot);
  const [review, setReview] = useState<{ title: string; command: Command; effects: string[]; blocked?: string } | null>(null);
  const { busy, error, perform } = useProductionAction();
  const cinema = draft.cinematography as RecordValue | null;
  const creative = draft.creative as Record<string, RecordValue> | null;
  const shots = Array.isArray(group.shots) ? group.shots as RecordValue[] : [];
  const shotId = String(shot.id ?? '');
  const groupId = String(group.id ?? group.group_id ?? '');
  const updateDecision = (dimension: string, key: string, value: unknown) => setDraft(previous => ({ ...previous, creative: { ...creative, [dimension]: { ...creative?.[dimension], [key]: value } } }));
  const updateCinema = (key: string, value: unknown) => setDraft(previous => ({ ...previous, cinematography: { ...cinema, [key]: value } }));
  const sourceIds = Array.isArray(shot.source_span_ids) ? shot.source_span_ids as string[] : [];
  const dialogueIds = Array.isArray(shot.dialogue_source_ids) ? shot.dialogue_source_ids as string[] : [];
  const references = relatedReferenceLabels(groupId, revisionId);

  const queueReview = (title: string, command: Command, effects: string[], blocked?: string) => setReview({ title, command, effects, blocked });
  const changedFields = [
    ...Object.keys(fields).filter(key => String(shot[key] ?? '') !== String(draft[key] ?? '')),
    ...(JSON.stringify(shot.cinematography) !== JSON.stringify(draft.cinematography) ? ['摄影/站位/灯光'] : []),
    ...(JSON.stringify(shot.creative) !== JSON.stringify(draft.creative) ? ['专业创作决策'] : []),
  ];
  const originalDuration = Math.max(0, Number(shot.duration_seconds) || 0);
  const changedDuration = Math.max(0, Number(draft.duration_seconds) || 0);
  const hasDraftChanges = JSON.stringify(shot) !== JSON.stringify(draft);
  const currentGroupDuration = groupDuration(group);
  const mediaStatus = media
    ? `关联媒体状态：分镜 ${String(media.stages?.render?.status ?? '未知')}，视频 ${String(media.stages?.video?.status ?? '未知')}。`
    : '关联组媒体会继续固定在当前已采用版本，候选保存不会替换或重生成媒体。';

  const submit = () => {
    if (!review) return;
    const pending = review;
    void perform(`shot-edit:${revisionId}:${JSON.stringify(pending.command)}`, async () => {
      const child = await apiCall<RecordValue>(
        `projects/${encodeURIComponent(project)}/episodes/${episode}/director-plans/${encodeURIComponent(revisionId)}/edits`,
        { method: 'post', retry: 0, json: pending.command },
      );
      setReview(null);
      onSaved(child);
    });
  };

  const moveShot = (targetIndex: number) => {
    const ids = shots.map(item => String(item.id ?? ''));
    const [moving] = ids.splice(shotIndex, 1);
    ids.splice(targetIndex, 0, moving);
    queueReview('重排镜头', { kind: 'reorder_shots', group_id: groupId, shot_ids: ids }, [
      `组内顺序将从 ${shotIndex + 1} 位移动到 ${targetIndex + 1} 位；总时长保持 ${currentGroupDuration.toFixed(1)} 秒。`,
      `保留 ${sourceIds.length} 个来源引用与 ${dialogueIds.length} 个对白来源。`,
      mediaStatus,
      references.length ? `下游引用：${references.join('、')}。` : '当前画布没有找到绑定此导演版本和叙事组的下游节点。',
    ]);
  };

  return <details className="mt-3 rounded border p-3"><summary className="cursor-pointer text-sm">调整镜头与专业方案</summary><div className="mt-3 space-y-3">
    <TextFields values={draft} labels={fields} onChange={(key, value) => setDraft(previous => ({ ...previous, [key]: value }))} />
    <label className="grid gap-1 text-sm">目标时长（秒）<input className="rounded border bg-background p-2" type="number" min={0.1} step={0.1} value={Number(draft.duration_seconds ?? 1)} onChange={event => setDraft(previous => ({ ...previous, duration_seconds: Number(event.target.value) }))} /></label>
    {cinema && <><h4 className="text-sm font-medium">摄影与空间连续性</h4><TextFields values={cinema} labels={cinemaFields} onChange={updateCinema} />
      {(['subjects', 'props', 'lights'] as const).map(collection => <section className="space-y-3" key={collection}><h4 className="text-sm font-medium">{{ lights: '灯光方案', subjects: '人物站位与视线', props: '道具调度' }[collection]}</h4>{(Array.isArray(cinema[collection]) ? cinema[collection] as Array<RecordValue> : []).map((item, index) => <div key={String(item.subject_id ?? item.prop_id ?? item.light_id ?? index)} className="rounded border p-3"><p className="mb-2 text-sm">{String(item.subject_id ?? item.prop_id ?? item.light_id ?? index + 1)}</p><TextFields values={item} labels={collection === 'lights' ? lightFields : collection === 'props' ? propFields : blockingFields} onChange={(key, value) => updateCinema(collection, (cinema[collection] as Array<RecordValue>).map((entry, i) => i === index ? { ...entry, [key]: value } : entry))} /></div>)}</section>)}</>}
    {creative && Object.entries(decisionFields).map(([dimension, definition]) => creative[dimension] && <details key={dimension} className="rounded border p-3"><summary>{definition.title}</summary><TextFields values={creative[dimension]} labels={definition.fields} onChange={(key, value) => updateDecision(dimension, key, value)} />
      {dimension === 'performance' && Array.isArray(creative.performance.characters) && (creative.performance.characters as Array<RecordValue>).map((character, index) => <div key={String(character.subject_id)} className="mt-3 rounded border p-3"><p>{String(character.subject_id)}</p><TextFields values={character} labels={performanceFields} onChange={(key, value) => updateDecision('performance', 'characters', (creative.performance.characters as Array<RecordValue>).map((item, i) => i === index ? { ...item, [key]: value } : item))} /></div>)}
    </details>)}

    <div className="flex flex-wrap gap-2" aria-label="镜头结构操作">
      <Button variant="outline" disabled={busy || shotIndex <= 0} onClick={() => moveShot(shotIndex - 1)}>上移镜头</Button>
      <Button variant="outline" disabled={busy || shotIndex >= shots.length - 1} onClick={() => moveShot(shotIndex + 1)}>下移镜头</Button>
      <Button variant="outline" disabled={busy || !shotId} onClick={() => queueReview('新增镜头模板', { kind: 'add_shot_from_template', group_id: groupId, template_shot_id: shotId }, [
        `在当前镜头之后插入同内容模板，组时长约增加 ${originalDuration.toFixed(1)} 秒。`,
        `模板会复制 ${sourceIds.length} 个来源引用${dialogueIds.length ? ` 与 ${dialogueIds.length} 个对白引用` : ''}；创建后请编辑新增镜头的动作与时长。`,
        mediaStatus,
        references.length ? `下游引用：${references.join('、')}。` : '当前画布没有找到绑定此导演版本和叙事组的下游节点。',
      ])}>审查新增镜头</Button>
      <Button variant="outline" disabled={busy || shots.length <= 1} onClick={() => {
        const remaining = shots.filter(item => String(item.id ?? '') !== shotId);
        const remainingSources = new Set(remaining.flatMap(item => Array.isArray(item.source_span_ids) ? item.source_span_ids as string[] : []));
        const missingSources = sourceIds.filter(id => !remainingSources.has(id));
        const remainingDialogue = new Set(remaining.flatMap(item => Array.isArray(item.dialogue_source_ids) ? item.dialogue_source_ids as string[] : []));
        const missingDialogue = dialogueIds.filter(id => !remainingDialogue.has(id));
        const blocked = missingSources.length || missingDialogue.length
          ? `必须先把唯一来源引用重新分配到其他镜头：${[...missingSources, ...missingDialogue].join('、')}`
          : undefined;
        queueReview('删除镜头', { kind: 'delete_shot', shot_id: shotId }, [
          `删除后组内保留 ${remaining.length} 个镜头，总时长约为 ${(currentGroupDuration - originalDuration).toFixed(1)} 秒。`,
          `此镜头引用来源 ${sourceIds.length} 个、对白 ${dialogueIds.length} 个；只有已由其他镜头覆盖的来源可删除。`,
          mediaStatus,
          references.length ? `下游引用：${references.join('、')}。` : '当前画布没有找到绑定此导演版本和叙事组的下游节点。',
        ], blocked);
      }}>审查删除镜头</Button>
    </div>

    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <Button disabled={busy || !hasDraftChanges} onClick={() => {
      const command = {
        kind: 'update_shot', shot_id: shot.id,
        ...Object.fromEntries(Object.keys(fields).map(key => [key, draft[key]])),
        duration_seconds: draft.duration_seconds,
        ...(cinema ? { cinematography: cinema } : {}),
        ...(creative ? { creative } : {}),
      };
      queueReview('修改镜头与专业方案', command, [
        `变更字段：${changedFields.join('、') || '目标时长'}；组总时长由 ${currentGroupDuration.toFixed(1)} 秒调整为 ${(currentGroupDuration - originalDuration + changedDuration).toFixed(1)} 秒。`,
        `镜头仍引用 ${sourceIds.length} 个剧本来源及 ${dialogueIds.length} 个对白来源。`,
        mediaStatus,
        references.length ? `下游引用：${references.join('、')}。` : '当前画布没有找到绑定此导演版本和叙事组的下游节点。',
      ]);
    }}>审查镜头修订影响</Button>
    <p className="text-xs text-muted-foreground">保存创建待审查导演候选；采用与媒体重生成分开执行，历史产物保持可追溯。</p>
    {review && <div className="space-y-2 rounded-lg border bg-muted/30 p-3" aria-live="polite">
      <h4 className="font-medium">{review.title} · 影响预览</h4>
      <ul className="list-disc space-y-1 pl-5 text-sm">{review.effects.map(effect => <li key={effect}>{effect}</li>)}</ul>
      {review.blocked && <p className="text-sm text-destructive" role="alert">{review.blocked}</p>}
      <p className="text-xs text-muted-foreground">候选会固定到新导演版本；现有下游版本、媒体与剪辑片段不会自动追改。</p>
      <div className="flex gap-2"><Button disabled={busy || Boolean(review.blocked)} onClick={submit}>创建候选版本</Button><Button variant="outline" disabled={busy} onClick={() => setReview(null)}>返回镜头编辑</Button></div>
    </div>}
  </div></details>;
}
