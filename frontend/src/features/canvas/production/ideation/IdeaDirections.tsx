import { Button } from '@/components/ui/button';
import type { IdeaCandidate, IdeaFields, IdeationRun } from '../creativeWorkflowApi';

const fields: [keyof IdeaFields, string][] = [
  ['one_line', '一句话立意'],
  ['protagonist_conflict', '主角与核心冲突'],
  ['highlights', '故事看点'],
  ['final_choice', '人物最终抉择'],
];

const statusLabel: Record<string, string> = {
  queued: '排队中', pending: '准备中', running: '正在生成', completed: '已完成', failed: '失败',
};

export interface IdeaDirectionsProps {
  run?: IdeationRun;
  selected?: IdeaCandidate;
  selectedIndex: number;
  draft?: IdeaFields;
  editing: boolean;
  busy: boolean;
  regenerationStatus?: string;
  regenerationError?: string | null;
  onSelect: (candidate: IdeaCandidate) => void;
  onEdit: () => void;
  onCancel: () => void;
  onDraftChange: (value: IdeaFields) => void;
  onSave: () => void;
  onRedo: () => void;
}

function FieldList({ value }: { value: IdeaFields }) {
  return <dl className="iw-fields">
    {fields.map(([key, label]) => <div className="iw-field" key={key}>
      <dt>{label}</dt><dd>{value[key] || '尚未填写'}</dd>
    </div>)}
  </dl>;
}

export function IdeaDirections({ run, selected, selectedIndex, draft, editing, busy, regenerationStatus, regenerationError,
  onSelect, onEdit, onCancel, onDraftChange, onSave, onRedo }: IdeaDirectionsProps) {
  const adopted = run?.adopted ?? null;
  const isSelectedAdopted = Boolean(selected && adopted?.candidate_id === selected.id && adopted.revision_id === selected.revision_id);
  const adoptedIndex = run?.candidates.findIndex(candidate => candidate.id === adopted?.candidate_id && candidate.revision_id === adopted.revision_id) ?? -1;
  const dirty = Boolean(selected && draft && fields.some(([key]) => draft[key] !== selected[key]));
  const regenerationActive = ['queued', 'pending', 'running'].includes(regenerationStatus ?? '');

  return <div className="iw-directions-layout">
    <aside className="iw-direction-list" aria-label="立意方向列表">
      <div className="iw-section-heading"><div><h2>三个立意方向</h2><p>先读一读，再选一个最想继续写的。</p></div>
        {run?.status && <span className="iw-status-pill">{statusLabel[run.status] ?? run.status}</span>}</div>
      {run?.error && <p role="alert" className="iw-inline-error">{run.error}</p>}
      {run?.candidates.length ? <div className="iw-candidate-list">
        {run.candidates.map((candidate, index) => {
          const isSelected = selected?.id === candidate.id && selected.revision_id === candidate.revision_id;
          const isAdopted = adopted?.candidate_id === candidate.id && adopted.revision_id === candidate.revision_id;
          return <button key={`${candidate.id}:${candidate.revision_id}`} type="button"
            aria-label={`查看方向 ${index + 1}：${candidate.one_line}`} aria-pressed={isSelected}
            className="iw-candidate-option" onClick={() => onSelect(candidate)}>
            <span className="iw-candidate-number">{String(index + 1).padStart(2, '0')}</span>
            <span className="iw-candidate-copy"><strong>{candidate.one_line || `立意方向 ${index + 1}`}</strong>
              <span>{candidate.protagonist_conflict || '核心冲突待补充'}</span>
              {isAdopted && <em>已采用</em>}
            </span>
          </button>;
        })}
      </div> : <div className="iw-empty-directions" role="status">
        {run?.status && ['queued', 'pending', 'running'].includes(run.status) ? '正在整理立意方向，完成后会显示在这里。' : '生成后，三个立意方向会显示在这里。'}
      </div>}
    </aside>

    <article className="iw-direction-detail" aria-live="polite">
      {!selected ? <div className="iw-detail-empty"><span className="iw-detail-mark">✦</span><h2>等待方向生成</h2><p>回到题材创意，生成方向后再逐个查看。</p></div>
        : <>
          <div className="iw-detail-topline"><span>立意方向 / {String(selectedIndex + 1).padStart(2, '0')}</span>
            {!editing && <div className="iw-detail-tools">
              <Button variant="outline" size="sm" disabled={busy || regenerationActive} onClick={onEdit}>编辑方向</Button>
              <Button variant="ghost" size="sm" disabled={busy || dirty || editing || regenerationActive} onClick={onRedo}>仅重做此方向</Button>
            </div>}
          </div>
          {regenerationStatus && <div className="iw-regeneration-status" role="status">
            {regenerationActive && <span className="iw-spinner" aria-hidden="true" />}{statusLabel[regenerationStatus] ?? regenerationStatus}
            {regenerationActive ? ' · 正在为这个方向整理另一种表达' : regenerationError ? ` · ${regenerationError}` : ''}
          </div>}
          {regenerationStatus === 'failed' && <p className="iw-inline-error" role="alert">
            {regenerationError || '这个方向暂时没有重做成功。'} 可以稍后重试。
          </p>}
          <h2 className="iw-detail-title">{selected.one_line || `立意方向 ${selectedIndex + 1}`}</h2>
          <p className="iw-detail-lead">{selected.protagonist_conflict || '从故事的核心冲突开始，逐步找到人物的选择。'}</p>
          <div className="iw-detail-rule" />
          {editing && draft ? <div className="iw-edit-form" aria-label="编辑立意方向">
            {fields.map(([key, label]) => <label className="iw-edit-field" key={key}>{label}
              <textarea aria-label={label} value={draft[key]} rows={key === 'one_line' ? 2 : 3}
                disabled={busy}
                onChange={event => onDraftChange({ ...draft, [key]: event.target.value })} />
            </label>)}
            <div className="iw-edit-actions"><Button variant="outline" disabled={busy} onClick={onCancel}>取消修改</Button>
              <Button disabled={busy || !dirty} onClick={onSave}>保存方向</Button></div>
          </div> : <FieldList value={selected} />}
          {!editing && <>
            {isSelectedAdopted
              ? <div className="iw-adoption-status" role="status"><strong>已采用此方向。</strong> 继续阅读其他方向不会自动改变采用状态。</div>
              : adoptedIndex >= 0
                ? <div className="iw-adoption-status" role="status"><strong>当前查看方向尚未采用。</strong> 方向 {adoptedIndex + 1} 仍已采用；采用当前方向会替换它。</div>
                : <div className="iw-adoption-status" role="status"><strong>当前查看方向，尚未采用。</strong> 只有点击「采用此方向」，才会进入下一步。</div>}
          </>}
        </>}
    </article>
  </div>;
}
