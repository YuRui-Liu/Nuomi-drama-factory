"""Immutable, append-only feedback for a director revision chain.

Persistence, optimistic locking and launching the independent QC context belong to
the caller. Persist the returned history atomically with the candidate revision;
never mutate an issue from author text. Evidence paths are references to artifacts
that the caller supplies to QC, not assertions that those artifacts were verified.
Restore whole histories only from trusted service storage: schema validation does
not replay transitions or authenticate stored conclusions. External author/QC
payloads must enter through the corresponding round transition methods.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Paths = Annotated[tuple[Text, ...], Field(min_length=1)]
Disposition = Literal["resolved", "persists", "regressed", "needs_human"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FeedbackIssue(_Frozen):
    issue_id: Text
    discovered_round_id: Text
    discovered_revision_id: Text
    description: Text
    evidence_paths: Paths
    impact: Text
    acceptance_criteria: Text


class AuthorIssueResponse(_Frozen):
    issue_id: Text
    status: Literal["proposed_change", "disputed", "blocked"]
    explanation: Text
    affected_paths: Paths
    evidence_paths: Paths

    @field_validator("explanation")
    @classmethod
    def reject_empty_claim(cls, value: str) -> str:
        if value.casefold().strip(".!。！ ") in {
            "已优化", "已修复", "已修改", "优化了", "fixed", "done", "optimized", "resolved",
        }:
            raise ValueError("A concrete change or reason is required")
        return value


class AuthorRevisionRound(_Frozen):
    round_id: Text
    base_revision_id: Text
    revision_id: Text
    rules_version: Text
    author_session_id: Text
    responses: tuple[AuthorIssueResponse, ...]


class IssueDisposition(_Frozen):
    issue_id: Text
    status: Disposition
    explanation: Text
    evidence_paths: Paths


class QCFeedbackRound(_Frozen):
    round_id: Text
    revision_id: Text
    rules_version: Text
    qc_context_id: Text
    dispositions: tuple[IssueDisposition, ...] = ()
    new_issues: tuple[FeedbackIssue, ...] = ()


class FeedbackIssueState(_Frozen):
    issue: FeedbackIssue
    status: Disposition = "persists"
    last_reviewed_revision_id: Text
    last_reviewed_round_id: Text


class FeedbackHistory(_Frozen):
    """One chain with fixed rules; retries are compared before stale checks.

    ``author_rounds`` retains every proposal and ``qc_rounds`` every independent
    assessment, including omitted issues. Pass the full history to the next
    author/QC call. ``is_clear`` only describes issue feedback, not adoption or
    other production gates. A changed rules version requires a new evaluation
    history; a caller must not silently reuse clearance under different rules.
    """

    initial_revision_id: Text
    current_revision_id: Text
    rules_version: Text
    author_session_id: Text
    issues: tuple[FeedbackIssueState, ...] = ()
    author_rounds: tuple[AuthorRevisionRound, ...] = ()
    qc_rounds: tuple[QCFeedbackRound, ...] = ()

    @classmethod
    def start(cls, revision_id: str, rules_version: str, author_session_id: str) -> Self:
        return cls(initial_revision_id=revision_id, current_revision_id=revision_id,
                   rules_version=rules_version, author_session_id=author_session_id)

    @property
    def open_issues(self) -> tuple[FeedbackIssueState, ...]:
        return tuple(item for item in self.issues if item.status != "resolved")

    @property
    def is_clear(self) -> bool:
        return (
            self._current_is_reviewed()
            and not self.open_issues
            and all(item.last_reviewed_revision_id == self.current_revision_id for item in self.issues)
        )

    def _current_is_reviewed(self) -> bool:
        return bool(self.qc_rounds and self.qc_rounds[-1].revision_id == self.current_revision_id)

    def _already_applied(self, incoming: AuthorRevisionRound | QCFeedbackRound) -> bool:
        for previous in (*self.author_rounds, *self.qc_rounds):
            if previous.round_id == incoming.round_id:
                if type(previous) is type(incoming) and previous == incoming:
                    return True
                raise ValueError(f"round_id conflict: {incoming.round_id}")
        return False

    def _check_binding(self, revision_id: str, rules_version: str) -> None:
        if revision_id != self.current_revision_id:
            raise ValueError(f"stale revision: expected {self.current_revision_id}, got {revision_id}")
        if rules_version != self.rules_version:
            raise ValueError("rules_version does not match this feedback history")

    def submit_revision(self, author_round: AuthorRevisionRound) -> Self:
        """Append complete author responses without changing any issue conclusion."""
        if self._already_applied(author_round):
            return self
        self._check_binding(author_round.base_revision_id, author_round.rules_version)
        if author_round.author_session_id != self.author_session_id:
            raise ValueError("author_session_id does not match the revision chain")
        if not self._current_is_reviewed():
            raise ValueError("Current revision requires independent QC before another author round")
        used_revisions = {self.initial_revision_id, *(item.revision_id for item in self.author_rounds)}
        if author_round.revision_id in used_revisions:
            raise ValueError("A revision_id must be new within the chain")
        response_ids = [item.issue_id for item in author_round.responses]
        if len(response_ids) != len(set(response_ids)):
            raise ValueError("Duplicate author response issue_id")
        required_ids = {item.issue.issue_id for item in self.open_issues}
        if set(response_ids) != required_ids:
            raise ValueError("Author responses must exactly cover all open issue IDs")
        return self.model_copy(update={
            "current_revision_id": author_round.revision_id,
            "author_rounds": (*self.author_rounds, author_round),
        })

    def apply_qc(self, qc_round: QCFeedbackRound) -> Self:
        """Apply only explicit conclusions of an independent current-revision QC."""
        if self._already_applied(qc_round):
            return self
        self._check_binding(qc_round.revision_id, qc_round.rules_version)
        if qc_round.qc_context_id == self.author_session_id:
            raise ValueError("QC must use an independent context from the author")
        known_ids = {item.issue.issue_id for item in self.issues}
        disposition_ids = [item.issue_id for item in qc_round.dispositions]
        new_ids = [item.issue_id for item in qc_round.new_issues]
        if len(disposition_ids) != len(set(disposition_ids)):
            raise ValueError("Duplicate QC disposition issue_id")
        if set(disposition_ids) - known_ids:
            raise ValueError("QC disposition refers to an unknown issue_id")
        if len(new_ids) != len(set(new_ids)) or set(new_ids) & known_ids:
            raise ValueError("New issue IDs must be unique and never reused")
        for issue in qc_round.new_issues:
            if (issue.discovered_round_id != qc_round.round_id
                    or issue.discovered_revision_id != qc_round.revision_id):
                raise ValueError("New issue discovery must bind to this QC round and revision")
        conclusions = {item.issue_id: item for item in qc_round.dispositions}
        states = []
        for item in self.issues:
            verdict = conclusions.get(item.issue.issue_id)
            if verdict is None:
                states.append(item)
                continue
            if verdict.status == "regressed" and item.status != "resolved":
                raise ValueError("Only a previously resolved issue can regress")
            if verdict.status == "persists" and item.status == "resolved":
                raise ValueError("Use regressed when a resolved issue returns")
            states.append(FeedbackIssueState(issue=item.issue, status=verdict.status,
                last_reviewed_revision_id=qc_round.revision_id,
                last_reviewed_round_id=qc_round.round_id))
        states.extend(FeedbackIssueState(issue=issue,
            last_reviewed_revision_id=qc_round.revision_id,
            last_reviewed_round_id=qc_round.round_id) for issue in qc_round.new_issues)
        return self.model_copy(update={"issues": tuple(states), "qc_rounds": (*self.qc_rounds, qc_round)})
