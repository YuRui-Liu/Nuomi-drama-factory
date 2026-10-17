"""Project-local immutable document revisions."""
from __future__ import annotations

import hashlib
from collections import Counter
from difflib import SequenceMatcher
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiosqlite

from .models import Block, Document, KINDS, Revision, SceneIdentity


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


def _scene_specs(markdown, blocks):
    """Return semantic scene headers and their stable paragraph anchors."""
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document

    parsed = parse_screenplay_document(markdown)
    if not parsed.scenes:
        return []
    lines = markdown.splitlines(keepends=True)
    line_starts = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line))
    block_positions = []
    block_start = 0
    for block in blocks:
        block_end = block_start + len(block.markdown)
        block_positions.append((block.id, block_start, block_end))
        block_start = block_end
    specs = []
    for scene in parsed.scenes:
        start_line = scene.source_range.start_line - 1
        end_line = scene.source_range.end_line
        start = line_starts[start_line] if start_line < len(line_starts) else len(markdown)
        end = line_starts[end_line] if end_line < len(line_starts) else len(markdown)
        anchors = tuple(block_id for block_id, block_start, block_end in block_positions
                        if block_start < end and block_end > start)
        specs.append((scene.heading, anchors))
    return specs


def _scene_identity_records(document_id, markdown, blocks, prior=(), update=None,
                            known_parent_ids=None):
    specs = _scene_specs(markdown, blocks)
    if update is not None:
        if not isinstance(update, list) or len(update) != len(specs):
            raise DocumentValidation("scene identity update must match the parsed scenes")
        prior_by_id = {item.id: item for item in prior}
        known_ids = set(prior_by_id) | set(known_parent_ids or ())
        seen_ids = set()
        records = []
        for ordinal, (item, spec) in enumerate(zip(update, specs), start=1):
            if not isinstance(item, dict):
                raise DocumentValidation("invalid scene identity")
            identity = str(item.get("id") or "").strip()
            try:
                item_ordinal = int(item.get("ordinal"))
            except (TypeError, ValueError):
                item_ordinal = 0
            heading = str(item.get("heading") or "")
            raw_parents = item.get("parent_ids", [])
            if (not identity or len(identity) > 128 or identity in seen_ids or item_ordinal != ordinal
                    or heading != spec[0] or not isinstance(raw_parents, list)):
                raise DocumentValidation("scene identity does not match screenplay order")
            parents = tuple(dict.fromkeys(str(value).strip() for value in raw_parents))
            if any(not value or value not in known_ids for value in parents):
                raise DocumentValidation("scene lineage must reference a known prior scene identity")
            if identity in parents:
                raise DocumentValidation("scene lineage cannot reference itself")
            if identity in prior_by_id and parents != prior_by_id[identity].parent_ids:
                raise DocumentValidation("retained scene lineage is immutable")
            if identity in known_ids and identity not in prior_by_id:
                raise DocumentValidation("a structural edit cannot reuse a retired scene identity")
            if identity not in prior_by_id and prior and not parents:
                raise DocumentValidation("new scenes must reference their source scene")
            seen_ids.add(identity)
            records.append(SceneIdentity(identity, ordinal, heading, parents, spec[1]))
        return tuple(records)

    # Reorder and ordinary edits retain identities through the document block
    # anchors. Ambiguous split/merge inheritance is handled only by an explicit
    # structure operation, never by a text-similarity guess.
    used = set()
    records = []
    for ordinal, (heading, anchors) in enumerate(specs, start=1):
        candidates = []
        anchor_set = set(anchors)
        for old in prior:
            if old.id in used:
                continue
            overlap = len(anchor_set.intersection(old.anchor_block_ids))
            if overlap:
                candidates.append((overlap, old))
        candidates.sort(key=lambda item: item[0], reverse=True)
        retained = (candidates[0][1] if candidates and
                    (len(candidates) == 1 or candidates[0][0] > candidates[1][0]) else None)
        if retained is not None:
            used.add(retained.id)
            identity = retained.id
            parents = retained.parent_ids
        else:
            anchor = anchors[0] if anchors else _id()
            identity = "scene-" + hashlib.sha256(
                f"{document_id}\\0{anchor}".encode("utf-8")).hexdigest()[:24]
            if identity in used:
                identity = "scene-" + _id()
            parents = ()
            used.add(identity)
        records.append(SceneIdentity(identity, ordinal, heading, parents, anchors))
    return tuple(records)


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
        # Text-only edits cannot disambiguate identical entries. Never let the
        # matcher's arbitrary duplicate choice transfer an entity's identity.
        if previous_text == current_text:
            return tuple(Block(old.id, part) for old, part in zip(previous, parts))
        old_counts, new_counts = Counter(previous_text), Counter(current_text)
        unique_old = {text: previous[index].id for index, text in enumerate(previous_text)
                      if old_counts[text] == 1}
        matched = {index: unique_old[text] for index, text in enumerate(current_text)
                   if new_counts[text] == 1 and text in unique_old}
        matcher = SequenceMatcher(None, previous_text, current_text, autojunk=False)
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            # Only a single isolated, unique replacement is an unambiguous
            # rename/edit. Cardinality changes and multi-entry rewrites get IDs
            # of their own; explicit block edits can retain known identities.
            if tag == "replace" and old_end - old_start == new_end - new_start == 1:
                old_text, new_text = previous_text[old_start], current_text[new_start]
                if (old_counts[old_text] == new_counts[new_text] == 1
                        and new_counts[old_text] == old_counts[new_text] == 0):
                    matched[new_start] = previous[old_start].id
        return tuple(Block(matched.get(index) or _id(), part) for index, part in enumerate(parts))
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
                CREATE TABLE IF NOT EXISTS script_craft_memory (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL,
                    data TEXT NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0,
                    dismissal_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_craft_memory_kind ON script_craft_memory(kind,updated_at);
                CREATE TABLE IF NOT EXISTS script_craft_contexts (
                    owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, step_key TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(owner_type,owner_id,step_key));
                CREATE TABLE IF NOT EXISTS script_craft_records (
                    owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, step_key TEXT NOT NULL,
                    stage TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(owner_type, owner_id, step_key, stage));
                CREATE TABLE IF NOT EXISTS script_craft_dependencies (
                    candidate_id TEXT NOT NULL, document_id TEXT NOT NULL,
                    source_candidate_id TEXT NOT NULL, content_hash TEXT NOT NULL,
                    PRIMARY KEY(candidate_id, document_id));
                CREATE TABLE IF NOT EXISTS script_documents (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                    episode_number INTEGER, current_revision_id TEXT NOT NULL,
                    adopted_revision_id TEXT, source_origin TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS script_document_adoptions (
                    document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    mutation_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(document_id, mutation_id));
                CREATE TABLE IF NOT EXISTS script_revisions (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    parent_revision_id TEXT, markdown TEXT NOT NULL, blocks TEXT NOT NULL,
                    client_mutation_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    restored_from_revision_id TEXT);
                CREATE INDEX IF NOT EXISTS idx_script_revisions_doc ON script_revisions(document_id, created_at);
                CREATE TABLE IF NOT EXISTS script_scene_identities (
                    document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    scene_id TEXT NOT NULL, ordinal INTEGER NOT NULL, heading TEXT NOT NULL,
                    parent_ids TEXT NOT NULL, anchor_block_ids TEXT NOT NULL,
                    PRIMARY KEY(document_id, revision_id, scene_id),
                    UNIQUE(document_id, revision_id, ordinal));
                CREATE TABLE IF NOT EXISTS script_generation_runs (
                    id TEXT PRIMARY KEY, mutation_id TEXT UNIQUE NOT NULL,
                    request_hash TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS script_plan_confirmation_runs (
                    plan_run_id TEXT NOT NULL, revisions_hash TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(plan_run_id, revisions_hash));
                CREATE TABLE IF NOT EXISTS script_screenplay_reviews (
                    document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    plan_run_id TEXT NOT NULL, confirmed_revisions TEXT NOT NULL,
                    status TEXT NOT NULL, task_id TEXT, result TEXT,
                    mutation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                    attempt_id TEXT NOT NULL, attempt_number INTEGER NOT NULL DEFAULT 1,
                    enqueue_claimed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(document_id, revision_id), UNIQUE(mutation_id));
                CREATE TABLE IF NOT EXISTS script_screenplay_review_attempts (
                    document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL, attempt_number INTEGER NOT NULL,
                    plan_run_id TEXT NOT NULL, confirmed_revisions TEXT NOT NULL,
                    status TEXT NOT NULL, task_id TEXT, result TEXT,
                    mutation_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
                    enqueue_claimed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(document_id, revision_id, attempt_id),
                    UNIQUE(document_id, revision_id, attempt_number));
                CREATE INDEX IF NOT EXISTS idx_screenplay_review_attempts
                    ON script_screenplay_review_attempts(document_id,revision_id,attempt_number);
                CREATE TABLE IF NOT EXISTS script_screenplay_review_requests (
                    mutation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                    document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS script_screenplay_recoveries (
                    run_id TEXT NOT NULL, episode_number INTEGER NOT NULL,
                    draft_index INTEGER NOT NULL, step_key TEXT NOT NULL, stage TEXT NOT NULL,
                    document_id TEXT NOT NULL, revision_id TEXT NOT NULL, draft_hash TEXT NOT NULL,
                    plan_run_id TEXT NOT NULL, confirmed_revisions TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, episode_number), UNIQUE(document_id));
                CREATE TABLE IF NOT EXISTS script_screenplay_recovery_requests (
                    mutation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                    run_id TEXT NOT NULL, episode_number INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS script_generation_candidates (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_key TEXT NOT NULL,
                    target_document_id TEXT, target_revision_id TEXT,
                    markdown TEXT NOT NULL, context_revisions TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(run_id, step_key));
                CREATE TABLE IF NOT EXISTS script_consistency_runs (
                    id TEXT PRIMARY KEY, mutation_id TEXT UNIQUE NOT NULL,
                    request_hash TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS script_consistency_issues (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_script_consistency_issues_run ON script_consistency_issues(run_id);
                CREATE TABLE IF NOT EXISTS script_consistency_target_jobs (
                    issue_id TEXT NOT NULL, document_id TEXT NOT NULL, rewrite_job_id TEXT NOT NULL,
                    selected_at TEXT NOT NULL, PRIMARY KEY(issue_id,document_id));
                CREATE TABLE IF NOT EXISTS script_rewrite_jobs (
                    id TEXT PRIMARY KEY, mutation_id TEXT UNIQUE NOT NULL,
                    request_hash TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS script_proposals (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    base_revision_id TEXT NOT NULL, block_id TEXT,
                    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
                    before_text TEXT NOT NULL, after_text TEXT NOT NULL,
                    reason TEXT NOT NULL, dependencies TEXT NOT NULL,
                    context_revisions TEXT NOT NULL, round_id TEXT NOT NULL,
                    status TEXT NOT NULL, source_candidate_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(document_id, round_id, id));
                CREATE INDEX IF NOT EXISTS idx_script_proposals_doc ON script_proposals(document_id, created_at);
                CREATE TABLE IF NOT EXISTS script_proposal_adoptions (
                    proposal_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS script_mutations (
                    scope TEXT NOT NULL, key TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    PRIMARY KEY(scope,key));
                CREATE UNIQUE INDEX IF NOT EXISTS idx_script_source_origin ON script_documents(
                    json_extract(source_origin, '$.source_episode_number'))
                    WHERE source_origin IS NOT NULL;
            """)
            columns = await (await db.execute(
                "PRAGMA table_info(script_screenplay_reviews)")).fetchall()
            column_names = {row["name"] for row in columns}
            for name, declaration in (
                ("attempt_id", "TEXT NOT NULL DEFAULT ''"),
                ("attempt_number", "INTEGER NOT NULL DEFAULT 1"),
                ("enqueue_claimed", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in column_names:
                    await db.execute(
                        f"ALTER TABLE script_screenplay_reviews ADD COLUMN {name} {declaration}")
            await db.execute(
                "UPDATE script_screenplay_reviews SET attempt_id=mutation_id "
                "WHERE attempt_id IS NULL OR attempt_id='' ")
            await db.execute("""
                INSERT OR IGNORE INTO script_screenplay_review_attempts
                    (document_id,revision_id,attempt_id,attempt_number,plan_run_id,confirmed_revisions,
                     status,task_id,result,mutation_id,request_hash,enqueue_claimed,created_at,updated_at)
                SELECT document_id,revision_id,attempt_id,attempt_number,plan_run_id,confirmed_revisions,
                       status,task_id,result,mutation_id,request_hash,enqueue_claimed,created_at,updated_at
                FROM script_screenplay_reviews
            """)
            await db.execute("""
                INSERT OR IGNORE INTO script_screenplay_review_requests
                    (mutation_id,request_hash,document_id,revision_id,attempt_id,created_at)
                SELECT mutation_id,request_hash,document_id,revision_id,attempt_id,created_at
                FROM script_screenplay_reviews
            """)
            await db.commit()

    async def _revision(self, db, revision_id):
        row = await (await db.execute("SELECT * FROM script_revisions WHERE id=?", (revision_id,))).fetchone()
        if row is None:
            raise DocumentNotFound("revision not found")
        blocks = tuple(Block(**item) for item in json.loads(row["blocks"]))
        document = await (await db.execute(
            "SELECT kind FROM script_documents WHERE id=?", (row["document_id"],)
        )).fetchone()
        scene_identities = ()
        if document and document["kind"] == "episode_script":
            identity_rows = await (await db.execute(
                "SELECT * FROM script_scene_identities WHERE document_id=? AND revision_id=? ORDER BY ordinal",
                (row["document_id"], revision_id),
            )).fetchall()
            if identity_rows:
                scene_identities = tuple(SceneIdentity(
                    item["scene_id"], item["ordinal"], item["heading"],
                    tuple(json.loads(item["parent_ids"])), tuple(json.loads(item["anchor_block_ids"])),
                ) for item in identity_rows)
            else:
                parent = (await self._revision(db, row["parent_revision_id"])
                          if row["parent_revision_id"] else None)
                scene_identities = _scene_identity_records(
                    row["document_id"], row["markdown"], blocks,
                    parent.scene_identities if parent else (),
                )
        return Revision(row["id"], row["document_id"], row["parent_revision_id"], row["markdown"],
                        blocks, row["client_mutation_id"], row["created_at"],
                        row["restored_from_revision_id"], scene_identities)

    @staticmethod
    async def _persist_scene_identities(db, document_id, revision_id, identities):
        if not identities:
            return
        await db.executemany(
            "INSERT INTO script_scene_identities "
            "(document_id,revision_id,scene_id,ordinal,heading,parent_ids,anchor_block_ids) "
            "VALUES (?,?,?,?,?,?,?)",
            [(document_id, revision_id, item.id, item.ordinal, item.heading,
              json.dumps(item.parent_ids, ensure_ascii=False),
              json.dumps(item.anchor_block_ids, ensure_ascii=False)) for item in identities],
        )

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

    async def get_revision(self, document_id, revision_id):
        async with self._db() as db:
            document = await self._document(db, document_id, revision_id)
            if document.revision.document_id != document_id:
                raise DocumentNotFound("revision not found")
            return document

    async def adopt_revision(self, document_id, *, revision_id, client_mutation_id,
                             require_current=False, dependency_revisions=None):
        if not client_mutation_id:
            raise DocumentValidation("client_mutation_id required")
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                document = await self._document(db, document_id)
                revision = await self._revision(db, revision_id)
                if revision.document_id != document_id:
                    raise DocumentNotFound("revision not found")
                existing = await (await db.execute(
                    "SELECT revision_id FROM script_document_adoptions WHERE document_id=? AND mutation_id=?",
                    (document_id, client_mutation_id))).fetchone()
                if existing:
                    if existing["revision_id"] != revision_id:
                        raise DocumentConflict("adoption mutation id reused with different revision",
                                               current_revision_id=document.adopted_revision_id)
                    await db.rollback()
                    return document
                if require_current and document.current_revision_id != revision_id:
                    raise DocumentConflict("screenplay revision is no longer current",
                                           current_revision_id=document.current_revision_id)
                for dependency_id, dependency_revision in (dependency_revisions or {}).items():
                    if dependency_id == document_id:
                        continue
                    dependency = await (await db.execute(
                        "SELECT current_revision_id FROM script_documents WHERE id=?", (dependency_id,))).fetchone()
                    if dependency is None or dependency["current_revision_id"] != dependency_revision:
                        raise DocumentConflict("confirmed planning dependency changed",
                                               current_revision_id=dependency["current_revision_id"] if dependency else None)
                stamp = _now()
                await db.execute("INSERT INTO script_document_adoptions VALUES (?,?,?,?)",
                                 (document_id, revision_id, client_mutation_id, stamp))
                await db.execute("UPDATE script_documents SET adopted_revision_id=? WHERE id=?",
                                 (revision_id, document_id))
                adopted = await self._document(db, document_id)
                await db.commit()
                return adopted
            except Exception:
                await db.rollback()
                raise

    async def proposal_adoption_for_candidate(self, candidate_id):
        async with self._db() as db:
            row = await (await db.execute(
                "SELECT p.id AS proposal_id,p.document_id,p.status,a.revision_id "
                "FROM script_proposals p JOIN script_proposal_adoptions a ON a.proposal_id=p.id "
                "WHERE p.source_candidate_id=? AND p.status='adopted' "
                "ORDER BY p.created_at DESC LIMIT 1", (candidate_id,))).fetchone()
            return dict(row) if row else None

    async def revision_descends_from(self, document_id, ancestor_revision_id, revision_id):
        async with self._db() as db:
            return await self._revision_descends_from(db, document_id, ancestor_revision_id, revision_id)

    async def create_screenplay_review(self, document_id, revision_id, *, plan_run_id, mutation_id):
        if not mutation_id:
            raise DocumentValidation("client_mutation_id required")
        request_hash = _digest({"document_id": document_id, "revision_id": revision_id,
                                "plan_run_id": plan_run_id})
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                request = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_requests WHERE mutation_id=?",
                    (mutation_id,),
                )).fetchone()
                if request:
                    if (request["request_hash"] != request_hash
                            or request["document_id"] != document_id
                            or request["revision_id"] != revision_id):
                        raise DocumentConflict("screenplay review mutation id reused with different request")
                    attempt = await (await db.execute(
                        "SELECT * FROM script_screenplay_review_attempts "
                        "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                        (document_id, revision_id, request["attempt_id"]),
                    )).fetchone()
                    await db.rollback()
                    return self._screenplay_review_row(attempt)
                document = await (await db.execute(
                    "SELECT kind,current_revision_id FROM script_documents WHERE id=?", (document_id,))).fetchone()
                if document is None:
                    raise DocumentNotFound("document not found")
                if document["kind"] != "episode_script":
                    raise DocumentValidation("screenplay document required")
                if document["current_revision_id"] != revision_id:
                    raise DocumentConflict("screenplay revision changed", document["current_revision_id"])
                revision = await (await db.execute(
                    "SELECT document_id FROM script_revisions WHERE id=?", (revision_id,))).fetchone()
                if revision is None or revision["document_id"] != document_id:
                    raise DocumentNotFound("revision not found")
                plan_row = await (await db.execute(
                    "SELECT data FROM script_generation_runs WHERE id=?", (plan_run_id,))).fetchone()
                if plan_row is None:
                    raise DocumentNotFound("planning run not found")
                plan = json.loads(plan_row["data"])
                if (plan.get("mode") != "planning" or plan.get("status") != "completed"
                        or plan.get("phase") != "confirmed" or not plan.get("confirmed_revisions")):
                    raise DocumentValidation("a confirmed planning run is required to review a screenplay")
                screenplay_run_id = plan.get("screenplay_run_id")
                if (not screenplay_run_id
                        or screenplay_run_id not in set((plan.get("confirmations") or {}).values())):
                    raise DocumentValidation("confirmed screenplay run is required")
                screenplay_row = await (await db.execute(
                    "SELECT data FROM script_generation_runs WHERE id=?", (screenplay_run_id,))).fetchone()
                screenplay_run = json.loads(screenplay_row["data"]) if screenplay_row else {}
                if (screenplay_run.get("mode") != "screenplay"
                        or screenplay_run.get("plan_run_id") != plan_run_id):
                    raise DocumentValidation("confirmed screenplay run is invalid")
                for dependency_id, dependency_revision in plan["confirmed_revisions"].items():
                    if dependency_id == (plan.get("source_idea") or {}).get("candidate_id"):
                        source = await (await db.execute(
                            "SELECT document_id FROM script_revisions WHERE id=?", (dependency_revision,))).fetchone()
                        if source is None or source["document_id"] != dependency_id:
                            raise DocumentConflict("confirmed idea revision is missing")
                        continue
                    current = await (await db.execute(
                        "SELECT current_revision_id FROM script_documents WHERE id=?", (dependency_id,))).fetchone()
                    if current is None or current["current_revision_id"] != dependency_revision:
                        raise DocumentConflict("confirmed planning dependency changed",
                                               current_revision_id=current["current_revision_id"] if current else None)
                existing = await (await db.execute(
                    "SELECT * FROM script_screenplay_reviews WHERE document_id=? AND revision_id=?",
                    (document_id, revision_id))).fetchone()
                if existing:
                    if existing["plan_run_id"] != plan_run_id:
                        raise DocumentConflict("screenplay revision already has a review for another plan")
                    if existing["status"] != "failed":
                        attempt_id = existing["attempt_id"]
                        await db.execute(
                            "INSERT INTO script_screenplay_review_requests "
                            "(mutation_id,request_hash,document_id,revision_id,attempt_id,created_at) "
                            "VALUES (?,?,?,?,?,?)",
                            (mutation_id, request_hash, document_id, revision_id, attempt_id, _now()))
                        attempt = await (await db.execute(
                            "SELECT * FROM script_screenplay_review_attempts "
                            "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                            (document_id, revision_id, attempt_id))).fetchone()
                        await db.commit()
                        return self._screenplay_review_row(attempt)
                    attempt_number = int(existing["attempt_number"]) + 1
                else:
                    attempt_number = 1
                attempt_id = _id()
                stamp = _now()
                confirmed_json = json.dumps(plan["confirmed_revisions"], ensure_ascii=False)
                if existing:
                    await db.execute(
                        "INSERT INTO script_screenplay_review_attempts "
                        "(document_id,revision_id,attempt_id,attempt_number,plan_run_id,confirmed_revisions,"
                        "status,task_id,result,mutation_id,request_hash,enqueue_claimed,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,'pending',NULL,NULL,?,?,0,?,?)",
                        (document_id, revision_id, attempt_id, attempt_number, plan_run_id,
                         confirmed_json, mutation_id, request_hash, stamp, stamp))
                    await db.execute(
                        "UPDATE script_screenplay_reviews SET status='pending',task_id=NULL,result=NULL,"
                        "confirmed_revisions=?,mutation_id=?,request_hash=?,attempt_id=?,attempt_number=?,"
                        "enqueue_claimed=0,updated_at=? "
                        "WHERE document_id=? AND revision_id=?",
                        (confirmed_json, mutation_id, request_hash, attempt_id, attempt_number,
                         stamp, document_id, revision_id))
                else:
                    await db.execute(
                        "INSERT INTO script_screenplay_reviews "
                        "(document_id,revision_id,plan_run_id,confirmed_revisions,status,task_id,result,"
                        "mutation_id,request_hash,attempt_id,attempt_number,enqueue_claimed,created_at,updated_at) "
                        "VALUES (?,?,?,?,'pending',NULL,NULL,?,?,?, ?,0,?,?)",
                        (document_id, revision_id, plan_run_id, confirmed_json, mutation_id,
                         request_hash, attempt_id, attempt_number, stamp, stamp))
                    await db.execute(
                        "INSERT INTO script_screenplay_review_attempts "
                        "(document_id,revision_id,attempt_id,attempt_number,plan_run_id,confirmed_revisions,"
                        "status,task_id,result,mutation_id,request_hash,enqueue_claimed,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,'pending',NULL,NULL,?,?,0,?,?)",
                        (document_id, revision_id, attempt_id, attempt_number, plan_run_id,
                         confirmed_json, mutation_id, request_hash, stamp, stamp))
                await db.execute(
                    "INSERT INTO script_screenplay_review_requests "
                    "(mutation_id,request_hash,document_id,revision_id,attempt_id,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (mutation_id, request_hash, document_id, revision_id, attempt_id, stamp))
                row = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id))).fetchone()
                await db.commit()
                return self._screenplay_review_row(row)
            except Exception:
                await db.rollback()
                raise

    @staticmethod
    def _screenplay_review_row(row):
        if row is None:
            return None
        result = json.loads(row["result"]) if row["result"] else None
        return {"document_id": row["document_id"], "revision_id": row["revision_id"],
                "plan_run_id": row["plan_run_id"],
                "confirmed_revisions": json.loads(row["confirmed_revisions"]),
                "status": row["status"], "task_id": row["task_id"],
                "result": result, "error": result.get("error") if isinstance(result, dict) else None,
                "mutation_id": row["mutation_id"],
                "attempt_id": row["attempt_id"], "attempt_number": row["attempt_number"],
                "enqueue_claimed": bool(row["enqueue_claimed"])}

    async def get_screenplay_review(self, document_id, revision_id):
        async with self._db() as db:
            row = await (await db.execute(
                "SELECT * FROM script_screenplay_reviews WHERE document_id=? AND revision_id=?",
                (document_id, revision_id))).fetchone()
            review = self._screenplay_review_row(row)
            if review is None:
                return None
            attempts = await (await db.execute(
                "SELECT * FROM script_screenplay_review_attempts "
                "WHERE document_id=? AND revision_id=? ORDER BY attempt_number",
                (document_id, revision_id))).fetchall()
            return {**review, "attempts": [self._screenplay_review_row(item) for item in attempts]}

    async def get_screenplay_review_lineage(self, document_id, revision_id):
        """Return review attempts on the revision's actual same-document parent chain."""
        async with self._db() as db:
            revision = await (await db.execute(
                "SELECT document_id,parent_revision_id FROM script_revisions WHERE id=?", (revision_id,)
            )).fetchone()
            if revision is None or revision["document_id"] != document_id:
                raise DocumentNotFound("revision not found")
            cursor = revision["parent_revision_id"]
            visited = {revision_id}
            lineage = []
            while cursor and cursor not in visited:
                visited.add(cursor)
                ancestor = await (await db.execute(
                    "SELECT document_id,parent_revision_id,markdown FROM script_revisions WHERE id=?", (cursor,)
                )).fetchone()
                if ancestor is None or ancestor["document_id"] != document_id:
                    break
                attempts = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? ORDER BY attempt_number DESC",
                    (document_id, cursor),
                )).fetchall()
                lineage.append({
                    "revision_id": cursor,
                    "parent_revision_id": ancestor["parent_revision_id"],
                    "markdown": ancestor["markdown"],
                    "attempts": [self._screenplay_review_row(item) for item in attempts],
                })
                cursor = ancestor["parent_revision_id"]
            return lineage

    async def claim_screenplay_review_submission(self, document_id, revision_id, attempt_id):
        """Atomically grant one API request the right to enqueue a review attempt."""
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                current = await (await db.execute(
                    "SELECT attempt_id,status,task_id FROM script_screenplay_reviews "
                    "WHERE document_id=? AND revision_id=?", (document_id, revision_id))).fetchone()
                attempt = await (await db.execute(
                    "SELECT status,task_id,enqueue_claimed FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id))).fetchone()
                if current is None or attempt is None:
                    raise DocumentNotFound("screenplay review attempt not found")
                if (current["attempt_id"] != attempt_id or attempt["status"] != "pending"
                        or attempt["task_id"] is not None or attempt["enqueue_claimed"]):
                    await db.rollback()
                    return False
                stamp = _now()
                await db.execute(
                    "UPDATE script_screenplay_review_attempts SET enqueue_claimed=1,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (stamp, document_id, revision_id, attempt_id))
                await db.execute(
                    "UPDATE script_screenplay_reviews SET enqueue_claimed=1,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (stamp, document_id, revision_id, attempt_id))
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                raise

    async def mark_screenplay_review_submission_unknown(self, document_id, revision_id, attempt_id, error):
        """Freeze an ambiguously submitted attempt so retries cannot enqueue twice."""
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                current = await (await db.execute(
                    "SELECT attempt_id,status FROM script_screenplay_reviews "
                    "WHERE document_id=? AND revision_id=?", (document_id, revision_id),
                )).fetchone()
                attempt = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id),
                )).fetchone()
                if current is None or attempt is None:
                    raise DocumentNotFound("screenplay review attempt not found")
                if (current["attempt_id"] != attempt_id or attempt["status"] != "pending"
                        or attempt["task_id"] is not None):
                    await db.rollback()
                    return self._screenplay_review_row(attempt)
                stored = {"error": str(error), "submission_unknown": True}
                stamp = _now()
                await db.execute(
                    "UPDATE script_screenplay_review_attempts SET status='submission_unknown',result=?,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (json.dumps(stored, ensure_ascii=False), stamp, document_id, revision_id, attempt_id),
                )
                await db.execute(
                    "UPDATE script_screenplay_reviews SET status='submission_unknown',result=?,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (json.dumps(stored, ensure_ascii=False), stamp, document_id, revision_id, attempt_id),
                )
                await db.commit()
                fresh = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id),
                )).fetchone()
                return self._screenplay_review_row(fresh)
            except Exception:
                await db.rollback()
                raise

    async def fail_unstarted_screenplay_review(self, document_id, revision_id, attempt_id, task_id, error):
        """Turn a backend task that died before review execution into a retryable failure."""
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                current = await (await db.execute(
                    "SELECT attempt_id FROM script_screenplay_reviews WHERE document_id=? AND revision_id=?",
                    (document_id, revision_id),
                )).fetchone()
                attempt = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id),
                )).fetchone()
                if current is None or attempt is None:
                    raise DocumentNotFound("screenplay review attempt not found")
                if (current["attempt_id"] != attempt_id
                        or attempt["status"] not in {"pending", "submission_unknown"}
                        or attempt["task_id"] not in {None, task_id}):
                    await db.rollback()
                    return self._screenplay_review_row(attempt)
                stored = {"error": str(error), "task_status": "failed_before_review"}
                stamp = _now()
                await db.execute(
                    "UPDATE script_screenplay_review_attempts SET status='failed',task_id=?,result=?,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (task_id, json.dumps(stored, ensure_ascii=False), stamp,
                     document_id, revision_id, attempt_id),
                )
                await db.execute(
                    "UPDATE script_screenplay_reviews SET status='failed',task_id=?,result=?,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (task_id, json.dumps(stored, ensure_ascii=False), stamp,
                     document_id, revision_id, attempt_id),
                )
                await db.commit()
                fresh = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id),
                )).fetchone()
                return self._screenplay_review_row(fresh)
            except Exception:
                await db.rollback()
                raise

    async def record_screenplay_review_task(self, document_id, revision_id, task_id, attempt_id=None):
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute(
                    "SELECT * FROM script_screenplay_reviews WHERE document_id=? AND revision_id=?",
                    (document_id, revision_id))).fetchone()
                if row is None:
                    raise DocumentNotFound("screenplay review not found")
                attempt_id = attempt_id or row["attempt_id"]
                attempt = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id))).fetchone()
                if attempt is None:
                    raise DocumentNotFound("screenplay review attempt not found")
                if row["attempt_id"] != attempt_id:
                    await db.rollback()
                    return self._screenplay_review_row(attempt)
                if attempt["task_id"] not in {None, task_id}:
                    raise DocumentConflict("screenplay review already has another task")
                if attempt["status"] in {"pending", "running", "submission_unknown"}:
                    next_status = "pending" if attempt["status"] == "submission_unknown" else attempt["status"]
                    await db.execute(
                        "UPDATE script_screenplay_review_attempts SET task_id=?,status=?,updated_at=? "
                        "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                        (task_id, next_status, _now(), document_id, revision_id, attempt_id))
                    await db.execute(
                        "UPDATE script_screenplay_reviews SET task_id=?,status=?,updated_at=? "
                        "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                        (task_id, next_status, _now(), document_id, revision_id, attempt_id))
                await db.commit()
                fresh = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id))).fetchone()
                return self._screenplay_review_row(fresh)
            except Exception:
                await db.rollback()
                raise

    async def claim_screenplay_review(self, document_id, revision_id, task_id, attempt_id=None):
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute(
                    "SELECT * FROM script_screenplay_reviews WHERE document_id=? AND revision_id=?",
                    (document_id, revision_id))).fetchone()
                if row is None:
                    raise DocumentNotFound("screenplay review not found")
                attempt_id = attempt_id or row["attempt_id"]
                attempt = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id))).fetchone()
                if attempt is None:
                    raise DocumentNotFound("screenplay review attempt not found")
                if row["attempt_id"] != attempt_id:
                    raise DocumentConflict("screenplay review task superseded")
                review = self._screenplay_review_row(attempt)
                if review["status"] in {"passed", "needs_revision", "needs_rebase"}:
                    await db.rollback()
                    return review
                if review["status"] not in {"pending", "submission_unknown"} or review["task_id"] not in {None, task_id}:
                    raise DocumentConflict("screenplay review is already active or superseded")
                await db.execute(
                    "UPDATE script_screenplay_review_attempts SET status='running',task_id=?,enqueue_claimed=1,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (task_id, _now(), document_id, revision_id, attempt_id))
                await db.execute(
                    "UPDATE script_screenplay_reviews SET status='running',task_id=?,enqueue_claimed=1,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (task_id, _now(), document_id, revision_id, attempt_id))
                await db.commit()
                fresh = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id))).fetchone()
                return self._screenplay_review_row(fresh)
            except Exception:
                await db.rollback()
                raise

    async def complete_screenplay_review(self, document_id, revision_id, task_id, *, result,
                                         status, error=None, attempt_id=None):
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute(
                    "SELECT * FROM script_screenplay_reviews WHERE document_id=? AND revision_id=?",
                    (document_id, revision_id))).fetchone()
                if row is None:
                    raise DocumentNotFound("screenplay review not found")
                attempt_id = attempt_id or row["attempt_id"]
                attempt = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id))).fetchone()
                if attempt is None:
                    raise DocumentNotFound("screenplay review attempt not found")
                if (row["attempt_id"] != attempt_id or attempt["task_id"] != task_id
                        or attempt["status"] != "running"):
                    raise DocumentConflict("screenplay review task superseded")
                current = await (await db.execute(
                    "SELECT current_revision_id FROM script_documents WHERE id=?", (document_id,))).fetchone()
                if current is None or current["current_revision_id"] != revision_id:
                    status, error, result = "needs_rebase", "待审正文已变化，请对当前修订重新审核", None
                plan_row = await (await db.execute(
                    "SELECT data FROM script_generation_runs WHERE id=?", (row["plan_run_id"],))).fetchone()
                plan = json.loads(plan_row["data"]) if plan_row else {}
                dependencies = json.loads(row["confirmed_revisions"])
                source_candidate_id = (plan.get("source_idea") or {}).get("candidate_id")
                if (plan.get("mode") != "planning" or plan.get("status") != "completed"
                        or plan.get("phase") != "confirmed"
                        or plan.get("confirmed_revisions") != dependencies
                        or not plan.get("screenplay_run_id")
                        or plan.get("screenplay_run_id") not in set(
                            (plan.get("confirmations") or {}).values())):
                    status, error, result = "needs_rebase", "确认的规划版本已变化，请重新确认规划后再审核", None
                if status != "needs_rebase":
                    for dependency_id, dependency_revision in dependencies.items():
                        if dependency_id == source_candidate_id:
                            continue
                        dependency = await (await db.execute(
                            "SELECT current_revision_id FROM script_documents WHERE id=?", (dependency_id,))).fetchone()
                        if dependency is None or dependency["current_revision_id"] != dependency_revision:
                            status, error, result = "needs_rebase", "规划依赖已变化，请重新确认规划后再审核", None
                            break
                stored_result = result if result is not None else ({"error": error} if error else None)
                await db.execute(
                    "UPDATE script_screenplay_review_attempts SET status=?,result=?,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (status, json.dumps(stored_result, ensure_ascii=False) if stored_result is not None else None,
                     _now(), document_id, revision_id, attempt_id))
                await db.execute(
                    "UPDATE script_screenplay_reviews SET status=?,result=?,updated_at=? "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (status, json.dumps(stored_result, ensure_ascii=False) if stored_result is not None else None,
                     _now(), document_id, revision_id, attempt_id))
                await db.commit()
                fresh = await (await db.execute(
                    "SELECT * FROM script_screenplay_review_attempts "
                    "WHERE document_id=? AND revision_id=? AND attempt_id=?",
                    (document_id, revision_id, attempt_id))).fetchone()
                return self._screenplay_review_row(fresh)
            except Exception:
                await db.rollback()
                raise

    async def recover_screenplay_draft(self, run_id, *, draft_index, mutation_id):
        """Create one unadopted screenplay document from an immutable CraftJournal draft.

        The receipt is fixed per generation run and episode, so concurrent recovery
        requests cannot create two paid-output documents or select different drafts.
        """
        if not mutation_id or not mutation_id.strip():
            raise DocumentValidation("client_mutation_id required")
        if isinstance(draft_index, bool) or not isinstance(draft_index, int) or draft_index < 0:
            raise DocumentValidation("draft_index must be a non-negative integer")
        request_hash = _digest({"run_id": run_id, "draft_index": draft_index})
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                prior_request = await (await db.execute(
                    "SELECT * FROM script_screenplay_recovery_requests WHERE mutation_id=?",
                    (mutation_id,),
                )).fetchone()
                if prior_request:
                    if prior_request["request_hash"] != request_hash or prior_request["run_id"] != run_id:
                        raise DocumentConflict("screenplay recovery mutation id reused with a different request")
                    receipt = await (await db.execute(
                        "SELECT * FROM script_screenplay_recoveries WHERE run_id=? AND episode_number=?",
                        (run_id, prior_request["episode_number"]),
                    )).fetchone()
                    if receipt is None:
                        raise DocumentConflict("screenplay recovery receipt is missing")
                    document = await self._document(db, receipt["document_id"])
                    await db.commit()
                    return {"document": document, "draft_index": receipt["draft_index"], "run_id": run_id}

                run_row = await (await db.execute(
                    "SELECT data FROM script_generation_runs WHERE id=?", (run_id,),
                )).fetchone()
                if run_row is None:
                    raise DocumentNotFound("generation run not found")
                run = json.loads(run_row["data"])
                if run.get("mode") != "screenplay" or run.get("status") != "needs_review":
                    raise DocumentValidation("only a needs-review screenplay run can recover a draft")
                steps = run.get("steps") or []
                recoverable_steps = [
                    step for step in steps
                    if step.get("kind") == "episode_script"
                    and step.get("status") == "needs_review"
                    and step.get("episode_number") is not None
                    and step.get("key") == f"episode_script:{step.get('episode_number')}"
                ]
                if len(recoverable_steps) != 1:
                    raise DocumentValidation("screenplay run must have exactly one needs-review episode draft")
                step = recoverable_steps[0]
                episode_number = int(step["episode_number"])
                step_key = step["key"]

                existing_receipt = await (await db.execute(
                    "SELECT * FROM script_screenplay_recoveries WHERE run_id=? AND episode_number=?",
                    (run_id, episode_number),
                )).fetchone()
                if existing_receipt:
                    if existing_receipt["draft_index"] != draft_index:
                        raise DocumentConflict("a different screenplay draft was already recovered for this episode")
                    await db.execute(
                        "INSERT INTO script_screenplay_recovery_requests VALUES (?,?,?,?,?)",
                        (mutation_id, request_hash, run_id, episode_number, _now()),
                    )
                    document = await self._document(db, existing_receipt["document_id"])
                    await db.commit()
                    return {"document": document, "draft_index": existing_receipt["draft_index"], "run_id": run_id}

                plan_run_id = run.get("plan_run_id")
                dependencies = run.get("confirmed_revisions")
                if not plan_run_id or not isinstance(dependencies, dict) or not dependencies:
                    raise DocumentValidation("screenplay run has no confirmed planning dependencies")
                plan_row = await (await db.execute(
                    "SELECT data FROM script_generation_runs WHERE id=?", (plan_run_id,),
                )).fetchone()
                plan = json.loads(plan_row["data"]) if plan_row else {}
                if (plan.get("mode") != "planning" or plan.get("status") != "completed"
                        or plan.get("phase") != "confirmed"
                        or plan.get("confirmed_revisions") != dependencies
                        or run_id not in set((plan.get("confirmations") or {}).values())):
                    raise DocumentValidation("screenplay run is not backed by a confirmed plan")
                source_candidate_id = (plan.get("source_idea") or {}).get("candidate_id")
                for dependency_id, dependency_revision in dependencies.items():
                    if dependency_id == source_candidate_id:
                        source_revision = await (await db.execute(
                            "SELECT document_id FROM script_revisions WHERE id=?", (dependency_revision,),
                        )).fetchone()
                        if source_revision is None or source_revision["document_id"] != dependency_id:
                            raise DocumentConflict("confirmed idea revision is missing")
                        continue
                    dependency = await (await db.execute(
                        "SELECT current_revision_id FROM script_documents WHERE id=?", (dependency_id,),
                    )).fetchone()
                    if dependency is None or dependency["current_revision_id"] != dependency_revision:
                        raise DocumentConflict("confirmed planning dependency changed",
                                               current_revision_id=dependency["current_revision_id"] if dependency else None)

                rows = await (await db.execute(
                    "SELECT stage,data FROM script_craft_records WHERE owner_type='generation' "
                    "AND owner_id=? AND step_key=? ORDER BY rowid", (run_id, step_key),
                )).fetchall()
                drafts = []
                for record_row in rows:
                    record = json.loads(record_row["data"])
                    markdown = (record.get("output") or {}).get("markdown")
                    if isinstance(markdown, str) and markdown.strip():
                        drafts.append((record_row["stage"], markdown))
                if draft_index >= len(drafts):
                    raise DocumentValidation("draft_index is outside the stored screenplay drafts")
                stage, markdown = drafts[draft_index]
                conflicting_document = await (await db.execute(
                    "SELECT id,current_revision_id FROM script_documents "
                    "WHERE kind='episode_script' AND episode_number=? LIMIT 1", (episode_number,),
                )).fetchone()
                if conflicting_document:
                    raise DocumentConflict("an episode screenplay already exists; recovery will not replace it",
                                           current_revision_id=conflicting_document["current_revision_id"])

                draft_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
                document_id, revision_id, stamp = _id(), _id(), _now()
                origin = {
                    "workflow_run_id": run_id,
                    "plan_run_id": plan_run_id,
                    "confirmed_revisions": dependencies,
                    "recovered_from_craft": {"step_key": step_key, "stage": stage,
                                              "draft_index": draft_index, "draft_hash": draft_hash},
                }
                blocks = _blocks(markdown)
                await db.execute(
                    "INSERT INTO script_documents VALUES (?,?,?,?,?,?,?,?,?)",
                    (document_id, "episode_script", step.get("title") or f"第 {episode_number} 集",
                     episode_number, revision_id, None, json.dumps(origin, ensure_ascii=False), stamp, stamp),
                )
                await db.execute(
                    "INSERT INTO script_revisions VALUES (?,?,?,?,?,?,?,?)",
                    (revision_id, document_id, None, markdown,
                     json.dumps(_as_json_blocks(blocks), ensure_ascii=False),
                     f"screenplay-recovery:{run_id}:{episode_number}:{draft_index}", stamp, None),
                )
                await db.execute(
                    "INSERT INTO script_screenplay_recoveries "
                    "(run_id,episode_number,draft_index,step_key,stage,document_id,revision_id,draft_hash,"
                    "plan_run_id,confirmed_revisions,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, episode_number, draft_index, step_key, stage, document_id, revision_id,
                     draft_hash, plan_run_id, json.dumps(dependencies, ensure_ascii=False), stamp),
                )
                await db.execute(
                    "INSERT INTO script_screenplay_recovery_requests VALUES (?,?,?,?,?)",
                    (mutation_id, request_hash, run_id, episode_number, stamp),
                )
                document = await self._document(db, document_id)
                await db.commit()
                return {"document": document, "draft_index": draft_index, "run_id": run_id}
            except Exception:
                await db.rollback()
                raise

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
                if kind == "episode_script":
                    identities = _scene_identity_records(document_id, markdown, parsed)
                    await self._persist_scene_identities(db, document_id, revision_id, identities)
                await db.execute("INSERT INTO script_mutations VALUES (?,?,?,?,?)",
                                 ("create", client_mutation_id, digest, document_id, revision_id))
                response = await self._document(db, document_id)
                await db.commit()
                return response
            except Exception:
                await db.rollback()
                raise

    async def save(self, document_id, *, base_revision_id, markdown, client_mutation_id,
                   blocks=None, scene_identity_update=None):
        return await self._change(document_id, base_revision_id=base_revision_id, markdown=markdown,
                                  client_mutation_id=client_mutation_id, blocks=blocks,
                                  restored_from_revision_id=None,
                                  scene_identity_update=scene_identity_update)

    async def restore(self, document_id, *, revision_id, base_revision_id, client_mutation_id):
        async with self._db() as db:
            revision = await self._revision(db, revision_id)
            if revision.document_id != document_id:
                raise DocumentNotFound("revision not found")
        return await self._change(document_id, base_revision_id=base_revision_id, markdown=revision.markdown,
                                  client_mutation_id=client_mutation_id, blocks=None,
                                  restored_from_revision_id=revision_id, restored_blocks=revision.blocks,
                                  restored_scene_identities=revision.scene_identities)

    async def _change(self, document_id, *, base_revision_id, markdown, client_mutation_id,
                      blocks, restored_from_revision_id, restored_blocks=None,
                      scene_identity_update=None, restored_scene_identities=None):
        if not client_mutation_id or not base_revision_id:
            raise DocumentValidation("base_revision_id and client_mutation_id required")
        normalized = ([{"id": b.id, "markdown": b.markdown} if isinstance(b, Block) else b
                       for b in blocks] if blocks is not None else None)
        digest_payload = dict(base_revision_id=base_revision_id, markdown=markdown,
                              blocks=normalized, restored_from_revision_id=restored_from_revision_id)
        if scene_identity_update is not None:
            digest_payload["scene_identity_update"] = scene_identity_update
        digest = _digest(digest_payload)
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
                identities = ()
                if current.kind == "episode_script":
                    if restored_scene_identities is not None:
                        specs = _scene_specs(markdown, parsed)
                        if (len(specs) != len(restored_scene_identities)
                                or any(spec[0] != item.heading
                                       for spec, item in zip(specs, restored_scene_identities))):
                            raise DocumentValidation("restored scene identities do not match screenplay")
                        identities = tuple(SceneIdentity(
                            item.id, ordinal, item.heading, item.parent_ids, spec[1]
                        ) for ordinal, (item, spec) in enumerate(zip(restored_scene_identities, specs), start=1))
                    else:
                        known_parent_ids = None
                        if scene_identity_update is not None:
                            known_parent_rows = await (await db.execute(
                                "SELECT DISTINCT scene_id FROM script_scene_identities WHERE document_id=?",
                                (document_id,),
                            )).fetchall()
                            known_parent_ids = {row["scene_id"] for row in known_parent_rows}
                        identities = _scene_identity_records(
                            current.id, markdown, parsed, current.revision.scene_identities,
                            scene_identity_update,
                            known_parent_ids=known_parent_ids,
                        )
                revision_id, stamp = _id(), _now()
                await db.execute("INSERT INTO script_revisions VALUES (?,?,?,?,?,?,?,?)",
                                 (revision_id, document_id, base_revision_id, markdown,
                                  json.dumps(_as_json_blocks(parsed), ensure_ascii=False),
                                  client_mutation_id, stamp, restored_from_revision_id))
                await self._persist_scene_identities(db, document_id, revision_id, identities)
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

    async def generation_start(self, data, mutation_id):
        legacy_digest = _digest({key: data[key] for key in (
            "mode", "brief_id", "script_mode", "episode_count", "episode_number", "instruction")})
        digest = _digest({key: data.get(key) for key in (
            "mode", "brief_id", "brief_revision_id", "script_mode", "episode_count", "episode_number",
            "instruction", "source_idea", "source_ideation_id", "source_candidate_id",
            "confirmed_revisions", "plan_run_id")})
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute("SELECT * FROM script_generation_runs WHERE mutation_id=?", (mutation_id,))).fetchone()
                if row:
                    if row["request_hash"] not in {digest, legacy_digest}:
                        raise DocumentConflict("generation mutation id reused with different payload")
                    await db.rollback()
                    return json.loads(row["data"])
                stamp = _now()
                data = {**data, "id": _id(), "created_at": stamp, "updated_at": stamp}
                await db.execute("INSERT INTO script_generation_runs VALUES (?,?,?,?,?,?)",
                                 (data["id"], mutation_id, digest, json.dumps(data, ensure_ascii=False), stamp, stamp))
                await db.commit()
                return data
            except Exception:
                await db.rollback()
                raise

    async def generation_confirm_plan(self, plan_run_id, *, screenplay_data, mutation_id,
                                      revisions_hash, frozen_revisions, output_candidates):
        """Atomically validate a plan revision collection and create its unique screenplay run."""
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute(
                    "SELECT data FROM script_generation_runs WHERE id=?", (plan_run_id,))).fetchone()
                if row is None:
                    raise DocumentNotFound("planning run not found")
                plan = json.loads(row["data"])
                if plan.get("mode") != "planning" or plan.get("status") != "completed":
                    raise DocumentValidation("a completed planning run is required")

                fixed_outputs = {}
                for step in plan.get("steps", []):
                    output = step.get("output") or {}
                    if output.get("kind") == "document":
                        document_id = output.get("document_id")
                        if not document_id:
                            raise DocumentValidation("planning output is incomplete")
                        fixed_outputs[document_id] = None
                    elif output.get("kind") == "candidate":
                        candidate_id = output.get("candidate_id")
                        candidate = await (await db.execute(
                            "SELECT target_document_id FROM script_generation_candidates WHERE id=? AND run_id=?",
                            (candidate_id, plan_run_id))).fetchone()
                        document_id = output.get("document_id") or (candidate["target_document_id"] if candidate else None)
                        if not candidate or not document_id or candidate["target_document_id"] != document_id:
                            raise DocumentValidation("planning candidate output is incomplete")
                        fixed_outputs[document_id] = candidate_id
                    else:
                        raise DocumentValidation("planning output is incomplete")
                if fixed_outputs != output_candidates:
                    raise DocumentConflict("planning output document collection changed")

                source_idea = plan.get("source_idea") or {}
                source_candidate_id = source_idea.get("candidate_id")
                source_revision_id = source_idea.get("revision_id")
                brief_id = plan.get("brief_id")
                if (screenplay_data.get("plan_run_id") != plan_run_id
                        or screenplay_data.get("brief_id") != brief_id
                        or screenplay_data.get("source_candidate_id") != source_candidate_id
                        or screenplay_data.get("source_idea", {}).get("revision_id") != source_revision_id):
                    raise DocumentConflict("screenplay confirmation source changed")
                if (frozen_revisions.get(brief_id) != plan.get("brief_revision_id")
                        or frozen_revisions.get(source_candidate_id) != source_revision_id
                        or set(frozen_revisions) != {brief_id, source_candidate_id, *fixed_outputs}):
                    raise DocumentConflict("planning revisions do not match the exact source and output revisions")

                for document_id, revision_id in frozen_revisions.items():
                    if document_id == source_candidate_id:
                        revision = await (await db.execute(
                            "SELECT document_id FROM script_revisions WHERE id=?", (revision_id,))).fetchone()
                        if revision is None or revision["document_id"] != document_id:
                            raise DocumentNotFound("confirmed idea revision not found")
                        continue
                    document = await (await db.execute(
                        "SELECT current_revision_id FROM script_documents WHERE id=?", (document_id,))).fetchone()
                    if document is None:
                        raise DocumentNotFound("planning document not found")
                    if document["current_revision_id"] != revision_id:
                        raise DocumentConflict("a planning revision has changed", document["current_revision_id"])

                for document_id, candidate_id in output_candidates.items():
                    if candidate_id is None:
                        continue
                    proposal = await (await db.execute(
                        "SELECT p.id, p.document_id, a.revision_id FROM script_proposals p "
                        "JOIN script_proposal_adoptions a ON a.proposal_id=p.id "
                        "WHERE p.source_candidate_id=? AND p.status='adopted' ORDER BY p.created_at DESC LIMIT 1",
                        (candidate_id,))).fetchone()
                    if (proposal is None or proposal["document_id"] != document_id
                            or not await self._revision_descends_from(
                                db, document_id, proposal["revision_id"], frozen_revisions[document_id])):
                        raise DocumentValidation("review and adopt each planning candidate before confirmation")

                confirmations = plan.setdefault("confirmations", {})
                confirmation_hashes = plan.setdefault("confirmation_hashes", {})
                old_hash = confirmation_hashes.get(mutation_id)
                if old_hash is not None and old_hash != revisions_hash:
                    raise DocumentConflict("confirmation mutation id reused with different revisions")

                existing = await (await db.execute(
                    "SELECT run_id FROM script_plan_confirmation_runs WHERE plan_run_id=? AND revisions_hash=?",
                    (plan_run_id, revisions_hash))).fetchone()
                if existing:
                    screenplay_row = await (await db.execute(
                        "SELECT data FROM script_generation_runs WHERE id=?", (existing["run_id"],))).fetchone()
                    if screenplay_row is None:
                        raise DocumentConflict("confirmed screenplay run is missing")
                    screenplay = json.loads(screenplay_row["data"])
                else:
                    stamp, run_id = _now(), _id()
                    screenplay = {**screenplay_data, "id": run_id, "created_at": stamp, "updated_at": stamp}
                    deterministic_mutation = f"creative-screenplay-confirm:{plan_run_id}:{revisions_hash}"
                    request_hash = _digest({key: screenplay.get(key) for key in (
                        "mode", "brief_id", "brief_revision_id", "script_mode", "episode_count",
                        "episode_number", "instruction", "source_idea", "source_ideation_id",
                        "source_candidate_id", "confirmed_revisions", "plan_run_id")})
                    await db.execute("INSERT INTO script_generation_runs VALUES (?,?,?,?,?,?)",
                                     (run_id, deterministic_mutation, request_hash,
                                      json.dumps(screenplay, ensure_ascii=False), stamp, stamp))
                    await db.execute("INSERT INTO script_plan_confirmation_runs VALUES (?,?,?)",
                                     (plan_run_id, revisions_hash, run_id))

                prior_run = confirmations.get(mutation_id)
                if prior_run is not None and prior_run != screenplay["id"]:
                    raise DocumentConflict("confirmation mutation id is bound to another run")
                confirmations[mutation_id] = screenplay["id"]
                confirmation_hashes[mutation_id] = revisions_hash
                plan.update(phase="confirmed", confirmed_revisions=frozen_revisions,
                            screenplay_run_id=screenplay["id"], updated_at=_now())
                await db.execute("UPDATE script_generation_runs SET data=?,updated_at=? WHERE id=?",
                                 (json.dumps(plan, ensure_ascii=False), plan["updated_at"], plan_run_id))
                await db.commit()
                return {"plan_run": plan, "screenplay_run": screenplay}
            except Exception:
                await db.rollback()
                raise

    async def _revision_descends_from(self, db, document_id, ancestor_revision_id, revision_id):
        cursor = revision_id
        visited = set()
        while cursor and cursor not in visited:
            if cursor == ancestor_revision_id:
                return True
            visited.add(cursor)
            row = await (await db.execute(
                "SELECT document_id,parent_revision_id FROM script_revisions WHERE id=?", (cursor,))).fetchone()
            if row is None or row["document_id"] != document_id:
                return False
            cursor = row["parent_revision_id"]
        return False

    async def generation_get(self, run_id):
        async with self._db() as db:
            row = await (await db.execute("SELECT data FROM script_generation_runs WHERE id=?", (run_id,))).fetchone()
            if not row:
                raise DocumentNotFound("generation run not found")
            return json.loads(row["data"])

    async def generation_list(self):
        async with self._db() as db:
            rows = await (await db.execute("SELECT data FROM script_generation_runs ORDER BY created_at DESC",)).fetchall()
            return [json.loads(row["data"]) for row in rows]

    async def generation_requeue(self, run_id):
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute("SELECT data FROM script_generation_runs WHERE id=?", (run_id,))).fetchone()
                if not row:
                    raise DocumentNotFound("generation run not found")
                data = json.loads(row["data"])
                if data["status"] not in {"failed", "paused"}:
                    raise DocumentConflict("generation run cannot be retried")
                data.update(status="pending", task_id=None, error=None, updated_at=_now())
                await db.execute("UPDATE script_generation_runs SET data=?,updated_at=? WHERE id=?",
                                 (json.dumps(data, ensure_ascii=False), data["updated_at"], run_id))
                await db.commit()
                return data
            except Exception:
                await db.rollback()
                raise

    async def generation_claim(self, run_id, task_id):
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute("SELECT data FROM script_generation_runs WHERE id=?", (run_id,))).fetchone()
                if not row:
                    raise DocumentNotFound("generation run not found")
                data = json.loads(row["data"])
                if data["status"] not in {"pending", "failed", "paused"}:
                    raise DocumentConflict("generation run is already active or requires rebase")
                data.update(status="running", task_id=task_id, error=None, updated_at=_now())
                await db.execute("UPDATE script_generation_runs SET data=?,updated_at=? WHERE id=?",
                                 (json.dumps(data, ensure_ascii=False), data["updated_at"], run_id))
                await db.commit()
                return data
            except Exception:
                await db.rollback()
                raise

    async def generation_update(self, run_id, data, *, expected_task_id=None):
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute("SELECT data FROM script_generation_runs WHERE id=?", (run_id,))).fetchone()
                if not row:
                    raise DocumentNotFound("generation run not found")
                previous = json.loads(row["data"])
                if expected_task_id is not None and previous.get("task_id") != expected_task_id:
                    raise DocumentConflict("generation task superseded")
                if previous.get("task_id") and data.get("task_id") != previous.get("task_id"):
                    raise DocumentConflict("generation task superseded")
                data = {**data, "updated_at": _now()}
                await db.execute("UPDATE script_generation_runs SET data=?,updated_at=? WHERE id=?",
                                 (json.dumps(data, ensure_ascii=False), data["updated_at"], run_id))
                await db.commit()
                return data
            except Exception:
                await db.rollback()
                raise

    async def generation_commit_step(self, run_id, *, step_index, task_id, markdown, references,
                                     target_kind, target_episode, target_id, target_ids, target_revision,
                                     context_slots, allow_fill, commit_guard=None, craft_dependencies=()):
        """Compare every input and target inside one write transaction before publishing output."""
        async with self._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute("SELECT data FROM script_generation_runs WHERE id=?", (run_id,))).fetchone()
                if not row:
                    raise DocumentNotFound("generation run not found")
                data = json.loads(row["data"])
                if data.get("task_id") != task_id or data.get("status") != "running" or data["steps"][step_index]["status"] != "running":
                    raise DocumentConflict("generation task superseded")
                for document_id, revision_id in references.items():
                    ref = await (await db.execute("SELECT current_revision_id FROM script_documents WHERE id=?", (document_id,))).fetchone()
                    if ref is None or ref["current_revision_id"] != revision_id:
                        raise DocumentConflict("generation context changed", ref["current_revision_id"] if ref else None)
                for slot in context_slots:
                    rows = await (await db.execute(
                        "SELECT id FROM script_documents WHERE kind=? AND episode_number IS ? ORDER BY created_at,id",
                        (slot["kind"], slot["episode_number"]))).fetchall()
                    if [item["id"] for item in rows] != slot["document_ids"]:
                        raise DocumentConflict("generation context document set changed")
                target_rows = await (await db.execute(
                    "SELECT id,current_revision_id FROM script_documents WHERE kind=? AND episode_number IS ? ORDER BY created_at,id",
                    (target_kind, target_episode))).fetchall()
                if [item["id"] for item in target_rows] != target_ids:
                    raise DocumentConflict("generation target document set changed")
                target = target_rows[0] if target_rows else None
                if (target["id"] if target else None) != target_id or (target["current_revision_id"] if target else None) != target_revision:
                    raise DocumentConflict("generation target changed", target["current_revision_id"] if target else None)
                if commit_guard is not None:
                    commit_guard()
                    table = await (await db.execute("SELECT name FROM sqlite_master WHERE name='task_states'")).fetchone()
                    if table:
                        task = await (await db.execute("SELECT status FROM task_states WHERE task_id=?", (task_id,))).fetchone()
                        if task is None or task["status"] not in {"queued", "running"}:
                            from novelvideo.task_backend.cancel import TaskCancelled
                            raise TaskCancelled()
                stamp = _now()
                if craft_dependencies and target is None:
                    # A downstream candidate needs a reviewable destination, but its
                    # unadopted upstream design must never become published content.
                    target_id, target_revision = _id(), _id()
                    await db.execute('INSERT INTO script_documents VALUES (?,?,?,?,?,?,?,?,?)',
                        (target_id, target_kind, data['steps'][step_index]['title'], target_episode,
                         target_revision, None, None, stamp, stamp))
                    await db.execute('INSERT INTO script_revisions VALUES (?,?,?,?,?,?,?,?)',
                        (target_revision, target_id, None, '', '[]', f'craft-slot:{run_id}:{step_index}', stamp, None))
                    target = {'id': target_id, 'current_revision_id': target_revision}
                    data['baseline_revisions'][target_id] = target_revision
                if target and (not allow_fill or craft_dependencies):
                    candidate_id = _id()
                    await db.execute("INSERT INTO script_generation_candidates VALUES (?,?,?,?,?,?,?,?)",
                                     (candidate_id, run_id, data["steps"][step_index]["key"], target_id,
                                      target_revision, markdown, json.dumps(references), stamp))
                    output = {"kind": "candidate", "candidate_id": candidate_id,
                              "document_id": target_id, "baseline_revision_id": target_revision}
                    for dependency in craft_dependencies:
                        await db.execute('INSERT INTO script_craft_dependencies VALUES (?,?,?,?)',
                            (candidate_id, dependency['document_id'], dependency['source_candidate_id'],
                             dependency['content_hash']))
                else:
                    document_id = target_id or _id()
                    revision_id = _id()
                    if target:
                        previous = await self._revision(db, target_revision)
                        blocks = _blocks(markdown, previous=previous.blocks)
                    else:
                        blocks = _blocks(markdown)
                        title = data["steps"][step_index]["title"]
                        source_origin = None
                        if data.get('mode') == 'screenplay':
                            source_origin = json.dumps({
                                'workflow_run_id': run_id,
                                'plan_run_id': data.get('plan_run_id'),
                                'confirmed_revisions': data.get('confirmed_revisions', {}),
                            }, ensure_ascii=False)
                        await db.execute("INSERT INTO script_documents VALUES (?,?,?,?,?,?,?,?,?)",
                                         (document_id, target_kind, title, target_episode, revision_id, None,
                                          source_origin, stamp, stamp))
                    await db.execute("INSERT INTO script_revisions VALUES (?,?,?,?,?,?,?,?)",
                                     (revision_id, document_id, target_revision, markdown,
                                      json.dumps(_as_json_blocks(blocks), ensure_ascii=False),
                                      f"generate:{run_id}:{step_index}", stamp, None))
                    if target:
                        await db.execute("UPDATE script_documents SET current_revision_id=?,updated_at=? WHERE id=?",
                                         (revision_id, stamp, document_id))
                    output = {"kind": "document", "document_id": document_id, "revision_id": revision_id}
                    if target_id in data.get("baseline_revisions", {}):
                        # A run may fill an existing blank template. Move only its own
                        # target pointer, while step.context_revisions retains the input.
                        data["baseline_revisions"][target_id] = revision_id
                step = data["steps"][step_index]
                step.update(status="completed", output=output, error=None, context_revisions=references,
                            task_id=task_id)
                if all(item["status"] == "completed" for item in data["steps"]):
                    data["status"] = "completed"
                data["updated_at"] = stamp
                await db.execute("UPDATE script_generation_runs SET data=?,updated_at=? WHERE id=?",
                                 (json.dumps(data, ensure_ascii=False), stamp, run_id))
                await db.commit()
                return data
            except Exception:
                await db.rollback()
                raise

    async def generation_candidate(self, candidate_id):
        async with self._db() as db:
            row = await (await db.execute("SELECT * FROM script_generation_candidates WHERE id=?", (candidate_id,))).fetchone()
            if not row:
                raise DocumentNotFound("candidate not found")
            result = dict(row)
            run = await (await db.execute('SELECT data FROM script_generation_runs WHERE id=?', (row['run_id'],))).fetchone()
            if run:
                step = next((s for s in json.loads(run['data'])['steps'] if s['key'] == row['step_key']), {})
                result['craft_review'] = step.get('craft_review', {'status': 'unreviewed'})
            return result

    async def find_import(self, episode_number):
        async with self._db() as db:
            row = await (await db.execute(
                "SELECT id FROM script_documents WHERE json_extract(source_origin, '$.source_episode_number')=?",
                (episode_number,))).fetchone()
            return await self._document(db, row["id"]) if row else None
