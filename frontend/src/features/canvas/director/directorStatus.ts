const STAGES: Record<string, string> = {
  created: '已创建',
  optimizing: '优化中',
  preparing: '准备中',
  submitting: '提交中',
  queued: '排队中',
  generating: '生成中',
  completed: '已完成',
  failed: '失败',
  submission_unknown: '提交状态未知',
};

export function directorStageLabel(stage: string | null | undefined,
  t: (key: string, options: { defaultValue: string }) => string): string {
  if (!stage) return '';
  const known = Object.prototype.hasOwnProperty.call(STAGES, stage);
  return t(`node.videoDirector.status.${known ? stage : 'unknown'}`,
    { defaultValue: known ? STAGES[stage] : '状态未知' });
}
