type RecordValue = Record<string, unknown>;
const record = (value: unknown): RecordValue => value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};
const labels: Record<string, string> = {
  pending: '等待执行', completed: '已完成', failed: '失败', unknown: '结果待确认',
  intent: '分镜意图', narrative_purpose: '叙事作用', audience_attention: '观众注意力',
  emotional_effect: '情绪效果', continuity_strategy: '连续性', shot_size: '景别',
  camera_angle: '机位角度', composition: '构图', camera_motion: '运镜', axis: '轴线',
  camera_side: '机位侧向', screen_direction: '画面方向', transition_intent: '转场意图',
  lighting: '灯光', purpose: '目的', source: '来源', kind: '类型', reference_ids: '参考',
  light_ids: '灯位', camera_relative_lighting: '相对机位光向', shadow: '阴影',
  quality: '光质', color: '光色', subject_background_exposure: '主体与背景曝光', eye_light: '眼神光',
  subjects: '人物站位', props: '道具站位', subject_id: '角色', prop_id: '道具',
  world_position: '场景位置', screen_position: '画面位置', facing: '朝向',
  gaze_target: '注视目标', motion_path: '运动路径', held_by: '持有人',
  characters: '角色表演', performance: '表演', start_behavior: '起始行为',
  end_behavior: '结束行为', goal: '目标', obstacle: '阻碍', tactics: '策略',
  visible_behavior: '可见行为', gaze: '视线', contact: '接触', held_objects: '持物',
  weight_and_inertia: '重量与惯性', beats: '表演节拍', at_seconds: '秒数',
  event_anchor: '事件', behavior: '行为', asset_requirements: '资产设计要求',
  entity_key: '资产', design_notes: '设计说明',
};

function describe(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (Array.isArray(value)) return value.map(describe).filter(Boolean).join('、');
  return Object.entries(record(value)).map(([key, item]) => `${labels[key] ?? key}：${describe(item)}`).join('；');
}

export function DirectorProfessionalRuns({ runs, decision }: { runs: unknown; decision: unknown }) {
  const entries = Array.isArray(runs) ? runs.map(record) : [];
  if (!entries.length && typeof decision !== 'string') return null;
  return <section aria-label="导演专业协作" className="space-y-3 rounded-lg border p-3">
    <h3 className="font-medium">导演专业协作</h3>
    {typeof decision === 'string' && decision && <p className="text-sm text-muted-foreground">{decision}</p>}
    {entries.map((run, index) => {
      const output = record(run.output);
      const contributions = Array.isArray(output.contributions) ? output.contributions.map(record) : [];
      const summary = typeof run.summary === 'string' ? run.summary : typeof output.summary === 'string' ? output.summary : '';
      return <article key={String(run.run_id ?? index)} className="space-y-2 rounded-md bg-muted/30 p-3">
        <p className="text-sm font-medium">{String(run.profile_name ?? run.role_id ?? '专业成员')} · {labels[String(run.status)] ?? String(run.status ?? '等待执行')}</p>
        <p className="text-sm">{String(run.task ?? '')}</p>
        {typeof run.reason === 'string' && <p className="text-xs text-muted-foreground">分工依据：{run.reason}</p>}
        <p className="text-xs text-muted-foreground">叙事组 {String(run.group_id ?? '')} · 镜头 {describe(run.shot_ids)} · 岗位版本 {String(run.profile_revision ?? '')}</p>
        {summary && <p className="whitespace-pre-wrap text-sm">{summary}</p>}
        {contributions.length > 0 && <details><summary className="cursor-pointer text-sm">查看岗位成果</summary>
          <div className="mt-2 space-y-3">{contributions.map((contribution, contributionIndex) => <div key={String(contribution.shot_id ?? contributionIndex)} className="space-y-1 border-l-2 pl-3">
            <p className="text-sm font-medium">镜头 {String(contribution.shot_id ?? '')}</p>
            {Object.entries(contribution).filter(([key]) => key !== 'shot_id' && key in labels).map(([key, value]) => <p key={key} className="whitespace-pre-wrap text-sm"><span className="text-muted-foreground">{labels[key]}：</span>{describe(value)}</p>)}
          </div>)}</div>
        </details>}
        {typeof run.error_code === 'string' && run.error_code && <p role="alert" className="text-sm text-destructive">该岗位尚未完成：{run.error_code}</p>}
      </article>;
    })}
  </section>;
}
