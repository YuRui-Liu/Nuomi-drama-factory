"""Explicit, revision-bound narrative identities and stable asset links.

No names are used to infer relationships. All writes share the project DB transaction.
"""
from __future__ import annotations

import json
import re
import sqlite3

from novelvideo.models import NovelCharacter

from .store import DocumentConflict, DocumentNotFound, DocumentValidation, _digest, _id

KINDS = {"people": "character", "scenes": "scene", "props": "prop"}
TABLES = {"character": "characters", "scene": "scenes", "prop": "props"}


def _design_prompt(asset_type: str, description: str) -> str:
    """Copy only explicit single-line visual fields from the design contract.

    Narrative roles, planned appearances, ownership and continuity may describe
    future events rather than a drawable asset. Keep those in the source text.
    Unlabeled prose and continuation lines remain there too; do not infer facts.
    """
    labels = {
        "scene": {"空间布局", "视觉/光线", "关键物件"},
        "prop": {"外观材质"},
    }[asset_type]
    fields = []
    for line in description.splitlines():
        line = re.sub(r"^\s*(?:[-*+]\s+)?", "", line).replace("**", "")
        parts = re.split(r"[：:]", line, maxsplit=1)
        if len(parts) != 2:
            continue
        label, value = parts
        if label.strip() in labels and value.strip():
            fields.append(f"{label.strip()}：{value.strip()}")
    return "\n".join(fields)


class EntityService:
    def __init__(self, store):
        self.store = store

    async def initialize(self):
        async with self.store._db() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS script_entities (
                    entity_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    block_id TEXT NOT NULL, asset_type TEXT NOT NULL, name TEXT NOT NULL,
                    confirmed_revision TEXT NOT NULL, asset_id TEXT, selected_revision TEXT,
                    relations TEXT NOT NULL, appearances TEXT NOT NULL,
                    UNIQUE(document_id,block_id));
                CREATE TABLE IF NOT EXISTS script_entity_mutations (
                    mutation_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result TEXT NOT NULL);
            """)
            await db.commit()

    @staticmethod
    async def snapshot(db, document_id=None):
        """Read on caller's connection; safe inside a handoff BEGIN IMMEDIATE.

        Returns immutable IDs and exact current asset records, with missing/stale flags.
        Callers must reject flags when freezing a production handoff.
        """
        query = "SELECT * FROM script_entities"
        args = ()
        if document_id is not None:
            query += " WHERE document_id=?"
            args = (document_id,)
        rows = await (await db.execute(query + " ORDER BY rowid", args)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["relations"] = json.loads(item["relations"])
            item["appearances"] = json.loads(item["appearances"])
            doc = await (await db.execute("SELECT current_revision_id FROM script_documents WHERE id=?", (item["document_id"],))).fetchone()
            revision = await (await db.execute("SELECT blocks FROM script_revisions WHERE id=?", (doc[0],))).fetchone() if doc else None
            blocks = json.loads(revision[0]) if revision else []
            item["entry_missing"] = not any(b["id"] == item["block_id"] for b in blocks)
            item["stale"] = not doc or doc[0] != item["confirmed_revision"] or bool(item["asset_id"] and doc[0] != item["selected_revision"])
            item["asset_name"] = None
            item["asset_record"] = None
            item["asset_missing"] = False
            if item["asset_id"]:
                registry = await (await db.execute("SELECT current_name FROM asset_registry WHERE asset_uuid=? AND kind=? AND deleted_at IS NULL", (item["asset_id"], item["asset_type"]))).fetchone()
                if registry:
                    record = await (await db.execute(f"SELECT * FROM {TABLES[item['asset_type']]} WHERE name=?", (registry[0],))).fetchone()
                    if record:
                        item["asset_name"] = registry[0]
                        item["asset_record"] = dict(record)
                item["asset_missing"] = item["asset_record"] is None
            for appearance in item["appearances"]:
                if appearance["status"] == "written":
                    current = await (await db.execute("SELECT current_revision_id FROM script_documents WHERE id=?", (appearance["document_id"],))).fetchone()
                    appearance["stale"] = not current or current[0] != appearance["revision_id"]
            result.append(item)
        # Cross-document relations are checked against the same project, not the filtered list.
        for item in result:
            for relation in item["relations"]:
                target = await (await db.execute("SELECT document_id,block_id,confirmed_revision,asset_id,selected_revision FROM script_entities WHERE entity_id=?", (relation["entity_id"],))).fetchone()
                relation["missing"] = target is None
                relation["stale"] = False
                if target:
                    current = await (await db.execute("SELECT current_revision_id FROM script_documents WHERE id=?", (target[0],))).fetchone()
                    relation["stale"] = not current or current[0] != target[2] or bool(target[3] and current[0] != target[4])
                    revision = await (await db.execute("SELECT blocks FROM script_revisions WHERE id=?", (current[0],))).fetchone() if current else None
                    relation["missing"] = not revision or not any(b["id"] == target[1] for b in json.loads(revision[0]))
        return result

    async def list(self, document_id=None):
        async with self.store._db() as db:
            return await self.snapshot(db, document_id)

    async def assets(self, asset_type):
        if asset_type not in TABLES:
            raise DocumentValidation("invalid asset type")
        async with self.store._db() as db:
            rows = await (await db.execute(f"""SELECT r.asset_uuid AS asset_id,r.kind AS asset_type,a.name,a.description
                FROM asset_registry r JOIN {TABLES[asset_type]} a ON a.name=r.current_name
                WHERE r.kind=? AND r.deleted_at IS NULL ORDER BY a.name""", (asset_type,))).fetchall()
            return [dict(row) for row in rows]

    async def put(self, *, document_id, base_revision_id, block_id, name, client_mutation_id,
                  entity_id=None, asset_id=None, create_text=None, relations=None, appearances=None):
        payload = dict(document_id=document_id, base_revision_id=base_revision_id, block_id=block_id,
            name=name, entity_id=entity_id, asset_id=asset_id, create_text=create_text,
            relations=relations, appearances=appearances)
        digest = _digest(payload)
        if not name.strip() or not client_mutation_id:
            raise DocumentValidation("name and mutation id are required")
        async with self.store._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                replay = await (await db.execute("SELECT payload_hash,result FROM script_entity_mutations WHERE mutation_id=?", (client_mutation_id,))).fetchone()
                if replay:
                    if replay[0] != digest:
                        raise DocumentConflict("mutation id reused with different payload")
                    return json.loads(replay[1])
                doc = await (await db.execute("SELECT kind,current_revision_id FROM script_documents WHERE id=?", (document_id,))).fetchone()
                if not doc:
                    raise DocumentNotFound("document not found")
                if doc[0] not in KINDS:
                    raise DocumentValidation("entities require a people, scenes or props document")
                if doc[1] != base_revision_id:
                    raise DocumentConflict("save and reload the current document first", doc[1])
                revision = await (await db.execute("SELECT blocks FROM script_revisions WHERE id=?", (base_revision_id,))).fetchone()
                if not any(b["id"] == block_id for b in json.loads(revision[0])):
                    raise DocumentValidation("entry block is missing; explicitly select an existing entry")
                existing = None
                if entity_id:
                    existing = await (await db.execute("SELECT * FROM script_entities WHERE entity_id=? AND document_id=?", (entity_id, document_id))).fetchone()
                    if not existing:
                        raise DocumentNotFound("entity not found in this document")
                    if existing["block_id"] != block_id:
                        raise DocumentValidation("an entity cannot be reassigned to another block")
                else:
                    entity_id = _id()
                asset_type = KINDS[doc[0]]
                relations = json.loads(existing["relations"]) if relations is None and existing else (relations or [])
                appearances = json.loads(existing["appearances"]) if appearances is None and existing else (appearances or [])
                previous_relations = {
                    (item["kind"], item["entity_id"]) for item in json.loads(existing["relations"])
                } if existing else set()
                for relation in relations:
                    target = await (await db.execute("SELECT asset_type,document_id,block_id FROM script_entities WHERE entity_id=?", (relation.get("entity_id"),))).fetchone()
                    allowed = {"holding": ("character", "prop"), "key_prop": ("scene", "prop"), "entry": ("scene", "character")}
                    if not target or allowed.get(relation.get("kind")) != (asset_type, target[0]):
                        raise DocumentValidation("relation requires a compatible stable entity in this project")
                    if (relation["kind"], relation["entity_id"]) not in previous_relations:
                        target_revision = await (await db.execute("""SELECT r.blocks FROM script_documents d
                            JOIN script_revisions r ON r.id=d.current_revision_id WHERE d.id=?""", (target[1],))).fetchone()
                        if not target_revision or not any(b["id"] == target[2] for b in json.loads(target_revision[0])):
                            raise DocumentValidation("cannot add a relation to a missing entry")
                for appearance in appearances:
                    if appearance.get("kind") not in ("first_appearance", "critical_scene"):
                        raise DocumentValidation("invalid appearance kind")
                    if appearance.get("status") == "planned":
                        if not isinstance(appearance.get("episode_number"), int) or appearance["episode_number"] < 1:
                            raise DocumentValidation("planned appearance requires episode number")
                    elif appearance.get("status") == "written":
                        episode = await (await db.execute("SELECT kind,current_revision_id FROM script_documents WHERE id=?", (appearance.get("document_id"),))).fetchone()
                        if not episode or episode[0] != "episode_script" or episode[1] != appearance.get("revision_id"):
                            raise DocumentValidation("written appearance requires the actual current episode script revision")
                    else:
                        raise DocumentValidation("invalid appearance status")
                if create_text:
                    if asset_id or not create_text.get("name", "").strip():
                        raise DocumentValidation("choose an existing asset or create a named text asset")
                    asset_name = create_text["name"].strip()
                    description = create_text.get("description", "")
                    if asset_type == "character":
                        # Keep the same name contract as the asset center, without
                        # inventing age facts from the legacy schema default.
                        asset_name = NovelCharacter(name=asset_name, age_group="").name
                        await db.execute("INSERT INTO characters(name,description,age_group) VALUES (?,?,?)", (asset_name, description, ""))
                    else:
                        prompt_field = "environment_prompt" if asset_type == "scene" else "visual_prompt"
                        await db.execute(
                            f"INSERT INTO {TABLES[asset_type]}(name,description,{prompt_field}) VALUES (?,?,?)",
                            (asset_name, description, _design_prompt(asset_type, description)),
                        )
                    asset_id = (await (await db.execute("SELECT asset_uuid FROM asset_registry WHERE kind=? AND current_name=? AND deleted_at IS NULL", (asset_type, asset_name))).fetchone())[0]
                if asset_id:
                    target = await (await db.execute(f"""SELECT r.asset_uuid FROM asset_registry r JOIN {TABLES[asset_type]} a ON a.name=r.current_name
                        WHERE r.asset_uuid=? AND r.kind=? AND r.deleted_at IS NULL""", (asset_id, asset_type))).fetchone()
                    if not target:
                        raise DocumentValidation("asset missing, wrong type or outside this project")
                selected_revision = base_revision_id if asset_id else (existing["selected_revision"] if existing else None)
                if asset_id is None and existing:
                    asset_id = existing["asset_id"]
                await db.execute("""INSERT INTO script_entities VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name,confirmed_revision=excluded.confirmed_revision,
                    asset_id=excluded.asset_id,selected_revision=excluded.selected_revision,
                    relations=excluded.relations,appearances=excluded.appearances""",
                    (entity_id, document_id, block_id, asset_type, name.strip(), base_revision_id, asset_id, selected_revision,
                     json.dumps(relations, ensure_ascii=False), json.dumps(appearances, ensure_ascii=False)))
                result = next(item for item in await self.snapshot(db, document_id) if item["entity_id"] == entity_id)
                await db.execute("INSERT INTO script_entity_mutations VALUES (?,?,?)", (client_mutation_id, digest, json.dumps(result, ensure_ascii=False)))
                await db.commit()
                return result
            except sqlite3.IntegrityError as exc:
                await db.rollback()
                raise DocumentConflict("entry or asset name already exists; select it explicitly") from exc
            except BaseException:
                await db.rollback()
                raise
