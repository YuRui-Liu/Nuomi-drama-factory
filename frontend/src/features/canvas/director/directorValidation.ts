import type { DirectorDraft, DirectorSegment } from '../domain/canvasNodes';
import type { DirectorCapabilities, TechniqueCard } from '@/api/videoDirector';

export type DirectorErrors = Record<string, string>;

/** Preserve domain error strings for callers while localizing the visible editor/card copy. */
export function directorErrorText(error: string, t: (key: string, options?: Record<string, unknown>) => string): string {
  const techniqueMessages: Record<string, string> = {
    techniqueCatalogUnavailable: '卡片目录暂不可用，请清除已选卡片后生成',
    techniqueUnknown: '所选卡片版本已失效，请换卡或清除',
    techniqueRetired: '卡片已停用',
    techniqueModeUnsupported: '卡片不支持当前模式',
    techniqueDurationShort: '对齐后时长短于卡片下限',
    techniqueDurationLong: '对齐后时长超过卡片上限',
    techniqueLastFrameRequired: '卡片需要尾帧',
    techniqueLastFrameForbidden: '卡片不支持尾帧',
  };
  if (techniqueMessages[error]) return t(`node.videoDirector.techniques.${error}`, { defaultValue: techniqueMessages[error] });
  const keys: Record<string, string> = {
    'Connected reference image is unavailable': 'connectedReferenceUnavailable',
    'Connected frame image is unavailable': 'connectedFrameUnavailable',
    '当前模型不可用，请选择可用模型': 'modelUnavailable',
    '当前分辨率不可用': 'resolutionUnavailable',
    '当前画幅不可用': 'aspectUnavailable',
    '当前分辨率与画幅组合不可用': 'sizeUnavailable',
    '至少需要一个分段': 'segmentsRequired',
    '请输入分段提示词': 'promptRequired',
    '时长必须为正数': 'durationPositive',
    '参考图与首帧不可同时使用': 'referenceFirstConflict',
    '参考图与尾帧不可同时使用': 'referenceLastConflict',
    '尾帧不能单独使用，请选择首帧': 'lastFrameAlone',
    '没有参考图时必须选择首帧': 'firstFrameRequired',
  };
  const key = keys[error];
  if (key) return t(`node.videoDirector.errors.${key}`, { defaultValue: error });
  const limit = /^最多选择 (\d+) 张参考图$/.exec(error);
  if (limit) return t('node.videoDirector.errors.referenceLimit', { count: Number(limit[1]), defaultValue: error });
  const mode = /^(\w+) 模式当前不可用$/.exec(error);
  if (mode) return t('node.videoDirector.errors.modeUnavailable', { mode: mode[1], defaultValue: error });
  return error;
}

export function alignDirectorDuration(seconds: number, capabilities: DirectorCapabilities): { frames: number; seconds: number } {
  const { fps, frameStep, frameOffset } = capabilities;
  if (!Number.isFinite(seconds) || seconds <= 0 || fps <= 0 || frameStep <= 0) return { frames: 0, seconds: 0 };
  const frames = frameOffset + Math.max(0, Math.ceil((seconds * fps - frameOffset) / frameStep)) * frameStep;
  return { frames, seconds: frames / fps };
}

export type TechniqueIssue = 'techniqueRetired' | 'techniqueModeUnsupported' | 'techniqueDurationShort' |
  'techniqueDurationLong' | 'techniqueLastFrameRequired' | 'techniqueLastFrameForbidden';

export function techniqueCompatibility(card: TechniqueCard, segment: DirectorSegment, hasReferences: boolean,
  capabilities: DirectorCapabilities): TechniqueIssue | null {
  if (card.status !== 'active') return 'techniqueRetired';
  const mode = hasReferences ? 'ref_only' : segment.lastFrame ? 'fl2v' : 'i2v';
  if (!card.applicability.modes.includes(mode)) return 'techniqueModeUnsupported';
  if (card.applicability.last_frame_constraint === 'required' && !segment.lastFrame) return 'techniqueLastFrameRequired';
  if (card.applicability.last_frame_constraint === 'forbidden' && segment.lastFrame) return 'techniqueLastFrameForbidden';
  const aligned = alignDirectorDuration(segment.durationSeconds, capabilities).seconds;
  if (aligned > 0 && aligned < card.applicability.min_duration_seconds - 1e-9) return 'techniqueDurationShort';
  if (aligned > card.applicability.max_duration_seconds + 1e-9) return 'techniqueDurationLong';
  return null;
}

export function validateDirectorDraft(draft: DirectorDraft, capabilities: DirectorCapabilities,
  techniques: TechniqueCard[] | null = null): DirectorErrors {
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
    if (segment.technique) {
      const card = techniques?.find((item) => item.id === segment.technique?.id && item.version === segment.technique?.version);
      errors[`${prefix}.technique`] = !techniques ? 'techniqueCatalogUnavailable' : !card ? 'techniqueUnknown'
        : techniqueCompatibility(card, segment, uniqueReferences.size > 0, capabilities) ?? '';
      if (!errors[`${prefix}.technique`]) delete errors[`${prefix}.technique`];
    }
    if (!errors[`${prefix}.first_frame`] && !errors[`${prefix}.last_frame`] &&
      capabilities.modes.length && !capabilities.modes.some((item) => item.id === mode && item.supported)) {
      errors[`${prefix}.first_frame`] = `${mode} 模式当前不可用`;
    }
  });
  return errors;
}
