import type { CaseProposal } from '@/api/caseReference';
import { draftToWire } from '@/api/videoDirector';
import type { DirectorDraft } from '../domain/canvasNodes';

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
    .map(([key, entry]) => `${JSON.stringify(key)}:${canonical(entry)}`).join(',')}}`;
  return JSON.stringify(value);
}

export function assertCaseProposalCurrent(draft: DirectorDraft, effective: DirectorDraft, proposal: CaseProposal) {
  if (proposal.status !== 'succeeded' || !proposal.result?.compatible || proposal.result.conflict) throw new Error('参考方案尚未成功或已取消');
  if (!draft.segments.some((segment) => segment.id === proposal.input.segment_id)
    || canonical(draftToWire(draft)) !== canonical(proposal.input.draft)
    || canonical(draftToWire(effective)) !== canonical(proposal.input.effective_draft)) {
    throw new Error('草稿或参考图已变化，方案已过期，请重新生成');
  }
}

export async function applyCaseReference(draft: DirectorDraft, effective: DirectorDraft, proposal: CaseProposal, editedPrompt: string): Promise<DirectorDraft> {
  assertCaseProposalCurrent(draft, effective, proposal);
  if (!editedPrompt.trim() || editedPrompt.length > 20000) throw new Error('请填写有效的建议提示词（最多 20000 字）');
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(editedPrompt));
  const hash = [...new Uint8Array(digest)].map((n) => n.toString(16).padStart(2, '0')).join('');
  return { ...draft, revision: draft.revision + 1, segments: draft.segments.map((segment) => segment.id !== proposal.input.segment_id ? segment : {
    ...segment, prompt: editedPrompt, caseReference: {
      proposal_id: proposal.id, case_id: proposal.input.case_id, dimensions: [...proposal.input.dimensions],
      source_prompts: structuredClone(proposal.evidence.prompts), description: proposal.evidence.description,
      applied_at: new Date().toISOString(), prompt_sha256: hash,
    },
  }) };
}
