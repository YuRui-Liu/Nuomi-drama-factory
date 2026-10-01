"""Transactional library, project references, optimistic drafts and durable jobs."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class Conflict(ValueError):
    pass


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class MusicStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                  revision INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, asset TEXT NOT NULL REFERENCES assets(id), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS refs(project TEXT NOT NULL, version TEXT NOT NULL REFERENCES versions(id),
                  favorite INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project,version));
                CREATE TABLE IF NOT EXISTS plans(project TEXT, canvas TEXT, node TEXT, revision INTEGER, body TEXT,
                  PRIMARY KEY(project,canvas,node));
                CREATE TABLE IF NOT EXISTS jobs(project TEXT, id TEXT, kind TEXT, input TEXT, body TEXT,
                  PRIMARY KEY(project,id));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_asset(self, owner, *, path, sha256, duration_ms, name, kind='audio', library=True, **metadata):
        asset_id, version_id = uuid4().hex, uuid4().hex
        body = dict(id=asset_id, versionId=version_id, name=name, durationMs=duration_ms,
                    kind=kind, revision=1, archived=False, library=library, tags=[], vocals='unknown', **metadata)
        version = {**body, 'path': str(path), 'sha256': sha256}
        with self.db() as db:
            db.execute('INSERT INTO assets VALUES(?,?,?,?)', (asset_id, owner, 1, dump(body)))
            db.execute('INSERT INTO versions VALUES(?,?,?)', (version_id, asset_id, dump(version)))
        return body

    def list_assets(self, owner):
        with self.db() as db:
            rows = db.execute('SELECT body FROM assets WHERE owner=? ORDER BY rowid DESC', (owner,))
            return [a for r in rows if (a := json.loads(r['body']))['library'] and a['kind'] == 'audio' and not a['archived']]

    def update_asset(self, owner, asset_id, revision, changes):
        allowed = {'name', 'tags', 'description', 'vocals', 'archived', 'library'}
        if changes.keys() - allowed:
            raise ValueError('不支持的音乐属性')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM assets WHERE id=? AND owner=?', (asset_id, owner)).fetchone()
            if not row:
                raise PermissionError('音乐不存在或不可访问')
            if row['revision'] != revision:
                raise Conflict('音乐已被更新，请刷新')
            body = {**json.loads(row['body']), **changes, 'revision': revision + 1}
            db.execute('UPDATE assets SET revision=?,body=? WHERE id=?', (revision + 1, dump(body), asset_id))
        return body

    def version(self, version_id, *, owner=None, project=None):
        with self.db() as db:
            row = db.execute('SELECT v.body,a.owner FROM versions v JOIN assets a ON a.id=v.asset WHERE v.id=?', (version_id,)).fetchone()
            grant = project is not None and db.execute('SELECT 1 FROM refs WHERE project=? AND version=?', (project, version_id)).fetchone()
            if not row or not (owner is not None and row['owner'] == owner or grant):
                raise PermissionError('音乐不存在或不可访问')
            return json.loads(row['body'])

    def reference(self, owner, project, version_id, *, favorite=False):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT a.owner,a.body FROM versions v JOIN assets a ON a.id=v.asset WHERE v.id=?', (version_id,)).fetchone()
            old = db.execute('SELECT 1 FROM refs WHERE project=? AND version=?', (project, version_id)).fetchone()
            if not row or (row['owner'] != owner and not old):
                raise PermissionError('音乐不存在或不可访问')
            if not old and json.loads(row['body'])['archived']:
                raise ValueError('音乐已归档')
            db.execute('INSERT INTO refs VALUES(?,?,?) ON CONFLICT(project,version) DO UPDATE SET favorite=MAX(favorite,excluded.favorite)', (project, version_id, int(favorite)))
        return self.version(version_id, project=project)

    def favorites(self, project):
        with self.db() as db:
            return [json.loads(r['body']) for r in db.execute('SELECT v.body FROM refs r JOIN versions v ON v.id=r.version WHERE r.project=? AND r.favorite=1', (project,))]

    def plan(self, project, canvas, node):
        with self.db() as db:
            row = db.execute('SELECT body FROM plans WHERE project=? AND canvas=? AND node=?', (project, canvas, node)).fetchone()
            return json.loads(row['body']) if row else None

    def save_plan(self, project, canvas, node, body, expected):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM plans WHERE project=? AND canvas=? AND node=?', (project, canvas, node)).fetchone()
            current = row['revision'] if row else 0
            if current != expected:
                raise Conflict('草稿已更新，本地修改已保留，请重新载入')
            result = {**body, 'revision': current + 1}
            db.execute('INSERT INTO plans VALUES(?,?,?,?,?) ON CONFLICT(project,canvas,node) DO UPDATE SET revision=excluded.revision,body=excluded.body', (project, canvas, node, current + 1, dump(result)))
            return result

    def create_job(self, project, job_id, kind, inputs):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM jobs WHERE project=? AND id=?', (project, job_id)).fetchone()
            if old:
                if old['input'] != dump(inputs) or old['kind'] != kind:
                    raise Conflict('请求标识已用于不同任务')
                return json.loads(old['body']), False
            body = dict(id=job_id, kind=kind, status='queued', input=inputs, createdAt=datetime.now(timezone.utc).isoformat())
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)', (project, job_id, kind, dump(inputs), dump(body)))
            return body, True

    def job(self, project, job_id):
        with self.db() as db:
            row = db.execute('SELECT body FROM jobs WHERE project=? AND id=?', (project, job_id)).fetchone()
            if not row:
                raise ValueError('任务不存在')
            return json.loads(row['body'])

    def jobs(self, project):
        with self.db() as db:
            return [json.loads(r['body']) for r in db.execute('SELECT body FROM jobs WHERE project=? ORDER BY rowid DESC LIMIT 100', (project,))]

    def update_job(self, project, job_id, **changes):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM jobs WHERE project=? AND id=?', (project, job_id)).fetchone()
            if not row:
                raise ValueError('任务不存在')
            body = {**json.loads(row['body']), **changes}
            db.execute('UPDATE jobs SET body=? WHERE project=? AND id=?', (dump(body), project, job_id))
            return body

    def claim_job(self, project, job_id, expected='queued', status='submitting'):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM jobs WHERE project=? AND id=?', (project, job_id)).fetchone()
            if not row:
                return False
            body = json.loads(row['body'])
            if body['status'] != expected:
                return False
            body['status'] = status
            db.execute('UPDATE jobs SET body=? WHERE project=? AND id=?', (dump(body),project,job_id))
            return True
