"""Durable source-to-history synchronization for explicitly registered assets.

Callers own asset_project_lock. All semantic transactions are synchronous and
short; trusted SQL source reads happen outside them. A missing post-write proof
is deliberately not inferred from whatever happens to be in SQL after restart.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .store import ObjectStore, RevisionConflict


class SourceSyncPending(RuntimeError):
    """Current references are unsafe; source changes may already be committed."""


_publication_owner = ContextVar('asset_publication_owner', default=None)


@contextmanager
def publication_source_write(state_dir, object_id):
    """Only the actual publishing Task may defer its target to its own journal."""
    token = _publication_owner.set((asyncio.current_task(), Path(state_dir).resolve(), object_id))
    try:
        yield
    finally:
        _publication_owner.reset(token)


def publication_target(state_dir):
    owner = _publication_owner.get()
    if owner and owner[:2] == (asyncio.current_task(), Path(state_dir).resolve()):
        return owner[2]
    return None


def fingerprint(payload):
    if payload is None:
        return 'null'
    normalized = {k: v for k, v in payload.items() if k != 'updated_at'}
    for key, value in normalized.items():
        if key == 'voice_facts_json':
            from novelvideo.character_voice_facts import VoiceFacts
            normalized[key] = VoiceFacts.model_validate_json(value or '{}').model_dump(mode='json')
        elif key.endswith('_json') and isinstance(value, str):
            normalized[key] = json.loads(value)
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def assert_current_available(conn, object_id):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('asset_source_sync', 'asset_publication_intents'):
        if table in tables and conn.execute(f'SELECT 1 FROM {table} WHERE object_id=?', (object_id,)).fetchone():
            raise SourceSyncPending(f'{object_id}: pending asset source reconciliation')


class SourceSync:
    def __init__(self, source, objects, actor_id='source-sync'):
        self.source, self.objects, self.actor_id = source, objects, actor_id
        with objects.transaction() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS asset_source_sync (
                object_id TEXT PRIMARY KEY REFERENCES objects(id), token TEXT NOT NULL,
                expected_revision INTEGER NOT NULL, mapping TEXT NOT NULL,
                snapshot_before TEXT NOT NULL, source_before TEXT NOT NULL,
                source_after TEXT, actor_id TEXT NOT NULL)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS asset_source_sync_resolutions (
                id TEXT PRIMARY KEY, object_id TEXT NOT NULL, evidence TEXT NOT NULL,
                confirmed_source TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL)''')

    @classmethod
    def existing(cls, source):
        """Never bootstrap creation for legacy projects or unregistered sources."""
        path = Path(source.state_dir) / 'creation' / 'objects.sqlite3'
        if not path.is_file():
            return None
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='asset_sources'").fetchone():
                return None
            if not conn.execute('SELECT 1 FROM asset_sources LIMIT 1').fetchone():
                return None
        finally:
            conn.close()
        objects = ObjectStore.__new__(ObjectStore)
        objects.db_path = path
        return cls(source, objects)

    def mappings(self, kind=None):
        with self.objects.transaction() as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM asset_sources WHERE (? IS NULL OR kind=?)', (kind, kind))]

    async def records(self, mappings):
        """Model projections loaded from SQL, never SQLiteStore's character cache."""
        characters = {c.name: c for c in await self.source.list_characters()} if any(m['kind'] == 'character' for m in mappings) else {}
        all_mappings = {m['object_id']: m for m in self.mappings()}
        records = {}
        for mapping in mappings:
            if mapping['kind'] == 'scene':
                record = await self.source.get_scene_exact(mapping['source_name'])
                expected_parent = all_mappings[mapping['parent_id']]['source_name'] if mapping['parent_id'] else ''
                if record and (record.base_scene_id != expected_parent or (mapping['parent_id'] and not record.variant_id)):
                    record = None  # Locator/relationship migrations require separate recovery.
            elif not mapping['parent_id']:
                record = characters.get(mapping['source_name'])
            else:
                parent = all_mappings[mapping['parent_id']]
                char = characters.get(parent['source_name'])
                record = next((i for i in char.identities if i.identity_id == mapping['source_name']), None) if char else None
            records[mapping['object_id']] = record.model_dump(mode='json') if record else None
        return records

    async def prepare(self, kind, names=None):
        mappings = self.mappings(kind)
        if names is not None:
            by_id = {m['object_id']: m for m in mappings}
            mappings = [m for m in mappings if
                        (by_id[m['parent_id']]['source_name'] if kind == 'character' and m['parent_id'] else m['source_name']) in names]
        target = publication_target(self.source.state_dir)
        mappings = [m for m in mappings if m['object_id'] != target]
        if not mappings:
            return None
        with self.objects.transaction() as conn:
            for mapping in mappings:
                assert_current_available(conn, mapping['object_id'])
        before = await self.records(mappings)
        token = uuid4().hex
        with self.objects.transaction() as conn:
            for mapping in mappings:
                object_id = mapping['object_id']
                current = ObjectStore._read(conn, object_id, None)
                conn.execute('INSERT INTO asset_source_sync VALUES (?,?,?,?,?,?,NULL,?)',
                             (object_id, token, current['revision'], json.dumps(mapping),
                              json.dumps(current['payload']), json.dumps(before[object_id]), self.actor_id))
        # A pre-existing source drift cannot be quietly folded into a new write.
        for mapping in mappings:
            row = self.entries(token)[mapping['object_id']]
            if before[mapping['object_id']] is None or fingerprint(json.loads(row['snapshot_before'])) != fingerprint(before[mapping['object_id']]):
                raise SourceSyncPending('Source drift before write requires explicit refresh confirmation')
        return token

    def entries(self, token=None):
        with self.objects.transaction() as conn:
            return {r['object_id']: dict(r) for r in conn.execute(
                'SELECT * FROM asset_source_sync WHERE (? IS NULL OR token=?)', (token, token))}

    def capture_after(self, token, actual, entries):
        with self.objects.transaction() as conn:
            for object_id, payload in actual.items():
                live = conn.execute('SELECT * FROM asset_source_sync WHERE object_id=?', (object_id,)).fetchone()
                if live is None or dict(live) != entries[object_id]:
                    raise SourceSyncPending('Source synchronization evidence changed before capture')
                conn.execute('UPDATE asset_source_sync SET source_after=? WHERE object_id=? AND token=?',
                             (json.dumps(payload), object_id, token))

    def apply(self, entries, actual):
        """CAS snapshots and clear their exact proof in one transaction."""
        results = []
        with self.objects.transaction() as conn:
            for object_id, entry in entries.items():
                live = conn.execute('SELECT * FROM asset_source_sync WHERE object_id=?', (object_id,)).fetchone()
                mapping = conn.execute('SELECT * FROM asset_sources WHERE object_id=?', (object_id,)).fetchone()
                if live is None or dict(live) != entry or mapping is None or dict(mapping) != json.loads(entry['mapping']):
                    raise SourceSyncPending('Source synchronization evidence changed')
                if conn.execute('SELECT 1 FROM asset_publication_intents WHERE object_id=?', (object_id,)).fetchone():
                    raise SourceSyncPending('Publication requires reconciliation before source sync')
                payload = actual[object_id]
                if payload is None:
                    raise SourceSyncPending('Source locator or relationship is missing; reconciliation required')
                proof = entry['source_after']
                if proof is None:
                    before = fingerprint(json.loads(entry['source_before']))
                    if fingerprint(payload) != before or fingerprint(json.loads(entry['snapshot_before'])) != before:
                        raise SourceSyncPending('Missing post-write source evidence; explicit confirmation required')
                elif fingerprint(payload) != fingerprint(json.loads(proof)):
                    raise SourceSyncPending('Source drift from recorded evidence; explicit confirmation required')
                current_revision = conn.execute('SELECT current_revision FROM objects WHERE id=?', (object_id,)).fetchone()[0]
                if current_revision != entry['expected_revision']:
                    raise RevisionConflict(object_id, entry['expected_revision'], current_revision)
                current = ObjectStore._read(conn, object_id, current_revision)
                if current['payload'] != json.loads(entry['snapshot_before']):
                    raise SourceSyncPending('Snapshot evidence changed')
                if fingerprint(current['payload']) != fingerprint(payload):
                    current_revision += 1
                    conn.execute('INSERT INTO object_versions VALUES (?,?,?,?,?)',
                                 (object_id, current_revision, json.dumps(payload), entry['actor_id'], datetime.now(timezone.utc).isoformat()))
                    conn.execute('UPDATE objects SET current_revision=? WHERE id=?', (current_revision, object_id))
                conn.execute('DELETE FROM asset_source_sync WHERE object_id=? AND token=?', (object_id, entry['token']))
                results.append((json.loads(entry['mapping']), current_revision))
        return results

    async def finish(self, token):
        if token is None:
            return []
        entries = self.entries(token)
        actual = await self.records([json.loads(e['mapping']) for e in entries.values()])
        self.capture_after(token, actual, entries)
        return self.apply(self.entries(token), actual)

    async def failed_write(self, token):
        """Only discard a failed attempt proven to have left its source unchanged."""
        if token is None:
            return
        entries = self.entries(token)
        actual = await self.records([json.loads(e['mapping']) for e in entries.values()])
        if all(fingerprint(actual[oid]) == fingerprint(json.loads(e['source_before'])) for oid, e in entries.items()):
            self.apply(entries, actual)
            return
        raise SourceSyncPending('Source write may already be committed; pending source reconciliation')

    async def reconcile(self):
        entries = self.entries()
        if not entries:
            return []
        actual = await self.records([json.loads(e['mapping']) for e in entries.values()])
        return self.apply(entries, actual)

    async def refresh(self, object_id, expected_revision, acknowledge_pending=False):
        mapping = next((m for m in self.mappings() if m['object_id'] == object_id), None)
        if mapping is None:
            raise KeyError(object_id)
        actual = (await self.records([mapping]))[object_id]
        if actual is None:
            raise SourceSyncPending('Source locator or relationship is missing; cannot confirm refresh')
        with self.objects.transaction() as conn:
            revision = conn.execute('SELECT current_revision FROM objects WHERE id=?', (object_id,)).fetchone()[0]
            if revision != expected_revision:
                raise RevisionConflict(object_id, expected_revision, revision)
            if conn.execute('SELECT 1 FROM asset_publication_intents WHERE object_id=?', (object_id,)).fetchone():
                raise SourceSyncPending('Publication requires its own reconciliation')
            pending = conn.execute('SELECT * FROM asset_source_sync WHERE object_id=?', (object_id,)).fetchone()
            if pending and not acknowledge_pending:
                raise SourceSyncPending('Pending source evidence requires explicit confirmation')
            current = ObjectStore._read(conn, object_id, revision)
            if fingerprint(current['payload']) != fingerprint(actual):
                revision += 1
                conn.execute('INSERT INTO object_versions VALUES (?,?,?,?,?)',
                             (object_id, revision, json.dumps(actual), self.actor_id, datetime.now(timezone.utc).isoformat()))
                conn.execute('UPDATE objects SET current_revision=? WHERE id=?', (revision, object_id))
            if pending:
                conn.execute('INSERT INTO asset_source_sync_resolutions VALUES (?,?,?,?,?,?)',
                             (uuid4().hex, object_id, json.dumps(dict(pending)), json.dumps(actual), self.actor_id, datetime.now(timezone.utc).isoformat()))
                conn.execute('DELETE FROM asset_source_sync WHERE object_id=?', (object_id,))
        return revision
