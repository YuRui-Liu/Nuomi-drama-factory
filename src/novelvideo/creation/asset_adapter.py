"""References and immutable snapshots over the existing project asset library.

Source names are explicit import locators, never semantic IDs. Media adoption is
deliberately excluded: use the existing candidate/publication domain service.
"""

from contextlib import asynccontextmanager, closing
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
from functools import partial
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal
from uuid import uuid4

from novelvideo.models import CharacterIdentity, NovelScene
from novelvideo.production_workflow.coordination import asset_project_lock
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.utils.derived_scenes import compose_derived_scene_name

from .models import AssetRef
from .store import ObjectStore, RevisionConflict
from .asset_sync import SourceSync, SourceSyncPending, fingerprint, publication_source_write, assert_current_available
from .asset_media import MEDIA_KEY, snapshot, compatible_before


_CHARACTER_FIELDS = {'age_group', 'appearance_details', 'face_prompt', 'body_type'}
_IDENTITY_FIELDS = {'age_group', 'appearance_details', 'face_prompt'}
_SCENE_FIELDS = {'time_of_day', 'variant_prompt', 'description', 'notes'}


async def registration_io(function, *args, **kwargs):
    """Drain synchronous I/O before the owning Task may release its lock.

    Shield only an executor Future, never a coroutine that acquires the
    Task-owned project lock. Repeated cancellation cannot abandon archive writes.
    """
    future = asyncio.get_running_loop().run_in_executor(None, partial(function, *args, **kwargs))
    cancellation = None
    while True:
        try:
            result = await asyncio.shield(future)
        except asyncio.CancelledError as exc:
            cancellation = exc
            if not future.done():
                continue
            if not future.cancelled():
                future.exception()  # consume a worker failure after cancellation
            raise cancellation
        except BaseException:
            if cancellation is not None:
                raise cancellation
            raise
        else:
            if cancellation is not None:
                raise cancellation
            return result


class AssetAdapter:
    """An adapter, not an independent writable asset library.

    Adapter, source metadata writes and workflow publications share a project
    process lock. Source and snapshot writes use separate databases, with
    durable publication and source synchronization evidence for recovery.
    """

    def __init__(self, source: SQLiteStore, objects: ObjectStore, actor_id: str = 'asset-adapter'):
        if objects.db_path.parent.parent.resolve() != Path(source.state_dir).resolve():
            raise ValueError('Source and snapshots must belong to the same project')
        self.source, self.objects, self.actor_id = source, objects, actor_id
        with objects.transaction() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS asset_sources (
                object_id TEXT PRIMARY KEY REFERENCES objects(id), kind TEXT NOT NULL,
                source_name TEXT NOT NULL, parent_id TEXT NOT NULL,
                UNIQUE(kind, source_name, parent_id))''')
            conn.execute('''CREATE TABLE IF NOT EXISTS asset_variant_requests (
                request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                source_name TEXT NOT NULL, result TEXT)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS asset_publication_intents (
                object_id TEXT PRIMARY KEY, expected_revision INTEGER NOT NULL,
                previous TEXT NOT NULL, requested TEXT NOT NULL)''')
            # Keep the four-column legacy intent contract. Old intents without
            # complete evidence fail closed rather than guessing a baseline.
            conn.execute('''CREATE TABLE IF NOT EXISTS asset_publication_evidence (
                object_id TEXT PRIMARY KEY, source_before TEXT NOT NULL,
                source_requested TEXT NOT NULL)''')

    async def _assert_fresh(self, base: dict[str, Any]) -> None:
        if base['kind'] != 'character':
            return
        cached = self.source.get_character(base['source_name'])
        # update_character persists the whole cached model. Check every stored
        # column, including media metadata outside this adapter's edit whitelist.
        # The domain list_characters projection currently omits is_main, so it
        # cannot establish full cache freshness. This connection is read-only.
        with closing(sqlite3.connect(Path(self.source.db_path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            fresh = conn.execute('SELECT * FROM characters WHERE name=?', (base['source_name'],)).fetchone()
        stale = cached is None or fresh is None
        if not stale:
            for column in fresh.keys():
                if column in {'created_at', 'updated_at'}:
                    continue  # Maintained by SQL, not written from the cache.
                field = 'aliases' if column == 'aliases_json' else column
                if not hasattr(cached, field):
                    stale = True  # Unknown future columns fail closed.
                    break
                stored = json.loads(fresh[column]) if column == 'aliases_json' else fresh[column]
                if getattr(cached, field) != stored:
                    stale = True
                    break
        if stale:
            raise RuntimeError('Character source cache is stale; reload source before editing')

    @asynccontextmanager
    async def _write_lock(self):
        async with asset_project_lock(self.source.state_dir):
            yield

    @staticmethod
    def _source_fingerprint(payload: dict[str, Any]) -> str:
        # SQL owns this timestamp; metadata writes can update it independently
        # of the semantic record. Every other source field is compared.
        return fingerprint(payload)

    async def reconcile_source(self) -> list[AssetRef]:
        """Replay complete source evidence; unknown or conflicting proof stays pending."""
        async with self._write_lock():
            from .asset_rename import reconcile_renames
            await reconcile_renames(self.source)
            results = await SourceSync(self.source, self.objects, self.actor_id).reconcile()
            return [AssetRef(base_asset_id=m['parent_id'] or m['object_id'],
                             variant_id=m['object_id'] if m['parent_id'] else None,
                             revision=revision) for m, revision in results]

    async def refresh_source(self, base_id: str, variant_id: str | None = None, *,
                             expected_revision: int, acknowledge_pending: bool = False) -> AssetRef:
        """Confirm trusted SQL at a CAS revision, retaining superseded pending proof.

        acknowledge_pending explicitly accepts the current SQL state after an
        ambiguous interruption. It cannot repair missing/renamed locators or
        consume publication intents; those require their own reconciliation.
        """
        async with self._write_lock():
            self._base(base_id)
            mapping = self._mapping(variant_id or base_id)
            if variant_id and mapping['parent_id'] != base_id:
                raise ValueError('Variant belongs to another base asset')
            revision = await SourceSync(self.source, self.objects, self.actor_id).refresh(
                variant_id or base_id, expected_revision, acknowledge_pending)
            return AssetRef(base_asset_id=base_id, variant_id=variant_id, revision=revision)

    async def reconcile_publication(self, base_id: str, variant_id: str | None = None) -> AssetRef:
        """Explicitly finish a proven write or abandon a proven unstarted write.

        Never rewrite source data. Unknown/legacy evidence, unrelated source
        changes and competing semantic revisions retain the intent and fail.
        A single synchronous CAS transaction advances history and clears the
        journal; source reads happen before entering that transaction.
        """
        async with self._write_lock():
            base = self._base(base_id)
            object_id = variant_id or base_id
            mapping = self._mapping(object_id)
            if variant_id and mapping['parent_id'] != base_id:
                raise ValueError('Variant belongs to another base asset')
            with self.objects.transaction() as conn:
                row = conn.execute('SELECT * FROM asset_publication_intents WHERE object_id=?', (object_id,)).fetchone()
                evidence = conn.execute('SELECT * FROM asset_publication_evidence WHERE object_id=?', (object_id,)).fetchone()
            if row is None:
                current = self.objects.read(object_id)
                return AssetRef(base_asset_id=base_id, variant_id=variant_id, revision=current['revision'])
            if evidence is None:
                raise RuntimeError('Legacy publication lacks complete source evidence; manual reconciliation required')
            await self._assert_fresh(base)
            actual = snapshot(self.source, mapping, await self._source_record(mapping))
            before, requested = json.loads(evidence['source_before']), json.loads(evidence['source_requested'])
            requested_matches = compatible_before(requested, actual)
            if compatible_before(before, actual):
                changed = False
            elif requested_matches:
                changed = True
            else:
                raise RuntimeError('Publication source drift requires manual reconciliation')
            expected = row['expected_revision']
            with self.objects.transaction() as conn:
                live = conn.execute('SELECT * FROM asset_publication_intents WHERE object_id=?', (object_id,)).fetchone()
                proof = conn.execute('SELECT * FROM asset_publication_evidence WHERE object_id=?', (object_id,)).fetchone()
                if live is None or dict(live) != dict(row) or proof is None or dict(proof) != dict(evidence):
                    raise RuntimeError('Publication intent changed during reconciliation')
                current = conn.execute('''SELECT o.current_revision, v.payload FROM objects o
                    JOIN object_versions v ON v.object_id=o.id AND v.revision=o.current_revision
                    WHERE o.id=?''', (object_id,)).fetchone()
                revision = current['current_revision']
                if revision == expected:
                    if changed:
                        revision += 1
                        conn.execute('INSERT INTO object_versions VALUES (?,?,?,?,?)',
                                     (object_id, revision, json.dumps(actual), self.actor_id, datetime.now(timezone.utc).isoformat()))
                        conn.execute('UPDATE objects SET current_revision=? WHERE id=? AND current_revision=?',
                                     (revision, object_id, expected))
                elif not (requested_matches and revision == expected + 1 and compatible_before(json.loads(current['payload']), actual)):
                    raise RevisionConflict(object_id, expected, revision)
                conn.execute('DELETE FROM asset_publication_intents WHERE object_id=?', (object_id,))
                conn.execute('DELETE FROM asset_publication_evidence WHERE object_id=?', (object_id,))
            return AssetRef(base_asset_id=base_id, variant_id=variant_id, revision=revision)

    def _mapping(self, object_id: str) -> dict[str, Any]:
        with self.objects.transaction() as conn:
            row = conn.execute('SELECT * FROM asset_sources WHERE object_id=?', (object_id,)).fetchone()
        if row is None:
            raise KeyError(object_id)
        return dict(row)

    async def _source_record(self, mapping: dict[str, Any]):
        if mapping['kind'] == 'scene':
            record = await self.source.get_scene_exact(mapping['source_name'])
        elif not mapping['parent_id']:
            record = await self.source.get_character_from_graph(mapping['source_name'])
        else:
            parent = self._mapping(mapping['parent_id'])
            char = await self.source.get_character_from_graph(parent['source_name'])
            record = next((item for item in char.identities if item.identity_id == mapping['source_name']), None) if char else None
        if record is None:
            raise KeyError(mapping['source_name'])
        return record

    async def _register(self, kind: str, source_name: str, parent_id: str = '') -> AssetRef:
        from .asset_rename import assert_asset_locators_writable
        # Character children use stable identity UUIDs, not character names.
        if kind == 'scene' or not parent_id:
            assert_asset_locators_writable(self.source, kind, [source_name])
        with self.objects.transaction() as conn:
            if parent_id:
                assert_current_available(conn, parent_id)
            existing = conn.execute('SELECT object_id FROM asset_sources WHERE kind=? AND source_name=? AND parent_id=?',
                                    (kind, source_name, parent_id)).fetchone()
            if existing:
                assert_current_available(conn, existing['object_id'])
        mapping = dict(kind=kind, source_name=source_name, parent_id=parent_id)
        record = await self._source_record(mapping)
        if kind == 'character' and not parent_id:
            source_name = record.name
            assert_asset_locators_writable(self.source, kind, [source_name])
        if kind == 'scene':
            expected = self._mapping(parent_id)['source_name'] if parent_id else ''
            if record.base_scene_id != expected or (parent_id and not record.variant_id):
                raise ValueError('Scene must be a state of this same base location')
        trusted = await registration_io(snapshot, self.source,
            dict(kind=kind, source_name=source_name, parent_id=parent_id), deepcopy(record))
        with self.objects.transaction() as conn:
            found = conn.execute('SELECT object_id FROM asset_sources WHERE kind=? AND source_name=? AND parent_id=?',
                                 (kind, source_name, parent_id)).fetchone()
        if found:
            obj = self.objects.read(found['object_id'])
            if fingerprint(obj['payload']) != fingerprint(trusted):
                if MEDIA_KEY in obj['payload'] or not compatible_before(obj['payload'], trusted):
                    raise SourceSyncPending('Registered asset drift requires explicit refresh with expected_revision')
                # Explicit registration may enrich an unchanged legacy flat
                # snapshot, but cannot acknowledge source or known-media drift.
                obj = self.objects.update(obj['id'], obj['revision'], trusted, self.actor_id)
        else:
            with self.objects.transaction() as conn:
                object_id = uuid4().hex
                conn.execute('INSERT INTO objects (id,kind,current_revision) VALUES (?,?,1)', (object_id, kind))
                conn.execute('INSERT INTO object_versions VALUES (?,1,?,?,?)',
                             (object_id, json.dumps(trusted), self.actor_id, datetime.now(timezone.utc).isoformat()))
                obj = dict(id=object_id, revision=1)
                conn.execute('INSERT INTO asset_sources VALUES (?, ?, ?, ?)', (obj['id'], kind, source_name, parent_id))
                conn.execute('UPDATE objects SET base_asset_id=?, variant_id=? WHERE id=?',
                             (parent_id or obj['id'], obj['id'] if parent_id else None, obj['id']))
        return AssetRef(base_asset_id=parent_id or obj['id'], variant_id=obj['id'] if parent_id else None, revision=obj['revision'])

    async def register_base(self, kind: Literal['character', 'scene'], source_name: str) -> AssetRef:
        if kind not in {'character', 'scene'}:
            raise ValueError('Unsupported asset kind')
        async with self._write_lock():
            return await self._register(kind, source_name)

    async def register_variant(self, base_id: str, source_name: str) -> AssetRef:
        async with self._write_lock():
            base = self._base(base_id)
            return await self._register(base['kind'], source_name, base_id)

    def _base(self, base_id: str) -> dict[str, Any]:
        base = self._mapping(base_id)
        if base['parent_id']:
            raise ValueError('Expected base asset')
        return base

    async def resolve(self, ref: AssetRef) -> dict[str, Any]:
        self._base(ref.base_asset_id)
        mapping = self._mapping(ref.variant_id or ref.base_asset_id)
        if ref.variant_id and mapping['parent_id'] != ref.base_asset_id:
            raise ValueError('Variant belongs to another base asset')
        obj = self.objects.read(ref.variant_id or ref.base_asset_id, ref.revision)
        if obj is None:
            raise KeyError(ref)
        return obj

    async def list_variants(self, base_id: str) -> list[AssetRef]:
        self._base(base_id)
        with self.objects.transaction() as conn:
            assert_current_available(conn, base_id)
            rows = conn.execute('''SELECT s.object_id, o.current_revision FROM asset_sources s
                JOIN objects o ON o.id=s.object_id WHERE parent_id=? ORDER BY s.object_id''', (base_id,)).fetchall()
            for row in rows:
                assert_current_available(conn, row['object_id'])
        return [AssetRef(base_asset_id=base_id, variant_id=r['object_id'], revision=r['current_revision']) for r in rows]

    @staticmethod
    def _validate_fields(kind: str, variant: bool, payload: dict[str, Any]) -> None:
        fields = _SCENE_FIELDS if kind == 'scene' else (_IDENTITY_FIELDS if variant else _CHARACTER_FIELDS)
        if kind == 'scene' and not variant:
            fields = fields | {'environment_prompt'}
        if payload.keys() - fields:
            raise ValueError('Only editable metadata is accepted; relationships and media require domain operations')
        if any(not isinstance(value, str) for value in payload.values()):
            raise ValueError('Asset metadata values must be strings')

    async def create_variant(self, base_id: str, name: str, delta: dict[str, Any], request_id: str) -> AssetRef:
        if not name.strip() or not request_id.strip():
            raise ValueError('name and request_id are required')
        async with self._write_lock():
            base = self._base(base_id)
            self.objects.read(base_id)
            await self._assert_fresh(base)
            source = await self._source_record(base)
            self._validate_fields(base['kind'], True, delta)
            fingerprint = json.dumps([base_id, name, delta], sort_keys=True, ensure_ascii=False)
            proposed_locator = uuid4().hex if base['kind'] == 'character' else compose_derived_scene_name(source.name, name)
            existing_scene = await self.source.get_scene_exact(proposed_locator) if base['kind'] == 'scene' else None
            with self.objects.transaction() as conn:
                row = conn.execute('SELECT * FROM asset_variant_requests WHERE request_id=?', (request_id,)).fetchone()
                if row and row['fingerprint'] != fingerprint:
                    raise ValueError('request_id already used with different input')
                if row and row['result']:
                    saved = AssetRef.model_validate_json(row['result'])
                    assert_current_available(conn, saved.variant_id or saved.base_asset_id)
                    return saved
                locator = row['source_name'] if row else proposed_locator
                if base['kind'] == 'scene':
                    from .asset_rename import assert_asset_locators_writable
                    assert_asset_locators_writable(self.source, 'scene', [locator])
                if not row:
                    if existing_scene is not None:
                        raise ValueError('Scene name already exists; explicitly register it instead')
                    conn.execute('INSERT INTO asset_variant_requests VALUES (?, ?, ?, NULL)', (request_id, fingerprint, locator))
            if base['kind'] == 'character':
                if not any(i.identity_id == locator for i in source.identities):
                    data = {'age_group': source.age_group, **delta}
                    await self.source.add_character_identity(source.name, CharacterIdentity(
                        identity_id=locator, character_name=source.name, identity_name=name, **data))
                    await self._assert_fresh(base)
            elif await self.source.get_scene_exact(locator) is None:
                await self.source.add_scene_if_absent(NovelScene(
                    name=locator, base_scene_id=source.name, variant_id=name, scene_type=source.scene_type, **delta))
            ref = await self._register(base['kind'], locator, base_id)
            with self.objects.transaction() as conn:
                conn.execute('UPDATE asset_variant_requests SET result=? WHERE request_id=?', (ref.model_dump_json(), request_id))
            return ref

    async def publish_revision(self, base_id: str, variant_id: str | None, expected_revision: int, payload: dict[str, Any]) -> AssetRef:
        async with self._write_lock():
            base = self._base(base_id)
            self.objects.read(base_id)
            await self._assert_fresh(base)
            ref = AssetRef(base_asset_id=base_id, variant_id=variant_id, revision=expected_revision)
            await self.resolve(ref)
            object_id = variant_id or base_id
            with self.objects.transaction() as conn:
                if conn.execute('SELECT 1 FROM asset_publication_intents WHERE object_id=?', (object_id,)).fetchone():
                    raise RuntimeError('Interrupted publication requires source/snapshot reconciliation')
            current = self.objects.read(object_id)
            if current['revision'] != expected_revision:
                raise RevisionConflict(object_id, expected_revision, current['revision'])
            self._validate_fields(base['kind'], variant_id is not None, payload)
            mapping = self._mapping(object_id)
            record = await self._source_record(mapping)
            if base['kind'] == 'scene' and record.base_scene_id != (base['source_name'] if variant_id else ''):
                raise ValueError('Scene source relationship changed')
            requested_record = record.__class__.model_validate({**record.model_dump(), **payload})
            previous = {key: getattr(record, key) for key in payload}
            trusted_before = snapshot(self.source, mapping, record)
            trusted_requested = snapshot(self.source, mapping, requested_record)
            with self.objects.transaction() as conn:
                conn.execute('INSERT INTO asset_publication_intents VALUES (?,?,?,?)',
                             (object_id, expected_revision, json.dumps(previous), json.dumps(payload)))
                conn.execute('INSERT INTO asset_publication_evidence VALUES (?,?,?)',
                             (object_id, json.dumps(trusted_before), json.dumps(trusted_requested)))
            async def write(values):
                with publication_source_write(self.source.state_dir, object_id):
                    if base['kind'] == 'scene':
                        await self.source.update_scene(mapping['source_name'], **values)
                    elif variant_id:
                        await self.source.update_character_identity(base['source_name'], mapping['source_name'], **values)
                    else:
                        await self.source.update_character(base['source_name'], **values)
                # Identity methods currently swallow a failed SQL update after
                # mutating their cache. Verify persisted state for both forward
                # writes and compensation before snapshots/intents can advance.
                await self._assert_fresh(base)
            try:
                await write(payload)
                record = await self._source_record(mapping)
                obj = self.objects.update(object_id, expected_revision, snapshot(self.source, mapping, record), self.actor_id)
            except BaseException:
                # Keep compensation in the owning Task. shield(coroutine)
                # creates a child Task which cannot reenter the project lock.
                # A second cancellation leaves durable evidence for explicit
                # recovery, rather than spawning an unowned background write.
                await write(previous)
                with self.objects.transaction() as conn:
                    conn.execute('DELETE FROM asset_publication_intents WHERE object_id=?', (object_id,))
                    conn.execute('DELETE FROM asset_publication_evidence WHERE object_id=?', (object_id,))
                raise
            with self.objects.transaction() as conn:
                conn.execute('DELETE FROM asset_publication_intents WHERE object_id=?', (object_id,))
                conn.execute('DELETE FROM asset_publication_evidence WHERE object_id=?', (object_id,))
            return ref.model_copy(update={'revision': obj['revision']})
