"""Validated model contracts for independent craft calls."""
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator


def locate_evidence(text, quote, start, end, source='current draft'):
    if start is not None and end is not None and 0 <= start < end <= len(text) and text[start:end] == quote:
        return start, end
    # Model-generated character counts are advisory. Recover only from exact,
    # unique source evidence; never fuzzy-match or choose an ambiguous passage.
    start = text.find(quote)
    if start < 0:
        raise ValueError(f'review quote is missing in {source}; copy verbatim from that source: {quote[:200]!r}')
    if text.find(quote, start + 1) >= 0:
        raise ValueError(f'review quote is ambiguous in {source}; extend to a unique verbatim passage: {quote[:200]!r}')
    return start, start + len(quote)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CraftRepairNote(StrictModel):
    issue_id: str = Field(min_length=1)
    diagnosis: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    changed_quote: str = Field(min_length=1)


class CraftDraft(StrictModel):
    scene_intents: list[str] = Field(default_factory=list)
    repair_notes: list[CraftRepairNote] = Field(default_factory=list)
    markdown: str = Field(min_length=20)

    @model_validator(mode='after')
    def addressed_issues(self, info: ValidationInfo):
        if info.context and 'repair_issues' in info.context:
            required = {i['id'] for i in info.context['repair_issues'] if i['severity'] == 'blocker'}
            given = [n.issue_id for n in self.repair_notes]
            if len(given) != len(set(given)) or set(given) != required:
                raise ValueError('repair_notes must address every blocker exactly once; no invented issue IDs')
            for note in self.repair_notes:
                if note.changed_quote not in self.markdown:
                    raise ValueError(f'repair_notes changed_quote missing from revised draft: {note.changed_quote!r}')
        return self


class CraftRewriteDraft(CraftDraft):
    """A selected fragment may be a single word or line."""
    markdown: str = Field(min_length=1)


class CraftEvidence(StrictModel):
    quote: str = Field(min_length=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1)


class CraftIssue(CraftEvidence):
    id: str = ''
    prior_issue_id: str | None = None
    basis: Literal['local', 'context'] = 'local'
    severity: Literal['blocker', 'suggestion']
    dimension: str = Field(min_length=1)
    repair_goal: str = Field(min_length=1)
    repair_scope: Literal['dialogue', 'local_action', 'story'] = 'story'
    reference_quote: str | None = None
    reference_start: int | None = Field(default=None, ge=0)
    reference_end: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def identify(self, info: ValidationInfo):
        if self.basis == 'context' and not self.reference_quote:
            raise ValueError('context issue requires reference_quote')
        if info.context and 'draft' in info.context:
            self.start, self.end = locate_evidence(info.context['draft'], self.quote, self.start, self.end)
        if self.start is None or self.end is None:
            self.id = ''  # Assigned only once source localization is available.
            return self
        value = json.dumps([self.dimension, self.basis, self.quote, self.start, self.end], ensure_ascii=False)
        self.id = 'issue_' + hashlib.sha256(value.encode()).hexdigest()[:20]
        return self


class CraftReview(StrictModel):
    summary: str = Field(min_length=1)
    issues: list[CraftIssue]
    coverage: list[str] = Field(min_length=1)
    strengths: list[CraftEvidence]
    resolved_issue_ids: list[str] = Field(default_factory=list)
    history_issue_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def evidence_for_pass(self, info: ValidationInfo):
        if not self.strengths and self.status == 'passed':
            raise ValueError('passing review requires strengths evidence')
        if not all(item.strip() for item in self.coverage):
            raise ValueError('review coverage must be substantive')
        context = info.context or {}
        if 'draft' in context:
            for item in [*self.strengths, *self.issues]:
                item.start, item.end = locate_evidence(context['draft'], item.quote, item.start, item.end)
            for issue in self.issues:
                if issue.reference_quote is not None:
                    issue.reference_start, issue.reference_end = locate_evidence(
                        context.get('reference', ''), issue.reference_quote, issue.reference_start, issue.reference_end,
                        source='task/reference (not current draft)')
                elif issue.reference_start is not None or issue.reference_end is not None:
                    raise ValueError('reference quote required for reference offsets')
        if 'issue_history' in context:
            history = {item['id']: item for item in context['issue_history']}
            known = set(history)
            claimed = set()
            for item in self.issues:
                lineage = {item.id}
                prior = item.prior_issue_id
                if item.id in history and prior in {None, item.id}:
                    # An unchanged finding inherits its stored ancestry even
                    # when the reviewer names only the latest occurrence.
                    prior = history[item.id].get('prior_issue_id')
                    item.prior_issue_id = prior
                while prior is not None:
                    if prior not in known:
                        raise ValueError('prior_issue_id references unknown issue')
                    if prior in lineage:
                        raise ValueError('prior_issue_id contains a cycle')
                    lineage.add(prior)
                    prior = history[prior].get('prior_issue_id')
                if claimed & lineage:
                    raise ValueError('prior_issue_id must have one-to-one correspondence')
                claimed.update(lineage)
            if not set(self.resolved_issue_ids) <= known:
                raise ValueError('resolved_issue_ids contains unknown issue')
            if set(self.resolved_issue_ids) & claimed:
                raise ValueError('resolved issue is still reported as active')
            blockers = {item['id'] for item in context['issue_history'] if item['severity'] == 'blocker'}
            disposition = set(self.resolved_issue_ids) | claimed
            if not blockers <= disposition:
                missing = ', '.join(sorted(blockers - disposition))
                raise ValueError(
                    'prior blocker missing explicit disposition: ' + missing
                    + '. Recheck each ID in issue_history: explicitly resolve it in '
                    'resolved_issue_ids only if fixed, otherwise report a current issue '
                    'linked by prior_issue_id. Include historical occurrences of the '
                    'same defect; do not invent IDs or silently pass unresolved issues.'
                )
            self.history_issue_ids = sorted(known)
        return self

    @property
    def status(self) -> str:
        return 'needs_revision' if any(i.severity == 'blocker' for i in self.issues) else 'passed'


class CraftStageRecord(StrictModel):
    stage: str
    input_hash: str
    output: dict[str, Any]
    output_hash: str
    elapsed_seconds: float = Field(ge=0)
    attempts: int = Field(default=1, ge=1, le=2)
    author_session_id: str | None = None
    session_mode: str | None = None
    provider_session_id: str | None = None


class CraftResult(StrictModel):
    markdown: str
    status: Literal['passed', 'needs_revision']
    review: CraftReview
    records: list[CraftStageRecord]
