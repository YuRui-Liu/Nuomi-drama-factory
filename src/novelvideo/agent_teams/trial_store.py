"""Independent candidate storage with atomic submission and per-side reservations."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


class TrialConflict(ValueError):
    pass


class TrialStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS trials (project TEXT, id TEXT, fingerprint TEXT, data TEXT, PRIMARY KEY(project,id))')

    def db(self):
        return closing(sqlite3.connect(self.path, timeout=30, isolation_level=None))

    def get(self, project, id):
        with self.db() as db:
            row = db.execute('SELECT data FROM trials WHERE project=? AND id=?', (project, id)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, project):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM trials WHERE project=? ORDER BY rowid DESC LIMIT 100', (project,))]

    def create(self, project, id, fingerprint, data):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT fingerprint,data FROM trials WHERE project=? AND id=?', (project,id)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise TrialConflict('request_id already has different input or methods')
                return json.loads(old[1]), False
            value = {**data, 'id': id, 'project_id': project, 'created_at': datetime.now(timezone.utc).isoformat(),
                     'sides': {s: {'status': 'queued', 'attempt': 1, 'candidate': None, 'error': None, 'duration_seconds': None, 'cost': None} for s in ('active','draft')}}
            db.execute('INSERT INTO trials VALUES(?,?,?,?)', (project,id,fingerprint,json.dumps(value,ensure_ascii=False)))
            db.commit()
            return value, True

    def transition(self, project, id, side, expected_attempt, expected, **changes):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM trials WHERE project=? AND id=?', (project,id)).fetchone()
            if not row:
                raise KeyError(id)
            value = json.loads(row[0])
            state = value['sides'][side]
            if state['attempt'] != expected_attempt or state['status'] not in expected:
                return None
            state.update(changes)
            db.execute('UPDATE trials SET data=? WHERE project=? AND id=?', (json.dumps(value,ensure_ascii=False),project,id))
            db.commit()
            return value

    def retry(self, project, id, side):
        value = self.get(project,id)
        if value is None:
            raise KeyError(id)
        attempt = value['sides'][side]['attempt']
        result = self.transition(project,id,side,attempt,{'failed','cancelled'}, status='queued',attempt=attempt+1,error=None,candidate=None,duration_seconds=None,task_id=None)
        if result is None:
            raise TrialConflict('only a failed or cancelled side can be retried')
        return result
