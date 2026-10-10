"""Immutable per-call craft checkpoints, scoped to a project document database."""
from __future__ import annotations

import hashlib
import json
import uuid

from .store import DocumentConflict, DocumentNotFound, DocumentStore, _now


class CraftCheckpointConflict(DocumentConflict):
    """Another worker owns an already recorded result; never invalidate its run."""


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class CraftJournal:
    def __init__(self, store: DocumentStore, owner_type: str, owner_id: str,
                 step_key: str, task_id: str | None = None):
        if owner_type not in {'generation', 'rewrite'}:
            raise ValueError('unknown craft owner')
        self.store, self.owner_type, self.owner_id = store, owner_type, owner_id
        self.step_key, self.task_id = step_key, task_id

    async def context(self, kind):
        from .craft_memory import CraftMemory
        snapshot = await CraftMemory(self.store).snapshot(kind)
        key = (self.owner_type,self.owner_id,self.step_key)
        async with self.store._db() as db:
            await db.execute('BEGIN IMMEDIATE')
            row = await (await db.execute('SELECT data FROM script_craft_contexts WHERE owner_type=? AND owner_id=? AND step_key=?',key)).fetchone()
            if row:
                value = json.loads(row['data'])
            else:
                value = {'kind':kind,'memory':snapshot,'author_session':{'id':str(uuid.uuid4())}}
                await db.execute('INSERT INTO script_craft_contexts VALUES (?,?,?,?)',(*key,json.dumps(value,ensure_ascii=False)))
            await db.commit()
        return value

    async def save_session(self, session):
        table = 'script_generation_runs' if self.owner_type == 'generation' else 'script_rewrite_jobs'
        key = (self.owner_type,self.owner_id,self.step_key)
        async with self.store._db() as db:
            await db.execute('BEGIN IMMEDIATE')
            owner_row = await (await db.execute(f'SELECT data FROM {table} WHERE id=?',(self.owner_id,))).fetchone()
            owner = json.loads(owner_row['data']) if owner_row else {}
            if owner.get('task_id') != self.task_id or owner.get('status') != 'running':
                await db.rollback(); raise DocumentConflict('craft task superseded')
            row = await (await db.execute('SELECT data FROM script_craft_contexts WHERE owner_type=? AND owner_id=? AND step_key=?',key)).fetchone()
            value = json.loads(row['data'])
            old = value['author_session']
            if old['id'] != session['id'] or (old.get('provider_session_id') and old['provider_session_id'] != session.get('provider_session_id')):
                await db.rollback(); raise CraftCheckpointConflict('author session cannot be replaced by another worker')
            value['author_session'] = {**old,**session}
            await db.execute('UPDATE script_craft_contexts SET data=? WHERE owner_type=? AND owner_id=? AND step_key=?',
                             (json.dumps(value,ensure_ascii=False),*key))
            await db.commit()

    async def validate_memory(self):
        async with self.store._db() as db:
            row = await (await db.execute('SELECT data FROM script_craft_contexts WHERE owner_type=? AND owner_id=? AND step_key=?',
                (self.owner_type,self.owner_id,self.step_key))).fetchone()
            if not row:
                return
            for entry in json.loads(row['data']).get('memory',[]):
                state = await (await db.execute('SELECT dismissed FROM script_craft_memory WHERE id=?',(entry['id'],))).fetchone()
                if state is None or state['dismissed']:
                    raise DocumentConflict('本轮引用的历史审稿意见已撤销，请重新生成')
                for identity, revision in entry.get('references',{}).items():
                    current = await (await db.execute('SELECT current_revision_id FROM script_documents WHERE id=?',(identity,))).fetchone()
                    if current is None or current['current_revision_id'] != revision:
                        raise DocumentConflict('历史审稿依据已变化，请重新生成')

    async def remember(self):
        from .craft_memory import CraftMemory
        context = await self.context('rewrite' if self.owner_type == 'rewrite' else self.step_key.split(':')[0])
        table = 'script_generation_runs' if self.owner_type == 'generation' else 'script_rewrite_jobs'
        async with self.store._db() as db:
            row = await (await db.execute(f'SELECT data FROM {table} WHERE id=?',(self.owner_id,))).fetchone()
            owner = json.loads(row['data'])
        target = next(s for s in owner['steps'] if s['key']==self.step_key) if self.owner_type=='generation' else owner
        references = dict(target.get('context_revisions',{}))
        if self.owner_type=='rewrite':
            references[owner['document_id']] = owner['base_revision_id']
        await CraftMemory(self.store).remember(kind=context['kind'],source=f'{self.owner_type}:{self.owner_id}:{self.step_key}',
            records=await self.records(),references=references)

    async def stage_started(self, stage: str) -> None:
        await self.validate_memory()
        table = 'script_generation_runs' if self.owner_type == 'generation' else 'script_rewrite_jobs'
        async with self.store._db() as db:
            await db.execute('BEGIN IMMEDIATE')
            row = await (await db.execute(f'SELECT data FROM {table} WHERE id=?', (self.owner_id,))).fetchone()
            owner = json.loads(row['data']) if row else None
            if not owner or owner.get('task_id') != self.task_id or owner['status'] != 'running':
                await db.rollback()
                raise DocumentConflict('craft task superseded')
            target = next(s for s in owner['steps'] if s['key'] == self.step_key) if self.owner_type == 'generation' else owner
            target['craft_stage'] = stage
            owner['updated_at'] = _now()
            await db.execute(f'UPDATE {table} SET data=?,updated_at=? WHERE id=?',
                             (json.dumps(owner, ensure_ascii=False), owner['updated_at'], self.owner_id))
            await db.commit()

    async def records(self) -> list[dict]:
        async with self.store._db() as db:
            rows = await (await db.execute(
                'SELECT data FROM script_craft_records WHERE owner_type=? AND owner_id=? '
                'AND step_key=? ORDER BY rowid',
                (self.owner_type, self.owner_id, self.step_key))).fetchall()
        return [json.loads(row['data']) for row in rows]

    async def append(self, record: dict) -> None:
        table = 'script_generation_runs' if self.owner_type == 'generation' else 'script_rewrite_jobs'
        async with self.store._db() as db:
            await db.execute('BEGIN IMMEDIATE')
            try:
                row = await (await db.execute(f'SELECT data FROM {table} WHERE id=?', (self.owner_id,))).fetchone()
                if row is None:
                    raise DocumentNotFound('craft owner not found')
                owner = json.loads(row['data'])
                if owner.get('task_id') != self.task_id or owner['status'] != 'running':
                    raise DocumentConflict('craft task superseded')
                key = (self.owner_type, self.owner_id, self.step_key, record['stage'])
                existing = await (await db.execute(
                    'SELECT data FROM script_craft_records WHERE owner_type=? AND owner_id=? AND step_key=? AND stage=?', key)).fetchone()
                if existing:
                    if json.loads(existing['data']) != record:
                        raise CraftCheckpointConflict('craft checkpoint cannot be overwritten')
                else:
                    await db.execute('INSERT INTO script_craft_records VALUES (?,?,?,?,?,?)',
                                     (*key, json.dumps(record, ensure_ascii=False), _now()))
                if self.owner_type == 'generation':
                    step = next(s for s in owner['steps'] if s['key'] == self.step_key)
                    step['craft_stage'] = record['stage']
                else:
                    owner['craft_stage'] = record['stage']
                owner['updated_at'] = _now()
                await db.execute(f'UPDATE {table} SET data=?,updated_at=? WHERE id=?',
                                 (json.dumps(owner, ensure_ascii=False), owner['updated_at'], self.owner_id))
                await db.commit()
            except BaseException:
                await db.rollback()
                raise


        if 'issues' in record['output']:
            await self.remember()


async def craft_details(store: DocumentStore, owner_type: str, owner_id: str) -> dict:
    table = {'generation': 'script_generation_runs', 'rewrite': 'script_rewrite_jobs'}[owner_type]
    async with store._db() as db:
        row = await (await db.execute(f'SELECT data FROM {table} WHERE id=?', (owner_id,))).fetchone()
        if row is None:
            raise DocumentNotFound('craft owner not found')
        owner = json.loads(row['data'])
        records = await (await db.execute(
            'SELECT step_key,data FROM script_craft_records WHERE owner_type=? AND owner_id=? ORDER BY rowid',
            (owner_type, owner_id))).fetchall()
        contexts = await (await db.execute('SELECT step_key,data FROM script_craft_contexts WHERE owner_type=? AND owner_id=?',
                                          (owner_type,owner_id))).fetchall()
    steps = owner.get('steps', [owner])
    expected = {key: value for step in steps for key, value in step.get('context_revisions', {}).items()}
    if owner_type == 'rewrite':
        expected[owner['document_id']] = owner['base_revision_id']
    for step in steps:
        output = step.get('output') or {}
        if output.get('kind') == 'candidate':
            expected[output['document_id']] = output['baseline_revision_id']
    stale = owner['status'] == 'needs_rebase'
    for doc_id, revision in expected.items():
        try:
            stale = stale or (await store.get(doc_id)).current_revision_id != revision
        except DocumentNotFound:
            stale = True
    for step in steps:
        output = step.get('output') or {}
        if output.get('kind') == 'document':
            try:
                stale = stale or (await store.get(output['document_id'])).current_revision_id != output['revision_id']
            except DocumentNotFound:
                stale = True
    return {'owner_id': owner_id, 'status': owner['status'], 'stale': stale,
            'contexts': [{'step_key':r['step_key'],**json.loads(r['data'])} for r in contexts],
            'steps': [{'key': s.get('key', 'rewrite'), 'quality': s.get('craft_review', {'status': 'unreviewed'})}
                      for s in steps],
            'records': [{'step_key': r['step_key'], **json.loads(r['data'])} for r in records]}


async def validate_candidate_dependencies(db, store, candidate_id: str) -> set[str]:
    """Verify exact upstream candidate adoption in the caller's write transaction."""
    rows = await (await db.execute(
        'SELECT * FROM script_craft_dependencies WHERE candidate_id=?', (candidate_id,))).fetchall()
    adopted = set()
    for dep in rows:
        doc = await store._document(db, dep['document_id'])
        if text_hash(doc.revision.markdown) != dep['content_hash']:
            raise DocumentConflict('请先采纳本候选依赖的上游设计，再重新审阅', doc.current_revision_id)
        adopted.add(doc.id)
    return adopted
