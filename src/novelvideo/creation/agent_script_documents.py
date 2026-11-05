"""Trusted Agent lineage for formal screenplay drafts and document-scoped reviews."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from novelvideo.script_creation.store import DocumentConflict, DocumentNotFound, DocumentValidation


def _now():
    return datetime.now(timezone.utc).isoformat()


def _stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DocumentValidation(f"{label} is required")
    return value.strip()


def _member(value, label):
    if not isinstance(value, dict):
        raise DocumentValidation(f"{label} identity is required")
    identifier = value.get("adoption_id")
    revision = value.get("revision")
    name = value.get("name")
    if (not isinstance(identifier, str) or not identifier
            or type(revision) is not int or revision < 1
            or not isinstance(name, str) or not name.strip()):
        raise DocumentValidation(f"{label} identity is invalid")
    return {"adoption_id": identifier, "revision": revision, "name": name.strip()}


def _source_ref(value):
    if value is None:
        return None
    fields = {"kind", "output_id", "source_run_id", "source_task_key", "source_delegation_id",
              "source_action_request_id", "source_operation_id"}
    if (not isinstance(value, dict) or value.get("kind") != "run_output"
            or set(value) != fields
            or any(not isinstance(value.get(key), str) or not value[key]
                   for key in fields - {"kind", "source_task_key"})
            or value.get("source_task_key") is not None
            and (not isinstance(value["source_task_key"], str) or not value["source_task_key"])):
        raise DocumentValidation("script source must be a trusted run output")
    return dict(value)


def _report(value):
    if (not isinstance(value, dict) or set(value) != {"conclusion", "summary", "issues"}
            or value.get("conclusion") not in {"passed", "needs_revision"}
            or not isinstance(value.get("summary"), str) or not value["summary"].strip()
            or len(value["summary"]) > 12000 or not isinstance(value.get("issues"), list)
            or len(value["issues"]) > 100):
        raise DocumentValidation("script review report is invalid")
    issues = []
    for item in value["issues"]:
        if (not isinstance(item, dict) or set(item) != {"severity", "issue", "evidence", "suggestion"}
                or item.get("severity") not in {"blocking", "major", "minor", "note"}
                or any(not isinstance(item.get(key), str) or len(item[key]) > 4000
                       for key in ("issue", "evidence", "suggestion"))
                or not item["issue"].strip() or not item["evidence"].strip()):
            raise DocumentValidation("script review issue is invalid")
        issues.append({key: item[key].strip() for key in ("severity", "issue", "evidence", "suggestion")})
    return {"conclusion": value["conclusion"], "summary": value["summary"].strip(), "issues": issues}


class AgentScriptDocuments:
    """Use the existing immutable document store; keep Agent review receipts beside it."""

    def __init__(self, documents):
        self.documents = documents

    async def initialize(self):
        await self.documents.initialize()
        async with self.documents._db() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_script_reviews (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    canvas_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    episode_number INTEGER,
                    candidate_hash TEXT NOT NULL,
                    source_delegation_id TEXT NOT NULL,
                    reviewer_delegation_id TEXT NOT NULL,
                    reviewer_adoption_id TEXT NOT NULL,
                    reviewer_revision INTEGER NOT NULL,
                    reviewer_name TEXT NOT NULL,
                    report TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_script_reviews_document
                    ON agent_script_reviews(project_id, document_id, revision_id, created_at);
            """)
            columns = await (await db.execute("PRAGMA table_info(agent_script_reviews)")).fetchall()
            if "episode_number" not in {row["name"] for row in columns}:
                await db.execute("ALTER TABLE agent_script_reviews ADD COLUMN episode_number INTEGER")
            # Older review rows may predate the episode field. Recover it only
            # from the canonical formal document metadata; never label an
            # unknown historical review as episode one.
            await db.execute("""UPDATE agent_script_reviews
                SET episode_number=(SELECT d.episode_number FROM script_documents d
                                    WHERE d.id=agent_script_reviews.document_id)
                WHERE EXISTS (SELECT 1 FROM script_documents d
                              WHERE d.id=agent_script_reviews.document_id)""")
            await db.commit()

    async def create_draft(self, *, request_id, project_id, canvas_id, delegation_id, writer,
                           episode_number, title, markdown, source_ref=None):
        await self.initialize()
        request_id = _text(request_id, "request_id", 128)
        project_id = _text(project_id, "project_id", 128)
        canvas_id = _text(canvas_id, "canvas_id", 128)
        delegation_id = _text(delegation_id, "delegation_id", 128)
        writer = _member(writer, "writer")
        source_ref = _source_ref(source_ref)
        if type(episode_number) is not int or episode_number < 1:
            raise DocumentValidation("explicit positive episode_number is required")
        title = _text(title, "title", 200)
        markdown = _text(markdown, "markdown", 100000)
        source_origin = {
            "kind": "agent_script_draft",
            "project_id": project_id,
            "canvas_id": canvas_id,
            "delegation_id": delegation_id,
            "member_id": writer["adoption_id"],
            "member_revision": writer["revision"],
            "source_ref": source_ref,
        }
        mutation_id = "agent-script-draft:" + _hash([project_id, delegation_id, request_id])[:48]
        document = await self.documents.create(
            kind="episode_script", title=title, markdown=markdown,
            client_mutation_id=mutation_id, episode_number=episode_number,
            source_origin=source_origin,
        )
        return {
            "state": "draft",
            "document_ref": {"document_id": document.id, "revision_id": document.current_revision_id,
                             "kind": document.kind, "episode_number": document.episode_number},
            "title": document.title,
            "markdown": document.revision.markdown,
            "source_origin": source_origin,
            "adopted": document.adopted_revision_id is not None,
        }

    async def record_review(self, *, request_id, project_id, canvas_id, delegation_id, reviewer,
                           document_id, revision_id, report):
        await self.initialize()
        request_id = _text(request_id, "request_id", 128)
        project_id = _text(project_id, "project_id", 128)
        canvas_id = _text(canvas_id, "canvas_id", 128)
        delegation_id = _text(delegation_id, "delegation_id", 128)
        reviewer = _member(reviewer, "reviewer")
        document_id = _text(document_id, "document_id", 128)
        revision_id = _text(revision_id, "revision_id", 128)
        report = _report(report)
        document = await self.documents.get(document_id)
        if (document.kind != "episode_script" or document.current_revision_id != revision_id
                or document.source_origin is None
                or document.source_origin.get("kind") != "agent_script_draft"
                or document.source_origin.get("project_id") != project_id
                or document.source_origin.get("canvas_id") != canvas_id):
            raise DocumentConflict("script review source is not the current Agent draft")
        writer_id = document.source_origin.get("member_id")
        if not reviewer["adoption_id"] or reviewer["adoption_id"] == writer_id:
            raise DocumentValidation("an independent reviewer is required")
        revision = await self.documents.get_revision(document_id, revision_id)
        candidate_hash = hashlib.sha256(revision.revision.markdown.encode("utf-8")).hexdigest()
        body = {
            "project_id": project_id, "canvas_id": canvas_id, "delegation_id": delegation_id,
            "reviewer": reviewer, "document_id": document_id, "revision_id": revision_id,
            "candidate_hash": candidate_hash, "report": report,
        }
        request_hash = _hash(body)
        async with self.documents._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute(
                    "SELECT * FROM agent_script_reviews WHERE request_id=?", (request_id,)
                )).fetchone()
                if row is not None:
                    if row["request_hash"] != request_hash:
                        raise DocumentConflict("review request_id reused with different payload")
                    await db.rollback()
                    return self._review_row(row)
                created_at = _now()
                review_id = uuid4().hex
                await db.execute("""INSERT INTO agent_script_reviews
                    (id,request_id,request_hash,project_id,canvas_id,document_id,revision_id,episode_number,candidate_hash,
                     source_delegation_id,reviewer_delegation_id,reviewer_adoption_id,reviewer_revision,
                     reviewer_name,report,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (review_id, request_id, request_hash, project_id, canvas_id, document_id, revision_id,
                     document.episode_number,
                     candidate_hash, document.source_origin["delegation_id"], delegation_id,
                     reviewer["adoption_id"], reviewer["revision"], reviewer["name"],
                     _stable(report), created_at))
                stored = await (await db.execute(
                    "SELECT * FROM agent_script_reviews WHERE id=?", (review_id,)
                )).fetchone()
                await db.commit()
                return self._review_row(stored)
            except Exception:
                await db.rollback()
                raise

    @staticmethod
    def _review_row(row):
        report = json.loads(row["report"])
        return {
            "id": row["id"], "status": report["conclusion"], "conclusion": report["conclusion"],
            "summary": report["summary"], "issues": report["issues"],
            "document_ref": {"document_id": row["document_id"], "revision_id": row["revision_id"],
                             "kind": "episode_script", "episode_number": row["episode_number"]},
            "candidate_hash": row["candidate_hash"],
            "source_task_id": row["source_delegation_id"],
            "reviewer": {"adoption_id": row["reviewer_adoption_id"],
                         "revision": row["reviewer_revision"], "name": row["reviewer_name"]},
            "created_at": row["created_at"],
        }

    async def current_review(self, document_id, revision_id):
        # The review table is initialized by the draft/review write paths. This
        # lookup also runs inside HandoffService.canvas_source_guard, which
        # holds a document-store transaction; running CREATE/ALTER statements
        # here would try to upgrade a read-only lookup into a competing schema
        # write and can deadlock SQLite.
        document = await self.documents.get(document_id)
        if document.current_revision_id != revision_id:
            return None
        async with self.documents._db() as db:
            row = await (await db.execute("""SELECT * FROM agent_script_reviews
                WHERE project_id=? AND document_id=? AND revision_id=?
                ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (document.source_origin.get("project_id") if document.source_origin else "",
                 document_id, revision_id))).fetchone()
        return self._review_row(row) if row is not None else None

    def project_draft(self, *, state_dir, project_id, canvas_id, actor, document_ref,
                      title, markdown, request_id):
        """Append an immutable formal-draft reference to an applied concept canvas."""
        from pathlib import Path
        from uuid import uuid4

        from novelvideo.creation.canvas_workspaces import list_canvases
        from novelvideo.creation.models import OperationResult
        from novelvideo.creation.operations import OperationError, create_text_object
        from novelvideo.creation.projection import ProjectionService
        from novelvideo.creation.store import ObjectStore
        from novelvideo.freezone.canvas_store import read_canvas

        state_dir = Path(state_dir)
        workspace = [item for item in list_canvases(state_dir)
                     if item["canvas_id"] == canvas_id and item["kind"] == "concept"
                     and item["episode_id"] is None and item["status"] == "applied"]
        if len(workspace) != 1:
            raise DocumentValidation("formal script drafts can only be projected to the applied concept canvas")
        canvas = read_canvas(state_dir, canvas_id)
        if canvas is None or canvas.get("project_id") != str(project_id):
            raise DocumentValidation("concept canvas is unavailable")

        objects = ObjectStore(state_dir)
        self._init_projection_table(objects)
        request_key = "agent-script-reference:" + _hash([
            str(project_id), canvas_id, document_ref["document_id"], document_ref["revision_id"]
        ])[:48]
        intent = {
            "kind": "agent_script_draft.reference_project",
            "project_id": str(project_id), "canvas_id": canvas_id,
            "document_ref": document_ref,
            "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        }
        request_hash = _hash(intent)
        with objects.transaction() as conn:
            previous = conn.execute("SELECT * FROM agent_script_draft_projections WHERE document_id=? AND revision_id=?",
                                    (document_ref["document_id"], document_ref["revision_id"])).fetchone()
            if previous is not None:
                if previous["canvas_id"] != canvas_id or previous["request_hash"] != request_hash:
                    raise OperationError("idempotency_conflict", "同一剧本修订已投影到不同画布")
                mapping = dict(previous)
            else:
                workspace_row = conn.execute("""SELECT w.kind,w.episode_id,o.state
                    FROM canvas_workspaces w JOIN operations o ON o.id=w.operation_id
                    WHERE w.canvas_id=?""", (canvas_id,)).fetchone()
                if (workspace_row is None or workspace_row["kind"] != "concept"
                        or workspace_row["episode_id"] is not None or workspace_row["state"] != "applied"):
                    raise DocumentValidation("concept canvas registry is unavailable")
                node_id, operation_id = uuid4().hex, uuid4().hex
                fragment = create_text_object(
                    conn, actor, kind="story_fragment", data={"title": title, "text": markdown},
                    canvas_id=canvas_id, episode_id=None,
                )
                roots = [node for node in canvas.get("nodes", []) if not node.get("parentId")]
                next_y = max((float((node.get("position") or {}).get("y", 0)) + 360 for node in roots), default=0)
                node = {
                    "id": node_id, "type": "textAnnotationNode",
                    "position": {"x": 0, "y": next_y}, "width": 520, "height": 360,
                    "data": {
                        "displayName": f"E{document_ref['episode_number']:02d} · {title}",
                        "content": markdown, "referenceOnly": True,
                        "semanticRef": {"object_id": fragment["id"], "kind": "story_fragment",
                                        "revision": fragment["revision"]},
                        "formalScriptRef": {"kind": "episode_script", "project_id": str(project_id),
                                            "canvas_id": canvas_id, **document_ref, "title": title},
                        "sourceSnapshot": {"kind": "agent_script_draft",
                                           "content_hash": intent["content_hash"]},
                    },
                }
                scope = {"project_id": str(project_id), "episode_id": None,
                         "canvas_id": canvas_id, "node_ids": [node_id]}
                result = OperationResult(
                    operation_id=operation_id, status="running",
                    object_refs=[{"kind": "episode_script", **document_ref, "title": title},
                                 {"kind": "story_fragment", **fragment, "canvas_id": canvas_id,
                                  "node_id": node_id}],
                )
                patch = {
                    "schema_version": 1, "operation_id": operation_id, "scope": scope,
                    "base_revision": canvas["revision"], "operations": [{"op": "add_node", "node": node}],
                    "before": {"nodes": {}, "edges": list(canvas.get("edges", []))},
                    "relation_changes": [], "input_guards": {},
                    "touched_fields": [{"node_id": node_id, "fields": [], "op": "add_node"}],
                }
                conn.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?)", (
                    operation_id, request_key, request_hash, "pending_projection",
                    json.dumps({**intent, "scope": scope}, ensure_ascii=False),
                    result.model_dump_json(), str(actor),
                ))
                conn.execute("INSERT INTO projection_outbox VALUES (?,?,?,NULL)",
                             (operation_id, canvas_id, json.dumps(patch, ensure_ascii=False)))
                conn.execute("INSERT INTO agent_script_draft_projections VALUES (?,?,?,?,?,?,?,?,?)", (
                    str(project_id), canvas_id, document_ref["document_id"], document_ref["revision_id"],
                    request_hash, node_id, fragment["id"], operation_id,
                    json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                ))
                mapping = {"project_id": str(project_id), "canvas_id": canvas_id,
                           "document_id": document_ref["document_id"], "revision_id": document_ref["revision_id"],
                           "request_hash": request_hash, "node_id": node_id,
                           "story_fragment_id": fragment["id"], "operation_id": operation_id,
                           "result": result.model_dump_json()}
        operation = ProjectionService(objects).apply(mapping["operation_id"])
        return {"status": operation.status, "canvas_id": canvas_id, "node_id": mapping["node_id"],
                "operation_id": mapping["operation_id"], "canvas_revision": operation.canvas_revision,
                "candidate_id": operation.candidate_id}

    @staticmethod
    def _init_projection_table(objects):
        with objects.transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_script_draft_projections (
                project_id TEXT NOT NULL, canvas_id TEXT NOT NULL, document_id TEXT NOT NULL,
                revision_id TEXT NOT NULL, request_hash TEXT NOT NULL, node_id TEXT NOT NULL,
                story_fragment_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, result TEXT NOT NULL,
                PRIMARY KEY(document_id,revision_id))""")

    async def require_handoff_review(self, document_id, revision_id, *, project_id):
        document = await self.documents.get(document_id)
        if (document.kind != "episode_script" or document.source_origin is None
                or document.source_origin.get("kind") != "agent_script_draft"
                or document.source_origin.get("project_id") != str(project_id)):
            raise DocumentValidation("current Agent script draft is required for handoff")
        if document.current_revision_id != revision_id:
            raise DocumentConflict("script revision changed; its independent review is stale",
                                   document.current_revision_id)
        review = await self.current_review(document_id, revision_id)
        if (review is None or review["status"] != "passed"
                or review["document_ref"].get("episode_number") != document.episode_number
                or review["candidate_hash"] != hashlib.sha256(
                    document.revision.markdown.encode("utf-8")).hexdigest()):
            raise DocumentValidation("a current independent script review must pass before handoff")
        return review
