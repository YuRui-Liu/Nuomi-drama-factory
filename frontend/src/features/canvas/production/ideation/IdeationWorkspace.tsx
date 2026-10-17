import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScriptSettingsContent } from '@/features/script-creation/script-settings-content';
import type { IdeaFields } from '../creativeWorkflowApi';
import type { ProductionNodeShellControls } from '../ProductionNodeShell';
import { IdeaDirections } from './IdeaDirections';
import { useIdeationWorkspace, type IdeationWorkspacePage, type UseIdeationWorkspaceOptions } from './useIdeationWorkspace';
import { RevisionImpactPanel } from '../RevisionImpactPanel';
import './ideation-workspace.css';

type Props = Omit<UseIdeationWorkspaceOptions, 'forceClose'> & {
  controls: ProductionNodeShellControls;
};

const manualFields: [keyof IdeaFields, string][] = [
  ['one_line', '一句话立意'], ['protagonist_conflict', '主角与核心冲突'],
  ['highlights', '故事看点'], ['final_choice', '人物最终抉择'],
];

function ManualIdeaForm({ value, onChange, disabled, onSave, onCancel }: {
  value: IdeaFields; onChange: (value: IdeaFields) => void; disabled: boolean;
  onSave: () => void; onCancel: () => void;
}) {
  const canSave = manualFields.every(([key]) => value[key].trim());
  return <section className="iw-manual-form" aria-label="自己写一个方向">
    <div className="iw-manual-heading"><div><h3>写下自己的立意</h3><p>四项都可以随时再修改，保存后会出现在立意方向列表中。</p></div>
      <button type="button" className="iw-icon-button" aria-label="关闭自写方向" onClick={onCancel}><X size={16} /></button></div>
    <div className="iw-manual-fields">{manualFields.map(([key, label]) => <label key={key}>{label}
      <textarea aria-label={label} value={value[key]} rows={key === 'one_line' ? 2 : 3}
        disabled={disabled} onChange={event => onChange({ ...value, [key]: event.target.value })} />
    </label>)}</div>
    <div className="iw-manual-actions"><Button variant="outline" disabled={disabled} onClick={onCancel}>取消</Button>
      <Button disabled={disabled || !canSave} onClick={onSave}>保存自写方向</Button></div>
  </section>;
}

const pageCopy: Record<IdeationWorkspacePage, { heading: string; copy: string }> = {
  creative: { heading: '先把故事的底色调出来', copy: '选择一个主题，再让另一种题材加入；不用一次想完整个故事。' },
  directions: { heading: '给这个故事，试着找三个落点', copy: '每个方向都从同一颗故事种子出发，只是把情感、冲突和主题放在不同的位置。' },
};

export function IdeationWorkspace({ project, nodeId, data, registerCloseGuard, controls }: Props) {
  const workspace = useIdeationWorkspace({ project, nodeId, data, registerCloseGuard, forceClose: controls.forceClose });
  const page = workspace.page;
  const pageInfo = pageCopy[page];
  const briefRevisionConsumers = new Map<string, string[]>();
  const addBriefConsumer = (revision: string | undefined, consumer: string) => {
    if (!revision) return;
    briefRevisionConsumers.set(revision, [...(briefRevisionConsumers.get(revision) ?? []), consumer]);
  };
  addBriefConsumer(workspace.selected?.source_brief_revision_id, '当前查看的立意方向');
  addBriefConsumer(workspace.adopted?.brief_revision_id, '已采用方向与后续故事规划、剧本');
  if (!briefRevisionConsumers.size) addBriefConsumer(workspace.run?.brief_revision_id, '本次立意运行');
  const briefDependencies = workspace.briefDocument ? [...briefRevisionConsumers].map(([revisionId, consumers]) => ({
    document: workspace.briefDocument!, revisionId, downstream: consumers.join('；'),
  })) : [];
  const runActive = ['queued', 'pending', 'running'].includes(workspace.run?.status ?? '');
  const generationDisabled = workspace.busy || workspace.docsLoading || Boolean(workspace.docsError) || runActive;
  const errors = [workspace.actionError, workspace.uiError].filter(Boolean);

  return <main className="ideation-workspace" aria-label="故事创作工作台">
    <header className="iw-topbar">
      <div className="iw-title-block"><span className="iw-eyebrow">故事创作 / 灵感起点</span>
        <h1>从灵感，找到故事的方向</h1></div>
      <nav className="iw-step-nav" aria-label="创作步骤">
        <button type="button" role="tab" aria-selected={page === 'creative'} aria-current={page === 'creative' ? 'step' : undefined}
          className="iw-step-button" onClick={() => workspace.requestPage('creative')}>
          <span className="iw-step-index">01</span><span>题材创意</span>
        </button>
        <span className="iw-step-connector" aria-hidden="true" />
        <button type="button" role="tab" aria-selected={page === 'directions'} aria-current={page === 'directions' ? 'step' : undefined}
          className="iw-step-button" disabled={!workspace.hasWorkflow}
          onClick={() => workspace.requestPage('directions')}>
          <span className="iw-step-index">02</span><span>立意方向</span>
        </button>
      </nav>
      <button type="button" className="iw-close" aria-label="关闭故事创作工作台" onClick={controls.requestClose}><X size={18} /></button>
    </header>

    <div className="iw-page-heading"><div><h2>{pageInfo.heading}</h2><p>{pageInfo.copy}</p></div>
      <span className="iw-start-note"><i aria-hidden="true" />{page === 'creative' ? '轻松开始，随时可改' : '选择之后仍可修改'}</span>
    </div>

    <div className="iw-page-content">
      {page === 'creative' ? <div className="iw-creative-scroll">
        {workspace.docsError && <div className="iw-query-error" role="alert"><span>{workspace.docsError}</span>
          <Button variant="outline" size="sm" onClick={workspace.retryDocuments}>重试读取</Button></div>}
        {errors.map((message, index) => <p key={`${index}:${message}`} role="alert" className="iw-inline-error">{message}</p>)}
        <ScriptSettingsContent value={workspace.settings} onChange={workspace.changeSettings} disabled={workspace.busy} />
        <div className={`iw-save-state iw-save-state-${workspace.briefSaveStatus}`} role="status" aria-live="polite">
          {workspace.briefSaveStatus === 'saving' && <span className="iw-spinner" aria-hidden="true" />}
          <span>{workspace.briefSaveStatus === 'saving' ? '正在保存创作简报…'
            : workspace.briefSaveStatus === 'saved' ? '创作简报已保存'
              : workspace.briefSaveStatus === 'failed' ? '保存失败，可修改后重试'
                : '创作简报尚未保存'}</span>
        </div>
        {!workspace.manualOpen
          ? <button type="button" className="iw-manual-entry" onClick={workspace.openManual}>
            <span aria-hidden="true">＋</span><span><strong>自己写一个方向</strong><small>已经有了想法？直接保存为一个立意方向</small></span>
          </button>
          : <ManualIdeaForm value={workspace.manualDraft} onChange={workspace.changeManualDraft} disabled={workspace.busy}
            onSave={() => void workspace.createManualIdea()} onCancel={workspace.closeManual} />}
      </div> : <div className="iw-directions-scroll">
        {workspace.ideaError && <div className="iw-query-error" role="alert"><span>{workspace.ideaError}</span>
          <Button variant="outline" size="sm" onClick={workspace.retryIdea}>重试读取</Button></div>}
        {errors.map((message, index) => <p key={`${index}:${message}`} role="alert" className="iw-inline-error">{message}</p>)}
        {workspace.ideaLoading && !workspace.run && <p className="iw-loading-line" role="status"><span className="iw-spinner" />正在读取立意方向…</p>}
        {workspace.candidateCount > 0 && <IdeaDirections
          run={workspace.run} selected={workspace.selected} selectedIndex={workspace.selectedIndex}
          draft={workspace.selectedDraft} editing={workspace.editing} busy={workspace.busy}
          regenerationStatus={workspace.selectedRegenerationStatus} regenerationError={workspace.selectedRegenerationError}
          onSelect={workspace.requestCandidate} onEdit={workspace.startCandidateEdit} onCancel={workspace.cancelCandidateEdit}
          onDraftChange={workspace.changeCandidateDraft} onSave={() => void workspace.saveSelectedCandidate()}
          onRedo={() => void workspace.redoSelected()} />}
        {workspace.candidateCount === 0 && runActive && <div className="iw-no-run iw-run-progress" role="status">
          <span className="iw-spinner" aria-hidden="true" /><h3>{workspace.runStatusLabel || '正在生成立意方向'}</h3>
          <p>方向整理完成后会保留在这里，你可以逐个查看、编辑和选择。</p>
        </div>}
        {workspace.candidateCount === 0 && !runActive && !workspace.ideaLoading && !workspace.ideaError && <div className="iw-no-run">
          <h3>还没有立意方向</h3><p>回到题材创意，保存故事底色后开始生成。</p>
          <Button variant="outline" onClick={() => workspace.requestPage('creative')}>返回题材创意</Button>
        </div>}
        <RevisionImpactPanel project={project} dependencies={briefDependencies} title="创作简报修订与立意影响" />
      </div>}
    </div>

    <footer className="iw-page-actions">
      {page === 'creative' ? <>
        <p className="iw-footer-copy"><strong>设定只是开始</strong><span>每一项都可以留白，故事会保留更多可能。</span></p>
        <div className="iw-action-buttons">
          <Button variant="ghost" disabled={workspace.busy || workspace.docsLoading || Boolean(workspace.docsError)} onClick={() => void workspace.saveDraft()}>
            {workspace.briefSaveStatus === 'saving' ? '保存中…' : '保存草稿'}
          </Button>
          <Button disabled={generationDisabled} onClick={() => void workspace.generateDirections()}>
            {workspace.briefSaveStatus === 'saving' ? '正在保存简报…' : workspace.busy ? '正在整理三个方向…' : '生成 3 个立意方向'}
          </Button>
        </div>
      </> : <>
        <p className="iw-footer-copy"><strong>{workspace.adopted ? '方向已采用，可以带着它进入故事规划。' : '正在查看一个方向；选择还不代表采用。'}</strong>
          <span>{workspace.runStatusLabel}{workspace.run?.error ? ` · ${workspace.run.error}` : ''}</span></p>
        <div className="iw-action-buttons"><Button variant="outline" disabled={workspace.busy} onClick={() => workspace.requestPage('creative')}>返回题材创意</Button>
          {workspace.selected && workspace.canPlan
            ? <Button disabled={workspace.busy || workspace.editing || workspace.candidateDirty} onClick={() => void workspace.planSelected()}>创作 E01 · 先规划</Button>
            : <Button disabled={workspace.busy || workspace.editing || workspace.candidateDirty || !workspace.selected} onClick={() => void workspace.adoptSelected()}>
              {workspace.selected && workspace.adopted ? '更换为此方向' : '采用此方向'}
            </Button>}
        </div>
      </>}
    </footer>

    {workspace.leaveIntent && workspace.candidateDirty && <div className="iw-confirm-layer" role="presentation">
      <section className="iw-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="iw-confirm-title" aria-describedby="iw-confirm-copy">
        <h2 id="iw-confirm-title">方向修改尚未保存</h2><p id="iw-confirm-copy">先保存当前方向，或放弃这次修改再离开。</p>
        <div className="iw-confirm-actions"><Button variant="ghost" onClick={workspace.stay}>继续编辑</Button>
          <Button variant="outline" onClick={workspace.discardAndLeave}>放弃修改</Button>
          <Button disabled={workspace.busy} onClick={() => void workspace.saveAndLeave()}>
            {workspace.leaveIntent.kind === 'close' ? '保存并关闭' : '保存并继续'}
          </Button></div>
      </section>
    </div>}
  </main>;
}
