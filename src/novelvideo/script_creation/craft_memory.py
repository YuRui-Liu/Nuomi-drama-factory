"""Project-local QC experience: evidence-backed reminders, never project facts."""
from __future__ import annotations

import hashlib
import json
from .store import DocumentNotFound, DocumentValidation, _now


class CraftMemory:
    def __init__(self, store):
        self.store = store

    async def remember(self, *, kind, source, records, references):
        entries, roots = {}, {}
        for record in records:
            output = record['output']
            for issue in output.get('issues', []):
                key = roots.get(issue.get('prior_issue_id'), issue['id'])
                roots[issue['id']] = key
                entry = entries.setdefault(key, {'issue': issue, 'history': [], 'status': 'open'})
                entry['issue'] = issue
                entry['status'] = 'open'
                entry['history'].append({'stage': record['stage'], 'qc_issue': issue})
            for note in output.get('repair_notes', []):
                key = roots.get(note['issue_id'], note['issue_id'])
                if key in entries:
                    entries[key]['history'].append({'stage': record['stage'], 'author_attempt': note})
            for resolved in output.get('resolved_issue_ids', []):
                key = roots.get(resolved, resolved)
                if key in entries:
                    entries[key]['status'] = 'resolved'
                    entries[key]['history'].append({'stage': record['stage'], 'qc_resolution': resolved})
        stamp = _now()
        async with self.store._db() as db:
            for key, entry in entries.items():
                entry.update(references=references, authority='historical_qc_reminder_not_fact', source=source)
                identity = hashlib.sha256(f'{source}:{key}'.encode()).hexdigest()
                await db.execute('INSERT INTO script_craft_memory (id,kind,source,data,created_at,updated_at) VALUES (?,?,?,?,?,?) '
                    'ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at',
                    (identity,kind,source,json.dumps(entry,ensure_ascii=False),stamp,stamp))
            await db.commit()

    async def list(self, *, kind=None):
        async with self.store._db() as db:
            query = 'SELECT * FROM script_craft_memory'
            params = ()
            if kind:
                query += ' WHERE kind=?'; params = (kind,)
            rows = await (await db.execute(query+' ORDER BY updated_at DESC,id LIMIT 100',params)).fetchall()
        current = {d.id:d.current_revision_id for d in await self.store.list()}
        result = []
        for row in rows:
            value = {**json.loads(row['data']), 'id':row['id'], 'kind':row['kind'],
                     'dismissed':bool(row['dismissed']), 'dismissal_reason':row['dismissal_reason']}
            value['stale'] = any(current.get(i) != rev for i,rev in value.get('references',{}).items())
            result.append(value)
        return result

    async def snapshot(self, kind):
        selected, size = [], 0
        for entry in await self.list(kind=kind):
            if entry['dismissed'] or entry['stale']:
                continue
            amount = len(json.dumps(entry,ensure_ascii=False))
            # Optional historical hints have a separate bounded budget; never
            # truncate an evidence quote into an apparently valid new quote.
            if size+amount <= 16000 and len(selected)<12:
                selected.append(entry); size += amount
        return selected

    async def dismiss(self, identity, reason):
        if not reason.strip() or len(reason)>2000:
            raise DocumentValidation('请填写撤销原因（不超过2000字）')
        async with self.store._db() as db:
            cursor = await db.execute('UPDATE script_craft_memory SET dismissed=1,dismissal_reason=?,updated_at=? WHERE id=?',
                                     (reason.strip(),_now(),identity))
            if not cursor.rowcount:
                raise DocumentNotFound('craft memory not found')
            await db.commit()
