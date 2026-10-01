"""Explicit scene design-source associations, separate from spatial variants."""
import json

from .store import DocumentConflict, DocumentNotFound, DocumentValidation, _digest, _now


class SceneContextLinks:
    def __init__(self, store):
        self.store = store

    async def initialize(self):
        async with self.store._db() as db:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS script_asset_context_links (
                    target_asset_id TEXT NOT NULL,source_asset_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,source_revision_id TEXT NOT NULL,
                    source_block_id TEXT NOT NULL,created_at TEXT NOT NULL,
                    UNIQUE(target_asset_id,source_asset_id,document_id,source_revision_id,source_block_id));
                CREATE TABLE IF NOT EXISTS script_context_link_mutations (
                    mutation_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,result TEXT NOT NULL);
            ''')
            await db.commit()

    async def list(self, target_asset_id):
        async with self.store._db() as db:
            rows = await (await db.execute('''SELECT l.*,a.current_name source_name,
                d.current_revision_id FROM script_asset_context_links l
                JOIN asset_registry a ON a.asset_uuid=l.source_asset_id
                JOIN script_documents d ON d.id=l.document_id
                WHERE l.target_asset_id=? ORDER BY l.created_at,l.source_block_id''', (target_asset_id,))).fetchall()
            return [dict(row) | {'stale': row['source_revision_id'] != row['current_revision_id']} for row in rows]

    async def associate(self, *, source_asset_id, target_asset_ids, document_id, base_revision_id, client_mutation_id):
        if not target_asset_ids or len(set(target_asset_ids)) != len(target_asset_ids) or source_asset_id in target_asset_ids:
            raise DocumentValidation('请选择不同于来源场景的目标子场景，不能重复')
        digest = _digest(dict(source=source_asset_id, targets=sorted(target_asset_ids), document=document_id, revision=base_revision_id))
        async with self.store._db() as db:
            await db.execute('BEGIN IMMEDIATE')
            old = await (await db.execute('SELECT * FROM script_context_link_mutations WHERE mutation_id=?', (client_mutation_id,))).fetchone()
            if old:
                if old['payload_hash'] != digest:
                    raise DocumentConflict('关联请求编号已用于其他内容')
                return json.loads(old['result'])
            doc = await self.store._document(db, document_id)
            if doc.kind != 'scenes' or doc.current_revision_id != base_revision_id:
                raise DocumentConflict('场景来源版本已变化，请刷新后确认', doc.current_revision_id)
            for asset_id in [source_asset_id, *target_asset_ids]:
                asset = await (await db.execute('''SELECT a.asset_uuid FROM asset_registry a
                    JOIN scenes s ON s.name=a.current_name
                    WHERE a.asset_uuid=? AND a.kind='scene' AND a.deleted_at IS NULL''', (asset_id,))).fetchone()
                if not asset:
                    raise DocumentNotFound('来源或目标场景不存在于当前项目')
            tables = {r[0] for r in await (await db.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
            block_ids = set()
            if 'script_prop_import_links' in tables:
                rows = await (await db.execute('''SELECT source_block_id FROM script_prop_import_links
                    WHERE asset_id=? AND source_document_id=? AND source_revision_id=?''',
                    (source_asset_id, document_id, base_revision_id))).fetchall()
                block_ids.update(r[0] for r in rows)
            if 'script_entities' in tables:
                rows = await (await db.execute('''SELECT block_id FROM script_entities WHERE asset_id=?
                    AND document_id=? AND selected_revision=? AND confirmed_revision=? AND asset_type='scene' ''',
                    (source_asset_id, document_id, base_revision_id, base_revision_id))).fetchall()
                block_ids.update(r[0] for r in rows)
            valid_blocks = {b.id for b in doc.revision.blocks}
            if not block_ids or not block_ids <= valid_blocks:
                raise DocumentValidation('来源场景没有此版本已确认的创作来源，请先关联或导入场景表')
            for target in target_asset_ids:
                for block in sorted(block_ids):
                    await db.execute('INSERT OR IGNORE INTO script_asset_context_links VALUES (?,?,?,?,?,?)',
                        (target, source_asset_id, document_id, base_revision_id, block, _now()))
            result = dict(source_asset_id=source_asset_id, target_asset_ids=target_asset_ids,
                document_id=document_id, source_revision_id=base_revision_id, source_block_ids=sorted(block_ids))
            await db.execute('INSERT INTO script_context_link_mutations VALUES (?,?,?)',
                (client_mutation_id, digest, json.dumps(result, ensure_ascii=False)))
            await db.commit()
            return result
