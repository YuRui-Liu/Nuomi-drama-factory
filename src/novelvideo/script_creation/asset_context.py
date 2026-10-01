"""Read authored asset context without granting it source-fact authority."""
import hashlib
import json
from pathlib import Path
import sqlite3


def load_asset_authoring_context(db_path, asset_type, name):
    tables_by_type = {'character': 'characters', 'scene': 'scenes', 'prop': 'props'}
    if asset_type not in tables_by_type or not Path(db_path).is_file():
        return []
    result = []
    seen = set()
    def add(document, revision, block, title, text):
        key = (document, revision, block)
        if text and text.strip() and key not in seen:
            seen.add(key)
            result.append(dict(kind='authoring_context', source_document=document,
                source_revision_id=revision, source_block_id=block, title=title,
                text=text, content_hash=hashlib.sha256(text.encode()).hexdigest()))
    with sqlite3.connect(f'file:{Path(db_path)}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table = tables_by_type[asset_type]
        if table in tables:
            row = db.execute(f'SELECT description FROM {table} WHERE name=?', (name,)).fetchone()
            if row:
                add(f'{asset_type}:{name}', '', '', name, row[0] or '')
        if not {'script_documents', 'script_revisions'} <= tables:
            return result
        for row in db.execute("""SELECT d.id,d.title,r.id revision,r.markdown
                FROM script_documents d JOIN script_revisions r ON r.id=d.current_revision_id
                WHERE d.kind='outline' ORDER BY d.id"""):
            add(row['id'], row['revision'], '', row['title'], row['markdown'])
        queries = []
        if {'script_asset_context_links', 'asset_registry'} <= tables:
            queries.append("""SELECT l.document_id document,l.source_block_id block,
                d.title,r.id revision,r.blocks FROM script_asset_context_links l
                JOIN asset_registry a ON a.asset_uuid=l.target_asset_id
                JOIN asset_registry parent ON parent.asset_uuid=l.source_asset_id
                JOIN script_documents d ON d.id=l.document_id
                JOIN script_revisions r ON r.id=d.current_revision_id
                WHERE a.kind=? AND a.current_name=? AND a.deleted_at IS NULL
                AND parent.deleted_at IS NULL AND l.source_revision_id=d.current_revision_id
                ORDER BY l.source_asset_id,l.source_block_id""")
        if {'script_entities', 'asset_registry'} <= tables:
            queries.append("""SELECT e.document_id document,e.block_id block,d.title,
                r.id revision,r.blocks FROM script_entities e
                JOIN asset_registry a ON a.asset_uuid=e.asset_id
                JOIN script_documents d ON d.id=e.document_id
                JOIN script_revisions r ON r.id=d.current_revision_id
                WHERE a.kind=? AND a.current_name=? AND a.deleted_at IS NULL
                AND e.confirmed_revision=d.current_revision_id
                AND e.selected_revision=d.current_revision_id ORDER BY e.entity_id""")
        if {'script_prop_import_links', 'asset_registry'} <= tables:
            queries.append("""SELECT l.source_document_id document,l.source_block_id block,
                d.title,r.id revision,r.blocks FROM script_prop_import_links l
                JOIN asset_registry a ON a.asset_uuid=l.asset_id
                JOIN script_documents d ON d.id=l.source_document_id
                JOIN script_revisions r ON r.id=d.current_revision_id
                WHERE a.kind=? AND a.current_name=? AND a.deleted_at IS NULL
                AND l.source_revision_id=d.current_revision_id ORDER BY l.id""")
        for query in queries:
            for row in db.execute(query, (asset_type, name)):
                block = next((b for b in json.loads(row['blocks']) if b['id'] == row['block']), None)
                if block:
                    add(row['document'], row['revision'], row['block'], row['title'], block.get('markdown', ''))
    return result
