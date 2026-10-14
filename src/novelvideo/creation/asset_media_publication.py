"""Task-owned, recoverable publication of registered media snapshots."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from novelvideo.production_workflow.coordination import asset_project_lock
from .asset_media import compatible_before
from .asset_sync import SourceSync, SourceSyncPending, assert_current_available, fingerprint
from .store import ObjectStore, RevisionConflict

_owner = ContextVar('asset_media_publication_owner', default=None)


def publication_targets(state_dir):
    owner = _owner.get()
    if owner and owner[:2] == (asyncio.current_task(), Path(state_dir).resolve()):
        return owner[2]
    return frozenset()


def _checkpoint(phase):
    """Fault seam after each durable boundary, never an I/O implementation."""


class MediaPublication:
    def __init__(self, sync, actor='media-publication'):
        self.sync, self.objects, self.actor = sync, sync.objects, actor
        with self.objects.transaction() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS asset_media_publications (
                object_id TEXT PRIMARY KEY REFERENCES objects(id), token TEXT NOT NULL,
                request_key TEXT NOT NULL, expected_revision INTEGER NOT NULL,
                mapping TEXT NOT NULL, snapshot_before TEXT NOT NULL,
                source_before TEXT NOT NULL, source_after TEXT, actor_id TEXT NOT NULL)''')

    def entries(self, token=None):
        with self.objects.transaction() as conn:
            return {r['object_id']: dict(r) for r in conn.execute(
                'SELECT * FROM asset_media_publications WHERE (? IS NULL OR token=?)', (token, token))}

    def affected(self, slot_ids):
        mappings = self.sync.mappings()
        by_id = {m['object_id']: m for m in mappings}
        selected = {}
        for slot_id in slot_ids:
            parts = slot_id.split(':')
            if len(parts) < 3:
                continue
            for mapping in mappings:
                if parts[0] == mapping['kind'] == 'character':
                    parent = by_id[mapping['parent_id']] if mapping['parent_id'] else mapping
                    identity = parts[3] if len(parts) >= 4 and parts[2] in ('state', 'identity') else None
                    if parent['source_name'] == parts[1] and (not mapping['parent_id'] or mapping['source_name'] == identity):
                        selected[mapping['object_id']] = mapping
                elif parts[0] == mapping['kind'] == 'scene':
                    name = parts[3] if len(parts) >= 5 and parts[2] == 'state' else parts[1]
                    if mapping['source_name'] == name:
                        selected[mapping['object_id']] = mapping
        return list(selected.values())

    async def begin(self, mappings, request_key):
        ids = {m['object_id'] for m in mappings}
        pending = {oid: row for oid, row in self.entries().items() if oid in ids}
        if pending:
            if any(row['request_key'] != request_key for row in pending.values()):
                raise SourceSyncPending('Media publication requires explicit reconciliation')
            for token in {row['token'] for row in pending.values()}:
                await self.reconcile(token)
        with self.objects.transaction() as conn:
            for object_id in ids:
                assert_current_available(conn, object_id)
        actual = await self.sync.records(mappings)
        token = uuid4().hex
        with self.objects.transaction() as conn:
            for mapping in mappings:
                object_id = mapping['object_id']
                current = ObjectStore._read(conn, object_id, None)
                if actual[object_id] is None or not compatible_before(current['payload'], actual[object_id]):
                    raise SourceSyncPending('Media/source drift before publication; explicit refresh required')
                conn.execute('INSERT INTO asset_media_publications VALUES (?,?,?,?,?,?,?,NULL,?)',
                    (object_id, token, request_key, current['revision'], json.dumps(mapping),
                     json.dumps(current['payload']), json.dumps(actual[object_id]), self.actor))
        _checkpoint('prepared')
        return token

    def apply(self, entries, actual, *, abandon=False):
        with self.objects.transaction() as conn:
            for object_id, entry in entries.items():
                live = conn.execute('SELECT * FROM asset_media_publications WHERE object_id=?', (object_id,)).fetchone()
                mapping = conn.execute('SELECT * FROM asset_sources WHERE object_id=?', (object_id,)).fetchone()
                if live is None or dict(live) != entry or mapping is None or dict(mapping) != json.loads(entry['mapping']):
                    raise SourceSyncPending('Media publication evidence changed')
                revision = conn.execute('SELECT current_revision FROM objects WHERE id=?', (object_id,)).fetchone()[0]
                if revision != entry['expected_revision']:
                    raise RevisionConflict(object_id, entry['expected_revision'], revision)
                current = ObjectStore._read(conn, object_id, revision)
                if current['payload'] != json.loads(entry['snapshot_before']):
                    raise SourceSyncPending('Media snapshot changed')
                proof = entry['source_before'] if abandon else entry['source_after']
                if proof is None or actual[object_id] is None or fingerprint(actual[object_id]) != fingerprint(json.loads(proof)):
                    raise SourceSyncPending('Media source drift or missing after evidence; reconciliation required')
                if not abandon and fingerprint(current['payload']) != fingerprint(actual[object_id]):
                    conn.execute('INSERT INTO object_versions VALUES (?,?,?,?,?)',
                        (object_id, revision + 1, json.dumps(actual[object_id]), entry['actor_id'], datetime.now(timezone.utc).isoformat()))
                    conn.execute('UPDATE objects SET current_revision=? WHERE id=?', (revision + 1, object_id))
                conn.execute('DELETE FROM asset_media_publications WHERE object_id=? AND token=?', (object_id, entry['token']))

    async def finish(self, token):
        entries = self.entries(token)
        actual = await self.sync.records([json.loads(e['mapping']) for e in entries.values()])
        with self.objects.transaction() as conn:
            for object_id, entry in entries.items():
                live = conn.execute('SELECT * FROM asset_media_publications WHERE object_id=?', (object_id,)).fetchone()
                if live is None or dict(live) != entry:
                    raise SourceSyncPending('Media evidence changed before after capture')
                conn.execute('UPDATE asset_media_publications SET source_after=? WHERE object_id=? AND token=?',
                             (json.dumps(actual[object_id]), object_id, token))
        _checkpoint('captured')
        self.apply(self.entries(token), actual)
        _checkpoint('committed')

    async def failed(self, token):
        entries = self.entries(token)
        actual = await self.sync.records([json.loads(e['mapping']) for e in entries.values()], archive=False)
        if all(fingerprint(actual[oid]) == fingerprint(json.loads(e['source_before'])) for oid, e in entries.items()):
            self.apply(entries, actual, abandon=True)

    async def reconcile(self, token=None):
        """Explicit recovery accepts exact before/after evidence, never guesses."""
        entries = self.entries(token)
        for group in {e['token'] for e in entries.values()}:
            batch = {oid: e for oid, e in entries.items() if e['token'] == group}
            no_after = all(e['source_after'] is None for e in batch.values())
            # Before evidence may only abandon an unstarted publication. A
            # captured after must also have valid archived bytes before commit.
            actual = await self.sync.records([json.loads(e['mapping']) for e in batch.values()], archive=not no_after)
            self.apply(batch, actual, abandon=no_after)


@asynccontextmanager
async def media_publication(source, slot_ids, request_key, actor):
    async with asset_project_lock(source.state_dir):
        sync = SourceSync.existing(source)
        if sync is None:
            yield
            return
        publication = MediaPublication(sync, actor)
        mappings = publication.affected(slot_ids)
        if not mappings:
            yield
            return
        token = await publication.begin(mappings, request_key)
        owner = _owner.set((asyncio.current_task(), Path(source.state_dir).resolve(), frozenset(m['object_id'] for m in mappings)))
        try:
            try:
                yield
            except BaseException:
                # Failure in rollback/recovery leaves the proof pending. Never
                # mask the original cancellation or publish work from a child Task.
                try:
                    await publication.failed(token)
                except BaseException:
                    pass
                raise
            else:
                _checkpoint('published')
                await publication.finish(token)
        finally:
            _owner.reset(owner)


async def reconcile_media_publications(source):
    async with asset_project_lock(source.state_dir):
        sync = SourceSync.existing(source)
        if sync:
            await MediaPublication(sync).reconcile()
