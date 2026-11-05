"""Create and durably project a confirmed script handoff as a managed episode."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from novelvideo.freezone.canvas_store import default_canvas_payload
from novelvideo.freezone.orchestration.models import fingerprint
from .models import OperationResult
from .operations import OperationError, create_text_object
from .store import ObjectStore


class ScriptHandoffCanvas:
    """Creation-store side of the script handoff bridge.

    The caller holds the source database's BEGIN IMMEDIATE lock and checks the
    current source before calling ``create``. This service commits the episode,
    semantic text object, source binding, operation, and projection outbox in a
    single creation-store transaction.
    """

    def __init__(self, objects: ObjectStore, *, project_id: str):
        self.objects = objects
        self.project_id = str(project_id)

    @staticmethod
    def lookup_existing(state_dir: Path | str, handoff_id: str) -> dict | None:
        """Read a prior mapping without initializing or mutating creation storage."""
        db_path = Path(state_dir) / "creation" / "objects.sqlite3"
        if not db_path.is_file():
            return None
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='script_handoff_bridges'"
            ).fetchone()
            if table is None:
                return None
            row = conn.execute(
                "SELECT * FROM script_handoff_bridges WHERE handoff_id=?", (handoff_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    @staticmethod
    def response(mapping: dict, result: OperationResult) -> dict:
        return {
            "episode_id": mapping["episode_id"],
            "canvas_id": mapping["canvas_id"],
            "title": mapping["title"],
            "number": mapping["episode_number"],
            "story_fragment_id": mapping["story_fragment_id"],
            "node_id": mapping["node_id"],
            "operation_id": mapping["operation_id"],
            "status": result.status,
        }

    def create(self, handoff: dict, *, actor: str) -> dict:
        """Create once per confirmed handoff; replay is independent of actor."""
        snapshot = handoff.get("snapshot") or {}
        handoff_id = handoff.get("id")
        episode_number = handoff.get("episode_number")
        source_revision = handoff.get("source_revision")
        source_hash = handoff.get("source_hash")
        title = snapshot.get("title")
        markdown = snapshot.get("markdown")
        if (handoff.get("project_id") != self.project_id
                or snapshot.get("project_id") != self.project_id
                or snapshot.get("episode_number") != episode_number
                or not isinstance(handoff_id, str) or not handoff_id
                or type(episode_number) is not int or episode_number < 1
                or type(source_revision) is not int or source_revision < 1
                or not isinstance(source_hash, str) or not source_hash
                or snapshot.get("content_hash") != source_hash
                or not isinstance(title, str) or not title.strip()
                or not isinstance(markdown, str) or not markdown.strip()):
            raise OperationError(
                "invalid_contract", "已确认剧本快照不完整，无法创建剧集画布",
                handoff_id=handoff_id,
            )

        source_ref = {
            "kind": "script_handoff",
            "handoff_id": handoff_id,
            "document_id": handoff["document_id"],
            "revision_id": handoff["revision_id"],
            "source_revision": source_revision,
            "source_hash": source_hash,
        }
        snapshot_hash = fingerprint({
            "project_id": self.project_id,
            "episode_number": episode_number,
            "title": title,
            **source_ref,
        })
        request_id = f"script-handoff-canvas:{handoff_id}"
        intent = {
            "kind": "script_handoff.canvas.create",
            "project_id": self.project_id,
            "handoff_id": handoff_id,
            "snapshot_hash": snapshot_hash,
        }
        request_hash = fingerprint(intent)

        with self.objects.transaction() as conn:
            previous = conn.execute(
                "SELECT * FROM script_handoff_bridges WHERE handoff_id=?", (handoff_id,)
            ).fetchone()
            if previous is not None:
                if previous["snapshot_hash"] != snapshot_hash:
                    raise OperationError(
                        "idempotency_conflict", "同一交接编号对应了不同的已确认剧本",
                        handoff_id=handoff_id,
                    )
                return dict(previous)

            # Creation requests and script handoffs share one request-ID table.
            # A manually reused ID is a conflict, never a reason to adopt data.
            previous_operation = conn.execute(
                "SELECT request_hash FROM operations WHERE request_id=?", (request_id,)
            ).fetchone()
            if previous_operation is not None:
                raise OperationError(
                    "idempotency_conflict", "交接请求编号已被其他创作操作使用",
                    handoff_id=handoff_id,
                )

            existing_episodes = conn.execute(
                "SELECT v.payload FROM objects o JOIN object_versions v "
                "ON v.object_id=o.id AND v.revision=o.current_revision WHERE o.kind='episode'"
            ).fetchall()
            for row in existing_episodes:
                try:
                    existing_payload = json.loads(row["payload"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if existing_payload.get("number") == episode_number:
                    raise OperationError(
                        "episode_number_conflict",
                        f"第 {episode_number} 集已存在；请先处理现有剧集，系统不会自动接管或覆盖",
                        episode_number=episode_number,
                    )

            episode_id = uuid4().hex
            canvas_id = uuid4().hex
            operation_id = uuid4().hex
            node_id = uuid4().hex
            screenplay_node_id = uuid4().hex
            director_node_id = uuid4().hex
            stamp = datetime.now(timezone.utc).isoformat()
            episode_payload = {
                "title": title,
                "number": episode_number,
                "operation_id": operation_id,
                "source_handoff": source_ref,
            }
            story_fragment = create_text_object(
                conn,
                actor,
                kind="story_fragment",
                data={"title": title, "text": markdown},
                canvas_id=canvas_id,
                episode_id=episode_id,
            )
            nodes = [
                {
                    "id": node_id,
                    "type": "textAnnotationNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "content": markdown,
                        "displayName": title,
                        "referenceOnly": True,
                        "formalScriptRef": {
                            "kind": "episode_script", "project_id": self.project_id,
                            "document_id": handoff["document_id"],
                            "revision_id": handoff["revision_id"],
                            "episode_number": episode_number,
                            "title": title,
                        },
                        "semanticRef": {
                            "object_id": story_fragment["id"],
                            "kind": "story_fragment",
                            "revision": story_fragment["revision"],
                        },
                        # This points to the immutable handoff snapshot. The node's
                        # editable content is not itself treated as source evidence.
                        "sourceSnapshot": {
                            **source_ref,
                            "snapshot_hash": snapshot_hash,
                        },
                    },
                },
                {
                    "id": screenplay_node_id,
                    "type": "screenplayNode",
                    "position": {"x": 640, "y": 0},
                    "width": 560,
                    "height": 620,
                    "data": {
                        "displayName": f"E{episode_number:02d} · 正式剧本",
                        "episodeNumber": episode_number,
                        "documentId": handoff["document_id"],
                        "documentRevisionId": handoff["revision_id"],
                        "sourceHandoffId": handoff_id,
                    },
                },
                {
                    "id": director_node_id,
                    "type": "directorWorkspaceNode",
                    "position": {"x": 1280, "y": 0},
                    "width": 600,
                    "height": 560,
                    "data": {
                        "displayName": f"E{episode_number:02d} · 导演工作空间",
                        "episodeNumber": episode_number,
                        "sourceDocumentId": handoff["document_id"],
                        "sourceDocumentRevisionId": handoff["revision_id"],
                        "sourceHandoffId": handoff_id,
                    },
                },
            ]
            scope = {
                "project_id": self.project_id,
                "episode_id": episode_id,
                "canvas_id": canvas_id,
                "node_ids": [node_id, screenplay_node_id, director_node_id],
            }
            canvas = default_canvas_payload(project_id=self.project_id, actor_id=actor)
            canvas.update(
                canvas_id=canvas_id,
                name=title,
                nodes=nodes,
                metadata={
                    "episode_id": episode_id,
                    "title": title,
                    "source_handoff_id": handoff_id,
                },
            )
            patch = {
                "schema_version": 1,
                "operation_id": operation_id,
                "scope": scope,
                "canvas_create": canvas,
                "relation_changes": [],
            }
            episode_ref = {
                "object_id": episode_id,
                "kind": "episode",
                "revision": 1,
                "canvas_id": canvas_id,
                "title": title,
                "number": episode_number,
            }
            fragment_ref = {
                "object_id": story_fragment["id"],
                "kind": "story_fragment",
                "revision": story_fragment["revision"],
                "canvas_id": canvas_id,
                "episode_id": episode_id,
                "node_id": node_id,
            }
            operation_result = OperationResult(
                operation_id=operation_id,
                status="running",
                object_refs=[episode_ref, fragment_ref,
                             {"kind": "screenplay_node", "node_id": screenplay_node_id,
                              "document_id": handoff["document_id"], "revision_id": handoff["revision_id"]},
                             {"kind": "director_workspace_node", "node_id": director_node_id,
                              "episode_number": episode_number}],
            )
            conn.execute(
                "INSERT INTO objects (id,kind,current_revision) VALUES (?, 'episode', 1)",
                (episode_id,),
            )
            conn.execute(
                "INSERT INTO object_versions VALUES (?,1,?,?,?)",
                (episode_id, json.dumps(episode_payload, ensure_ascii=False), actor, stamp),
            )
            conn.execute("INSERT INTO episode_canvases VALUES (?,?)", (episode_id, canvas_id))
            conn.execute(
                "INSERT INTO canvas_workspaces VALUES (?,?,?,?,?,?)",
                (canvas_id, "episode", episode_id, title, operation_id, stamp),
            )
            conn.execute(
                "INSERT INTO operations VALUES (?,?,?,?,?,?,?)",
                (
                    operation_id,
                    request_id,
                    request_hash,
                    "pending_projection",
                    json.dumps({**intent, "scope": scope}, ensure_ascii=False),
                    operation_result.model_dump_json(),
                    actor,
                ),
            )
            conn.execute(
                "INSERT INTO projection_outbox (operation_id,canvas_id,patch) VALUES (?,?,?)",
                (operation_id, canvas_id, json.dumps(patch, ensure_ascii=False)),
            )
            conn.execute(
                "INSERT INTO script_handoff_bridges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    handoff_id,
                    episode_number,
                    handoff["document_id"],
                    handoff["revision_id"],
                    source_revision,
                    source_hash,
                    snapshot_hash,
                    title,
                    episode_id,
                    canvas_id,
                    story_fragment["id"],
                    node_id,
                    operation_id,
                    stamp,
                ),
            )
            row = conn.execute(
                "SELECT * FROM script_handoff_bridges WHERE handoff_id=?", (handoff_id,)
            ).fetchone()
            return dict(row)
