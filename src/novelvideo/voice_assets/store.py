"""Independent audio copies with transactional, owner-scoped deduplication."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Tags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timbre: list[str] = Field(default_factory=list, max_length=20)
    identity: list[str] = Field(default_factory=list, max_length=20)
    personality: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("timbre", "identity", "personality")
    @classmethod
    def clean(cls, values):
        result = list(dict.fromkeys(v.strip() for v in values))
        if any(not v or len(v) > 30 for v in result):
            raise ValueError("标签须为 1–30 字")
        return result


class AssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    tags: Tags = Field(default_factory=Tags)


class VoiceAssetStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, sha256 TEXT NOT NULL,
                extension TEXT NOT NULL, duration REAL NOT NULL, metadata TEXT NOT NULL,
                sources TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(owner,sha256))""")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / "index.sqlite3", timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            raise KeyError("配音资产不存在")
        result = dict(row)
        result.pop("owner")
        result.update(json.loads(result.pop("metadata")))
        result["sources"] = json.loads(result["sources"])
        return result

    def get(self, owner, asset_id):
        with self.db() as db:
            return self.decode(db.execute("SELECT * FROM assets WHERE owner=? AND id=?", (owner, asset_id)).fetchone())

    def save(self, owner, content, extension, duration, metadata, source):
        if extension not in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"} or not content:
            raise ValueError("不支持的音频格式")
        digest = hashlib.sha256(content).hexdigest()
        path = None
        try:
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute("SELECT * FROM assets WHERE owner=? AND sha256=?", (owner, digest)).fetchone()
                if old:
                    sources = json.loads(old["sources"])
                    if source not in sources:
                        sources.append(source)
                        db.execute("UPDATE assets SET sources=? WHERE id=?", (json.dumps(sources), old["id"]))
                    result = self.decode(old)
                    result["sources"] = sources
                    return result, False
                asset_id = uuid4().hex
                path = self.root / f"{asset_id}{extension}"
                temporary = path.with_suffix(".tmp")
                try:
                    temporary.write_bytes(content)
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
                db.execute("INSERT INTO assets(id,owner,sha256,extension,duration,metadata,sources) VALUES(?,?,?,?,?,?,?)",
                           (asset_id, owner, digest, extension, duration, metadata.model_dump_json(), json.dumps([source])))
            return self.get(owner, asset_id), True
        except Exception:
            if path is not None:
                path.unlink(missing_ok=True)
            raise

    def list(self, owner, q="", tags=None, offset=0, limit=30):
        with self.db() as db:
            rows = [self.decode(row) for row in db.execute("SELECT * FROM assets WHERE owner=? ORDER BY updated_at DESC,id", (owner,))]
        rows = [r for r in rows if q.casefold() in r["name"].casefold() and all(
            not values or set(values).intersection(r["tags"].get(group, [])) for group, values in (tags or {}).items())]
        return {"items": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit}

    def tags(self, owner):
        with self.db() as db:
            rows = [self.decode(row) for row in db.execute("SELECT * FROM assets WHERE owner=?", (owner,))]
        return {group: sorted({t for row in rows for t in row["tags"][group]}) for group in ("timbre", "identity", "personality")}

    def update(self, owner, asset_id, metadata):
        with self.db() as db:
            if db.execute("UPDATE assets SET metadata=?,updated_at=CURRENT_TIMESTAMP WHERE owner=? AND id=?",
                          (metadata.model_dump_json(), owner, asset_id)).rowcount != 1:
                raise KeyError("配音资产不存在")
        return self.get(owner, asset_id)

    def read(self, owner, asset_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            asset = self.decode(db.execute("SELECT * FROM assets WHERE owner=? AND id=?", (owner, asset_id)).fetchone())
            content = (self.root / f"{asset['id']}{asset['extension']}").read_bytes()
            if hashlib.sha256(content).hexdigest() != asset["sha256"]:
                raise ValueError("音频完整性校验失败")
            return asset, content

    def delete(self, owner, asset_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            asset = self.decode(db.execute("SELECT * FROM assets WHERE owner=? AND id=?", (owner, asset_id)).fetchone())
            db.execute("DELETE FROM assets WHERE owner=? AND id=?", (owner, asset_id))
        (self.root / f"{asset['id']}{asset['extension']}").unlink(missing_ok=True)
