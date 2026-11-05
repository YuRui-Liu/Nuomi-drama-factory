import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import type { AgentNodeData } from '@/features/canvas/domain/canvasNodes';
import type { IdeationCandidate, IdeationCandidateBody, IdeationObject } from '@/api/ideation';
import { runtimeStateLabel } from './runtimeModel';
import { useAgentNodeRuntime, type AgentNodeHistoryItem } from './useAgentNodeRuntime';

type CandidateFields = keyof IdeationCandidateBody;
const fields: { key: CandidateFields; label: string }[] = [
  { key: 'title', label: '标题' },
  { key: 'premise', label: '故事前提' },
  { key: 'protagonist_goal', label: '主角目标' },
  { key: 'conflict', label: '核心冲突' },
  { key: 'hook', label: '叙事钩子' },
  { key: 'expansion', label: '拓展方向' },
];
const ACTIVE = new Set(['queued', 'model_inflight', 'applying']);
const RESUMABLE = new Set(['queued', 'applying', 'stopped', 'blocked']);

interface Props {
  nodeId: string;
  agentRef: AgentNodeData['agentRef'];
  saveSettings: () => Promise<boolean>;
  isIdeation: boolean;
}

interface CandidateDialogTarget {
  runId: string;
  objectId: string;
  candidateId: string;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function referenceLabel(ref: { kind: string; node_id?: string; candidate_id?: string; revision?: number }, nodes: ReturnType<typeof useAgentNodeRuntime>['nodes']): string {
  if (ref.kind === 'ideation_selection') return `已采纳候选 ${ref.candidate_id} · v${ref.revision}`;
  const node = nodes.find((item) => item.id === ref.node_id);
  return String(node?.data.displayName || node?.data.title || node?.data.label || node?.type || ref.node_id || '输入节点');
}

export function AgentNodeRuntimePanel({ nodeId, agentRef, saveSettings, isIdeation }: Props) {
  const { t } = useTranslation();
  const runtime = useAgentNodeRuntime({ nodeId, agentRef, saveSettings });
  const [dialog, setDialog] = useState<CandidateDialogTarget | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<IdeationCandidateBody | null>(null);

  const dialogItem = dialog ? runtime.history.find((item) => item.run.id === dialog.runId) : undefined;
  const dialogOutput = dialog && dialogItem
    ? dialogItem.outputs.find((entry) => entry.object?.object_id === dialog.objectId)
    : undefined;
  const dialogObject = dialogOutput?.object;
  const dialogCandidate = dialogObject?.candidates.find((candidate) => candidate.id === dialog?.candidateId);
  const hasUnknownRun = runtime.history.some((item) => item.run.state === 'submission_unknown');
  const adopted = dialogObject && dialogCandidate
    ? (() => {
        const ref = runtime.nodes.find((item) => item.id === nodeId)?.data as AgentNodeData | undefined;
        return ref?.adoptedRef?.object_id === dialogObject.object_id
          && ref.adoptedRef.revision === dialogObject.revision
          && ref.adoptedRef.candidate_id === dialogCandidate.id;
      })()
    : false;

  const chooseCandidate = (item: AgentNodeHistoryItem, object: IdeationObject, candidate: IdeationCandidate) => {
    setDialog({ runId: item.run.id, objectId: object.object_id, candidateId: candidate.id });
    setEditing(false);
    setDraft(Object.fromEntries(fields.map(({ key }) => [key, candidate[key]])) as IdeationCandidateBody);
  };

  return (
    <section className="agent-runtime-panel nodrag nowheel" aria-label={t('canvas.agentNode.runtimeTitle')}>
      <div className="agent-runtime-heading">
        <strong>{t('canvas.agentNode.runtimeTitle')}</strong>
        <button type="button" className="agent-runtime-refresh" onClick={() => void runtime.refresh()} disabled={runtime.loading}>
          {runtime.loading ? t('canvas.agentNode.runtimeRefreshing') : t('canvas.agentNode.runtimeRefresh')}
        </button>
      </div>

      <div className="agent-runtime-controls">
        <label>
          <span>{t('canvas.agentNode.runtimeCallLimit')}</span>
          <select
            className="nodrag nowheel"
            aria-label={t('canvas.agentNode.runtimeCallLimit')}
            value={runtime.maxModelCalls}
            onChange={(event) => runtime.setMaxModelCalls(Number(event.target.value))}
            disabled={runtime.working}
          >
            {[1, 2, 3].map((count) => <option key={count} value={count}>{count}</option>)}
          </select>
        </label>
        <Button
          type="button"
          className="agent-runtime-generate nodrag"
          onClick={() => void runtime.generate()}
          disabled={runtime.working || hasUnknownRun}
        >
          {runtime.working ? t('canvas.agentNode.runtimeWorking') : t('canvas.agentNode.runtimeGenerate')}
        </Button>
        {hasUnknownRun && <Button
          type="button"
          variant="outline"
          className="nodrag"
          disabled={runtime.working}
          onClick={() => {
            if (window.confirm(t('canvas.agentNode.runtimeUnknownRepeatConfirm'))) void runtime.generate(true);
          }}
        >{t('canvas.agentNode.runtimeUnknownRepeat')}</Button>}
      </div>

      {runtime.runtimeStatus && !runtime.runtimeStatus.available && (
        <p className="agent-runtime-status" role="status">{t('canvas.agentNode.runtimeUnavailable')}: {runtime.runtimeStatus.code}</p>
      )}
      {runtime.pendingIntent?.phase === 'pending' && (
        <div className="agent-runtime-pending" role="status">
          <span>{t('canvas.agentNode.runtimePending')}</span>
          <button type="button" onClick={() => void runtime.resumePending()} disabled={runtime.working}>{t('canvas.agentNode.runtimeRestore')}</button>
        </div>
      )}
      {runtime.pendingWrite && (
        <div className="agent-runtime-pending" role="status">
          <span>{t('canvas.agentNode.runtimeWritePending')}</span>
          <button type="button" onClick={() => void runtime.recoverCandidateWrite()} disabled={runtime.working}>{t('canvas.agentNode.runtimeRestore')}</button>
        </div>
      )}

      {runtime.incoming.unadoptedAgentNodeIds.length > 0 && (
        <p className="agent-runtime-warning" role="status">
          {t('canvas.agentNode.runtimeUnadopted')}：{runtime.incoming.unadoptedAgentNodeIds.map((id) => {
            const node = runtime.nodes.find((item) => item.id === id);
            return String(node?.data.displayName || node?.data.title || node?.type || id);
          }).join('、')}
        </p>
      )}
      {runtime.incoming.refs.length > 0 && (
        <details className="agent-runtime-inputs">
          <summary>{t('canvas.agentNode.runtimeInputs')} · {runtime.incoming.refs.length}</summary>
          <ul>{runtime.incoming.refs.map((ref, index) => <li key={`${ref.kind}:${index}`}>{referenceLabel(ref, runtime.nodes)}</li>)}</ul>
        </details>
      )}

      {runtime.error && <p className="agent-runtime-error" role="alert">{runtime.error}</p>}
      {runtime.notice && <p className="agent-runtime-notice" role="status">{runtime.notice}</p>}

      <div className="agent-runtime-history" aria-label={t('canvas.agentNode.runtimeHistory')}>
        {runtime.history.length === 0 ? (
          <p className="agent-runtime-empty">{runtime.conversationId ? t('canvas.agentNode.runtimeNoResults') : t('canvas.agentNode.runtimeNoConversation')}</p>
        ) : runtime.history.map((item) => (
          <article className="agent-runtime-run" key={item.run.id}>
            <div className="agent-runtime-run-heading">
              <strong>{runtimeStateLabel(item.run.state)}</strong>
              {item.inputSignature && runtime.isStale(item) && <span className="agent-runtime-stale">{t('canvas.agentNode.runtimeStale')}</span>}
              <span className="agent-runtime-version">v{item.run.revision}</span>
            </div>
            {item.run.state === 'submission_unknown' && <p className="agent-runtime-output-error">{t('canvas.agentNode.runtimeUnknownHelp')}</p>}
            {item.run.error?.message && <p className="agent-runtime-output-error">{item.run.error.message}</p>}
            {item.outputs.map((entry, index) => {
              const output = entry.output;
              const object = entry.object;
              const raw = entry.rawText ?? text(output.object?.payload?.text);
              if (isIdeation && output.operation === 'proposal.create') {
                return (
                  <div key={`${item.run.id}:${index}`} className="agent-runtime-output">
                    {entry.parseError || !object ? (
                      <div className="agent-runtime-output-error">
                        <p>{entry.parseError || t('canvas.agentNode.runtimeInvalidCandidates')}</p>
                        {raw && <details><summary>{t('canvas.agentNode.runtimeRawProposal')}</summary><pre>{raw}</pre></details>}
                      </div>
                    ) : (
                      <>
                        <p className="agent-runtime-source">{t('canvas.agentNode.runtimeSource')} · v{object.revision} · {item.run.state}</p>
                        <div className="agent-runtime-candidate-list">
                          {object.candidates.map((candidate) => (
                            <button
                              type="button"
                              key={candidate.id}
                              className="agent-runtime-candidate"
                              onClick={() => chooseCandidate(item, object, candidate)}
                            >
                              <strong>{candidate.title}</strong>
                              <span>{candidate.hook}</span>
                              <small>{candidate.conflict}</small>
                            </button>
                          ))}
                        </div>
                        <button type="button" className="agent-runtime-compare" onClick={() => chooseCandidate(item, object, object.candidates[0])}>
                          {t('canvas.agentNode.runtimeCompareEdit')}
                        </button>
                      </>
                    )}
                  </div>
                );
              }
              if (output.operation === 'proposal.create' && raw) {
                return <div key={`${item.run.id}:${index}`} className="agent-runtime-generic-output"><strong>{output.title || t('canvas.agentNode.runtimeProposal')}</strong><p>{raw}</p></div>;
              }
              return null;
            })}
            {ACTIVE.has(item.run.state) && (
              <button type="button" className="agent-runtime-run-action" disabled={runtime.working} onClick={() => void runtime.stopRun(item)}>{t('canvas.agentNode.runtimeStop')}</button>
            )}
            {RESUMABLE.has(item.run.state) && (
              <button type="button" className="agent-runtime-run-action" disabled={runtime.working} onClick={() => void runtime.resumeRun(item)}>{t('canvas.agentNode.runtimeResume')}</button>
            )}
          </article>
        ))}
      </div>

      <Dialog open={Boolean(dialog)} onOpenChange={(open) => { if (!open) setDialog(null); }}>
        <DialogContent className="agent-candidate-dialog" overlayClassName="z-[11000]" style={{ zIndex: 11001 }}>
          <DialogTitle>{dialogCandidate?.title || t('canvas.agentNode.runtimeCandidate')}</DialogTitle>
          <DialogDescription>
            {dialogObject && `${t('canvas.agentNode.runtimeSource')} · ${dialogObject.source.delegation_id} · v${dialogObject.revision}`}
          </DialogDescription>
          {dialogObject && <div className="agent-candidate-compare" aria-label={t('canvas.agentNode.runtimeCandidateCompare')}>
            {dialogObject.candidates.map((candidate) => (
              <button
                type="button"
                key={candidate.id}
                aria-pressed={candidate.id === dialogCandidate?.id}
                onClick={() => chooseCandidate(dialogItem!, dialogObject, candidate)}
              >
                <strong>{candidate.title}</strong><span>{candidate.premise}</span><small>{candidate.hook}</small>
              </button>
            ))}
          </div>}
          {dialogCandidate && draft && (
            <div className="agent-candidate-detail">
              {fields.map(({ key, label }) => (
                <label key={key}>
                  <span>{label}</span>
                  {editing
                    ? <textarea className="nodrag nowheel" value={draft[key]} onChange={(event) => setDraft((value) => value ? { ...value, [key]: event.target.value } : value)} />
                    : <p>{dialogCandidate[key]}</p>}
                </label>
              ))}
            </div>
          )}
          <div className="agent-candidate-actions">
            {dialogCandidate && !editing && <Button type="button" variant="outline" onClick={() => setEditing(true)}>{t('canvas.agentNode.runtimeEdit')}</Button>}
            {dialogCandidate && editing && <Button
              type="button"
              variant="outline"
              disabled={runtime.working || !dialogItem || !dialogObject}
              onClick={async () => {
                if (!dialogItem || !dialogObject || !dialogCandidate || !draft) return;
                if (await runtime.saveCandidateEdit(dialogItem, dialogObject, dialogCandidate, draft)) {
                  setEditing(false);
                  setDialog(null);
                }
              }}
            >{runtime.working ? t('canvas.agentNode.runtimeWorking') : t('canvas.agentNode.runtimeSaveVersion')}</Button>}
            {dialogCandidate && dialogObject && dialogItem && <Button
              type="button"
              disabled={runtime.working || adopted || Boolean(dialogOutput?.parseError)}
              onClick={async () => {
                if (await runtime.adoptCandidate(dialogItem, dialogObject, dialogCandidate)) setDialog(null);
              }}
            >{adopted ? t('canvas.agentNode.runtimeAdopted') : t('canvas.agentNode.runtimeAdopt')}</Button>}
          </div>
          {dialogOutput?.parseError && <p className="agent-runtime-error" role="alert">{dialogOutput.parseError}</p>}
        </DialogContent>
      </Dialog>
    </section>
  );
}
