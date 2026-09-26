import type { DirectorDraft } from '../domain/canvasNodes';
import type { DirectorCapabilities } from '@/api/videoDirector';

export type DirectorErrors = Record<string, string>;

export function alignDirectorDuration(seconds: number, capabilities: DirectorCapabilities): { frames: number; seconds: number } {
  const { fps, frameStep, frameOffset } = capabilities;
  if (!Number.isFinite(seconds) || seconds <= 0 || fps <= 0 || frameStep <= 0) return { frames: 0, seconds: 0 };
  const frames = frameOffset + Math.max(0, Math.ceil((seconds * fps - frameOffset) / frameStep)) * frameStep;
  return { frames, seconds: frames / fps };
}

export function validateDirectorDraft(draft: DirectorDraft, capabilities: DirectorCapabilities): DirectorErrors {
  const errors: DirectorErrors = {};
  if (!capabilities.models.some((model) => model.id === draft.modelId)) errors.model_id = '当前模型不可用，请选择可用模型';
  if (!capabilities.params.resolution.includes(draft.resolution)) errors.resolution = '当前分辨率不可用';
  if (!capabilities.params.aspectRatio.includes(draft.aspectRatio)) errors.aspect_ratio = '当前画幅不可用';
  if (capabilities.sizes.length && !capabilities.sizes.some((size) => size.resolution === draft.resolution && size.aspectRatio === draft.aspectRatio)) {
    errors.resolution = '当前分辨率与画幅组合不可用';
  }
  const uniqueReferences = new Set(draft.references.map((image) => image.imageId));
  if (uniqueReferences.size > capabilities.effectiveReferenceLimit) errors.references = `最多选择 ${capabilities.effectiveReferenceLimit} 张参考图`;
  if (!draft.segments.length) errors.segments = '至少需要一个分段';
  draft.segments.forEach((segment, index) => {
    const prefix = `segments[${index}]`;
    if (!segment.prompt.trim()) errors[`${prefix}.prompt`] = '请输入分段提示词';
    if (!Number.isFinite(segment.durationSeconds) || segment.durationSeconds <= 0) errors[`${prefix}.duration_seconds`] = '时长必须为正数';
    if (uniqueReferences.size) {
      if (segment.firstFrame) errors[`${prefix}.first_frame`] = '参考图与首帧不可同时使用';
      if (segment.lastFrame) errors[`${prefix}.last_frame`] = '参考图与尾帧不可同时使用';
    } else if (!segment.firstFrame) {
      errors[`${prefix}.first_frame`] = segment.lastFrame ? '尾帧不能单独使用，请选择首帧' : '没有参考图时必须选择首帧';
    }
    const mode = uniqueReferences.size ? 'ref_only' : segment.lastFrame ? 'fl2v' : 'i2v';
    if (!errors[`${prefix}.first_frame`] && !errors[`${prefix}.last_frame`] &&
      capabilities.modes.length && !capabilities.modes.some((item) => item.id === mode && item.supported)) {
      errors[`${prefix}.first_frame`] = `${mode} 模式当前不可用`;
    }
  });
  return errors;
}
