"""Project-local immutable document revisions."""
from __future__ import annotations

import hashlib
from difflib import SequenceMatcher
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiosqlite

from .models import Block, Document, KINDS, Revision


class DocumentNotFound(LookupError):
    pass


class DocumentValidation(ValueError):
    pass


class DocumentConflict(RuntimeError):
    def __init__(self, message="revision conflict", current_revision_id=None):
        super().__init__(message)
        self.current_revision_id = current_revision_id


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id():
    return uuid4().hex


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _as_json_blocks(blocks):
    return [{"id": b.id, "markdown": b.markdown} for b in blocks]


def _parts(markdown):
    if not markdown:
        return []
    bits = markdown.split("\n\n")
    return [part + ("\n\n" if index < len(bits) - 1 else "") for index, part in enumerate(bits)]


def _blocks(markdown, supplied=None, previous=(), *, allow_client_ids=False):
    if supplied is None:
        parts = _parts(markdown)
        previous_text = [block.markdown.rstrip("\n") for block in previous]
        current_text = [part.rstrip("\n") for part in parts]
        matched = {}
        for a, b, size in SequenceMatcher(None, previous_text, current_text, autojunk=False).get_matching_blocks():
            for offset in range(size):
                matched[b + offset] = previous[a + offset].id
        used = set(matched.values())
        result = []
        for index, part in enumerate(parts):
            block_id = matched.get(index)
            if block_id is None and index < len(previous) and previous[index].id not in used:
                block_id = previous[index].id
            block_id = block_id or _id()
            used.add(block_id)
            result.append(Block(block_id, part))
        return tuple(result)
    known = {block.id for block in previous}
    seen = set()
    result = []
    for raw in supplied:
        block = raw if isinstance(raw, Block) else Block(**raw)
        block_id = block.id or _id()
        if block_id in seen or (not allow_client_ids and block.id and block.id not in known):
            raise DocumentValidation("duplicate or foreign block id")
        seen.add(block_id)
        result.append(Block(block_id, block.markdown))
    if "".join(block.markdown for block in result) != markdown:
        raise DocumentValidation("blocks must concatenate to markdown exactly")
    return tuple(result)


class DocumentStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)

    @asynccontextmanager
    async def _db(self):
        db = await aiosqlite.connect(self.db_path, timeout=10)
        try:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=10000")
            yield db
        finally:
            await db.close()

    async def initialize(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._db() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS script_documents (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                    episode_number INTEGER, current_revision_id TEXT NOT NULL,
                    adopted_revision_id TEXT, source_origin TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS script_revisions (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    parent_revision_id TEXT, markdown TEXT NOT NULL, blocks TEXT NOT NULL,
                    client_mutation_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    restored_from_revision_id TEXT);
                CREATE INDEX IF NOT EXISTS idx_script_revisions_doc ON script_revisions(document_id, created_at);
                CREATE TABLE IF NOT EXISTS script_mutations (
                    scope TEXT NOT NULL, key TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    PRIMARY KEY(scope,key));
                CREATE UNIQUE INDEX IF NOT EXISTS idx_script_source_origin ON script_documents(
                    json_extract(source_origin, '$.source_episode_number'))
                    WHERE source_origin IS NOT NULL;
            """)
            await db.commit()

    async def _revision(self, db, revision_id):
        row = await (await db.execute("SELECT * FROM script_revisions WHERE id=?", (revision_id,))).fetchone()
        if row is None:
            raise DocumentNotFound("revision not found")
        return Revision(row["id"], row["document_id"], row["parent_revision_id"], row["markdown"],
                        tuple(Block(**item) for item in json.loads(row["blocks"])),
                        row["client_mutation_id"], row["created_at"], row["restored_from_revision_id"])

    async def _document(self, db, document_id, revision_id=None):
        row = await (await db.execute("SELECT * FROM script_documents WHERE id=?", (document_id,))).fetchone()
        if row is None:
            raise DocumentNotFound("document not found")
        revision = await self._revision(db, revision_id or row["current_revision_id"])
        return Document(row["id"], row["kind"], row["title"], row["episode_number"],
                        row["current_revision_id"], row["adopted_revision_id"],
                        json.loads(row["source_origin"]) if row["source_origin"] else None,
                        row["created_at"], revision.created_at if revision_id else row["updated_at"], revision)

    async def _replay(self, db, scope, key, digest):
        row = await (await db.execute("SELECT * FROM script_mutations WHERE scope=? AND key=?", (scope, key))).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != digest:
            current = await self._document(db, row["document_id"])
            raise DocumentConflict("mutation id reused with different payload",
                                   current_revision_id=current.current_revision_id)
        document = await self._document(db, row["document_id"], row["revision_id"])
        return replace(document, current_revision_id=row["revision_id"])

    async def get(self, document_id):
        async with self._db() as db:
            return await self._document(db, document_id)

    async def list(self):
        async with self._db() as db:
            rows = await (await db.execute("SELECT id FROM script_documents ORDER BY created_at,id")).fetchall()
            return [await self._document(db, row["id"]) for row in rows]

    async def revisions(self, document_id):
        async with self._db() as db:
            await self._document(db, document_id)
            rows = await (await db.execute("SELECT id FROM script_revisions WHERE document_id=? ORDER BY created_at,rowid",
                                           (document_id,))).fetchall()
            return [await self._revision(db, row["id"]) for row in rows]

    async def create(self, *, kind, title, markdown="", client_mutation_id,
                     episode_number=None, blocks=None, source_origin=None):
        if kind not in KINDS or not title.strip() or not client_mutation_id.strip():
            raise DocumentValidation("invalid kind, title, or client_mutation_id")
        if episode_number is not None and episode_number < 1:
            raise DocumentValidation("episode_number must be positive")
        normalized = ([{"id": item.id, "markdown": item.markdown} if isinstance(item, Block) else item
                       for item in blocks] if blocks is not None else None)
        parsed = _blocks(markdown, normalized, allow_client_ids=True)
        digest = _digest(dict(kind=kind, title=title, markdown=markdown, episode_number=episode_number,
                              blocks=normalized, source_origin=source_origin))
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                replay = await self._replay(db, "create", client_mutation_id, digest)
                if replay:
                    await db.rollback()
                    return replay
                document_id, revision_id, stamp = _id(), _id(), _now()
                await db.execute("INSERT INTO script_documents VALUES (?,?,?,?,?,?,?,?,?)",
                                 (document_id, kind, title, episode_number, revision_id, None,
                                  json.dumps(source_origin, ensure_ascii=False) if source_origin else None, stamp, stamp))
                await db.execute("INSERT INTO script_revisions VALUES (?,?,?,?,?,?,?,?)",
                                 (revision_id, document_id, None, markdown,
                                  json.dumps(_as_json_blocks(parsed), ensure_ascii=False), client_mutation_id, stamp, None))
                await db.execute("INSERT INTO script_mutations VALUES (?,?,?,?,?)",
                                 ("create", client_mutation_id, digest, document_id, revision_id))
                response = await self._document(db, document_id)
                await db.commit()
                return response
            except Exception:
                await db.rollback()
                raise

    async def save(self, document_id, *, base_revision_id, markdown, client_mutation_id, blocks=None):
        return await self._change(document_id, base_revision_id=base_revision_id, markdown=markdown,
                                  client_mutation_id=client_mutation_id, blocks=blocks, restored_from_revision_id=None)

    async def restore(self, document_id, *, revision_id, base_revision_id, client_mutation_id):
        async with self._db() as db:
            revision = await self._revision(db, revision_id)
            if revision.document_id != document_id:
                raise DocumentNotFound("revision not found")
        return await self._change(document_id, base_revision_id=base_revision_id, markdown=revision.markdown,
                                  client_mutation_id=client_mutation_id, blocks=None,
                                  restored_from_revision_id=revision_id, restored_blocks=revision.blocks)

    async def _change(self, document_id, *, base_revision_id, markdown, client_mutation_id,
                      blocks, restored_from_revision_id, restored_blocks=None):
        if not client_mutation_id or not base_revision_id:
            raise DocumentValidation("base_revision_id and client_mutation_id required")
        normalized = ([{"id": b.id, "markdown": b.markdown} if isinstance(b, Block) else b
                       for b in blocks] if blocks is not None else None)
        digest = _digest(dict(base_revision_id=base_revision_id, markdown=markdown,
                              blocks=normalized, restored_from_revision_id=restored_from_revision_id))
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                replay = await self._replay(db, document_id, client_mutation_id, digest)
                if replay:
                    await db.rollback()
                    return replay
                current = await self._document(db, document_id)
                if current.current_revision_id != base_revision_id:
                    raise DocumentConflict(current_revision_id=current.current_revision_id)
                parsed = restored_blocks if restored_blocks is not None else _blocks(
                    markdown, normalized, current.revision.blocks)
                revision_id, stamp = _id(), _now()
                await db.execute("INSERT INTO script_revisions VALUES (?,?,?,?,?,?,?,?)",
                                 (revision_id, document_id, base_revision_id, markdown,
                                  json.dumps(_as_json_blocks(parsed), ensure_ascii=False),
                                  client_mutation_id, stamp, restored_from_revision_id))
                await db.execute("UPDATE script_documents SET current_revision_id=?,updated_at=? WHERE id=?",
                                 (revision_id, stamp, document_id))
                await db.execute("INSERT INTO script_mutations VALUES (?,?,?,?,?)",
                                 (document_id, client_mutation_id, digest, document_id, revision_id))
                response = await self._document(db, document_id)
                await db.commit()
                return response
            except Exception:
                await db.rollback()
                raise

    async def find_import(self, episode_number):
        async with self._db() as db:
            row = await (await db.execute(
                "SELECT id FROM script_documents WHERE json_extract(source_origin, '$.source_episode_number')=?",
                (episode_number,))).fetchone()
            return await self._document(db, row["id"]) if row else None
