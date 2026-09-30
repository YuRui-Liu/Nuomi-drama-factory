import type { DirectorSegment } from '../domain/canvasNodes';
import type { DirectorCapabilities } from '@/api/videoDirector';
import { alignDirectorDuration, type DirectorErrors } from './directorValidation';
import { useTranslation } from 'react-i18next';
import { DirectorImageSlot } from './DirectorImageSlot';

interface Props {
  segment: DirectorSegment;
  index: number;
  count: number;
  capabilities: DirectorCapabilities | null;
  errors: DirectorErrors;
  onPatch: (patch: Partial<DirectorSegment>) => void;
  onPick: (field: 'firstFrame' | 'lastFrame') => void;
  onUpload: (field: 'firstFrame' | 'lastFrame', file: File) => void;
  onRemove: (field: 'firstFrame' | 'lastFrame') => void;
  imageErrors: Record<string, string>;
  uploading: Record<string, boolean>;
  onCopy: () => void;
  onDelete: () => void;
  onMove: (offset: number) => void;
}

export function DirectorSegmentEditor({ segment, index, count, capabilities, errors, onPatch, onPick, onUpload, onRemove, imageErrors, uploading, onCopy, onDelete, onMove }: Props) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string) => t(`node.videoDirector.editor.${key}`, { defaultValue });
  const prefix = `segments[${index}]`;
  const aligned = capabilities && alignDirectorDuration(segment.durationSeconds, capabilities);
  return <section className="rounded-xl border border-white/10 bg-white/[0.035] p-3" aria-label={`${tr('segment', '分段')} ${index + 1}`}>
    <div className="mb-3 flex items-center gap-2 text-sm">
      <strong className="mr-auto">{tr('segment', '分段')} {index + 1}</strong>
      <button type="button" onClick={() => onMove(-1)} disabled={index === 0}>{tr('moveUp', '上移')}</button>
      <button type="button" onClick={() => onMove(1)} disabled={index === count - 1}>{tr('moveDown', '下移')}</button>
      <button type="button" onClick={onCopy}>{tr('copy', '复制')}</button>
      <button type="button" onClick={onDelete} disabled={count === 1}>{tr('delete', '删除')}</button>
    </div>
    <label className="block text-xs text-text-muted">{tr('prompt', '提示词')}
      <textarea className="mt-1 min-h-20 w-full rounded-lg border border-white/10 bg-black/20 p-2 text-sm text-white" value={segment.prompt}
        onChange={(event) => onPatch({ prompt: event.target.value })} />
    </label>
    {errors[`${prefix}.prompt`] && <p className="text-xs text-red-300">{errors[`${prefix}.prompt`]}</p>}
    <label className="mt-3 block text-xs text-text-muted">{tr('duration', '时长（秒）')}
      <input type="number" min="0.01" step="0.1" className="ml-2 w-24 rounded border border-white/10 bg-black/20 p-1 text-white"
        value={Number.isFinite(segment.durationSeconds) ? segment.durationSeconds : ''}
        onChange={(event) => onPatch({ durationSeconds: event.target.value === '' ? Number.NaN : Number(event.target.value) })} />
    </label>
    {aligned && aligned.frames > 0 && <p className="text-xs text-text-muted">{tr('aligned', 'H3 对齐')}：{aligned.frames} {tr('frames', '帧')} / {aligned.seconds.toFixed(2)} {tr('seconds', '秒')}</p>}
    {errors[`${prefix}.duration_seconds`] && <p className="text-xs text-red-300">{errors[`${prefix}.duration_seconds`]}</p>}
    <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
      {(['firstFrame', 'lastFrame'] as const).map((field) => <div key={field}>
        <DirectorImageSlot label={field === 'firstFrame' ? tr('firstFrame', '首帧') : tr('lastFrame', '尾帧')}
          image={segment[field]} error={imageErrors[`${segment.id}.${field}`]}
          uploading={uploading[`${segment.id}.${field}`]}
          onPick={() => onPick(field)} onUpload={(file) => onUpload(field, file)} onRemove={() => onRemove(field)} />
        {errors[`${prefix}.${field === 'firstFrame' ? 'first_frame' : 'last_frame'}`] &&
          <p className="text-red-300">{errors[`${prefix}.${field === 'firstFrame' ? 'first_frame' : 'last_frame'}`]}</p>}
      </div>)}
    </div>
  </section>;
}
