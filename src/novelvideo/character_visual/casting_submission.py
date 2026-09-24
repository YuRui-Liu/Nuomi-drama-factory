"""Persistent request deduplication before an asynchronous task can start."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from novelvideo.production_workflow import production_workflow_project_lock
from .casting_compiler import snapshot_digest


class SubmissionJournal:
    def __init__(self, ctx):
        self.state_dir = Path(ctx.state_dir)
        self.project_id = ctx.project_id
        self.path = self.state_dir / 'casting_submissions.json'

    def key(self, operation, character_id, identity_id, idempotency_key):
        return snapshot_digest([self.project_id, operation, character_id, identity_id, idempotency_key])

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name('.casting-submissions-' + uuid4().hex)
        try:
            with temporary.open('w') as handle:
                json.dump(rows, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            descriptor = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def existing(self, operation, character_id, identity_id, idempotency_key, fingerprint):
        row = self.read().get(self.key(operation, character_id, identity_id, idempotency_key))
        if row and row['fingerprint'] != fingerprint:
            raise ValueError('idempotency key already bound to different input')
        return row

    def update(self, key, **fields):
        with production_workflow_project_lock(self.state_dir):
            rows = self.read()
            rows[key].update(fields)
            self.write(rows)
            return rows[key]

    def list(self, character_id, identity_id):
        return [row for row in self.read().values()
                if (row['character_id'], row['identity_id']) == (character_id, identity_id)]

    def claim_execution(self, *, request_id, submission_token, task_id, task_type, scope, payload):
        with production_workflow_project_lock(self.state_dir):
            rows = self.read()
            row = rows.get(request_id)
            if row is None or row['submission_token'] != submission_token:
                raise ValueError('casting submission not found or token mismatch')
            if row['operation'] != 'recast' or row['task_type'] != task_type or row['scope'] != scope:
                raise ValueError('casting task scope mismatch')
            if any(payload.get(key) != value for key, value in row['payload'].items()):
                raise ValueError('casting persisted payload mismatch')
            if row.get('task_id') not in (None, task_id) or row.get('execution_task_id') not in (None, task_id):
                raise ValueError('casting task already bound')
            if row.get('execution_status') == 'completed':
                return row
            if row.get('execution_status') in ('running', 'failed'):
                raise ValueError('casting execution uncertain or failed; explicit new recast required')
            row.update(execution_task_id=task_id, execution_status='running', task_id=task_id)
            self.write(rows)
            return row

    def finish_execution(self, request_id, *, task_id, result=None, error=None):
        with production_workflow_project_lock(self.state_dir):
            rows = self.read()
            row = rows[request_id]
            if row.get('execution_task_id') != task_id or row.get('execution_status') != 'running':
                raise ValueError('casting execution task mismatch')
            row.update(execution_status='failed' if error else 'completed', result=result, error=error)
            self.write(rows)


def submission_view(row, ctx, manager):
    state = manager.get_task_for_project(ctx, row['task_type'], 0, scope=row['scope'])
    result = {k: row.get(k) for k in ('request_id', 'operation', 'character_id', 'identity_id', 'task_id', 'task_type',
        'scope', 'status', 'candidate_id', 'attempt_id', 'reference_coverage', 'result', 'error', 'execution_status')}
    if state is not None:
        result.update(task_id=state.task_id, status=state.status)
    return result


async def submit_once(*, ctx, journal, operation, character_id, identity_id, idempotency_key,
                      fingerprint, task_type, payload, backend, manager, prepare=None):
    key = journal.key(operation, character_id, identity_id, idempotency_key)
    with production_workflow_project_lock(journal.state_dir):
        rows = journal.read()
        row = rows.get(key)
        if row:
            if row['fingerprint'] != fingerprint:
                raise ValueError('idempotency key already bound to different input')
            return submission_view(row, ctx, manager)
        token = uuid4().hex
        row = dict(request_id=key, operation=operation, character_id=character_id, identity_id=identity_id,
            fingerprint=fingerprint, submission_token=token, scope='casting_' + key,
            task_type=task_type, task_id=None, status='submitting', payload=dict(payload))
        if prepare:
            prepare(row)
        row['payload'].update(submission_token=token, submission_request_id=key)
        rows[key] = row
        journal.write(rows)
    # No project lock across await. The durable record makes a lost response safe.
    try:
        queued = await backend.enqueue_project_task(ctx, task_type=task_type, episode=0,
            scope=row['scope'], payload=row['payload'])
        row = journal.update(key, task_id=queued.task_state.task_id, status=queued.task_state.status)
    except Exception:
        # A backend exception can follow a successful paid dispatch. Never infer
        # that absence from a momentary task lookup means the task did not start.
        row = journal.update(key, status='submission_unknown')
    return submission_view(row, ctx, manager)
