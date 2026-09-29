"""Immutable, project-scoped handoff of a saved script to episode production."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from novelvideo.episode_sources import EpisodeCandidate, content_sha256
from novelvideo.episode_source_store import EpisodeSourceRevisionConflict
from novelvideo.screenplay_semantics.parser import parse_screenplay_document
from novelvideo.task_backend.client import enqueue_project_task
from novelvideo.task_state import get_task_manager

from .entities import EntityService
from .store import DocumentConflict, DocumentNotFound, DocumentValidation, _digest, _id, _now


def _semantic_extraction_failure(result):
    """A generated revision can be archived while extraction itself has failed."""
    if not isinstance(result, dict):
        return None
    report = result.get("validation_report") or {}
    issues = (report.get("issues") or []) if isinstance(report, dict) else []
    failures = [issue for issue in issues if isinstance(issue, dict) and
                issue.get("code") in {"scene_extraction_failed", "scene_not_processed"}]
    if not failures and not result.get("failed_scenes"):
        return None
    details = [str(issue.get("message") or issue.get("code")) for issue in failures]
    return "; ".join(details) or f"{result.get('failed_scenes', 0)} scene extraction(s) failed"


class HandoffService:
    """The database row and outbox are the business identity, not the task slot."""

    def __init__(self, documents, sources, *, project_id: str):
        self.documents = documents
        self.sources = sources
        self.project_id = str(project_id)
        self.entities = EntityService(documents)

    async def initialize(self):
        await self.documents.initialize()
        await self.sources._db()  # source schema/identity must exist before BEGIN
        await self.entities.initialize()
        async with self.documents._db() as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS script_handoffs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, episode_number INTEGER NOT NULL,
                    document_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    identity_hash TEXT NOT NULL, prepare_mutation_id TEXT NOT NULL,
                    prepare_hash TEXT NOT NULL, confirm_mutation_id TEXT, confirm_hash TEXT,
                    data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id, prepare_mutation_id), UNIQUE(project_id, identity_hash));
                CREATE INDEX IF NOT EXISTS idx_script_handoffs_episode
                    ON script_handoffs(project_id, episode_number, created_at);
                CREATE TABLE IF NOT EXISTS script_handoff_outbox (
                    handoff_id TEXT PRIMARY KEY, envelope TEXT NOT NULL,
                    task_id TEXT, status TEXT NOT NULL, result TEXT, error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            """)
            await db.commit()

    @staticmethod
    def _decode(row):
        return json.loads(row["data"])

    async def _row(self, db, handoff_id):
        row = await (await db.execute(
            "SELECT * FROM script_handoffs WHERE id=? AND project_id=?",
            (handoff_id, self.project_id),
        )).fetchone()
        if row is None:
            raise DocumentNotFound("handoff not found in this project")
        return row

    async def _save(self, db, item):
        item["updated_at"] = _now()
        await db.execute("UPDATE script_handoffs SET data=?,updated_at=? WHERE id=? AND project_id=?",
                         (json.dumps(item, ensure_ascii=False), item["updated_at"], item["id"], self.project_id))

    async def get(self, handoff_id):
        async with self.documents._db() as db:
            return self._decode(await self._row(db, handoff_id))

    async def list(self, episode_number=None):
        async with self.documents._db() as db:
            if episode_number is None:
                rows = await (await db.execute(
                    "SELECT data FROM script_handoffs WHERE project_id=? ORDER BY created_at DESC,rowid DESC",
                    (self.project_id,),
                )).fetchall()
            else:
                rows = await (await db.execute(
                    "SELECT data FROM script_handoffs WHERE project_id=? AND episode_number=? ORDER BY created_at DESC,rowid DESC",
                    (self.project_id, episode_number),
                )).fetchall()
            return [self._decode(row) for row in rows]

    @staticmethod
    async def _source(db, episode):
        return await (await db.execute(
            "SELECT * FROM episode_sources WHERE episode_number=?", (episode,),
        )).fetchone()

    @staticmethod
    async def _project_source_revision(db):
        row = await (await db.execute(
            "SELECT project_revision FROM episode_source_state WHERE id=1"
        )).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _diff(old: str, new: str):
        old_lines, new_lines = old.splitlines(), new.splitlines()
        text = [{"operation": tag, "old_lines": [a + 1, b], "new_lines": [c + 1, d],
                 "before": old_lines[a:b], "after": new_lines[c:d]}
                for tag, a, b, c, d in SequenceMatcher(
                    None, old_lines, new_lines, autojunk=False).get_opcodes() if tag != "equal"]
        try:
            old_scenes = parse_screenplay_document(old).scenes if old.strip() else ()
            new_scenes = parse_screenplay_document(new).scenes
            old_hashes = [scene.content_hash for scene in old_scenes]
            new_hashes = [scene.content_hash for scene in new_scenes]
            scene_changes = [{"operation": tag,
                              "old_scene_ids": [scene.id for scene in old_scenes[a:b]],
                              "new_scene_ids": [scene.id for scene in new_scenes[c:d]]}
                             for tag, a, b, c, d in SequenceMatcher(
                                 None, old_hashes, new_hashes, autojunk=False).get_opcodes()
                             if tag != "equal"]
            needs_reparse = (not new_scenes or bool(old.strip() and not old_scenes)
                             or any(old_hashes.count(h) > 1 or new_hashes.count(h) > 1
                                    for h in set(old_hashes + new_hashes)))
            reused = [] if needs_reparse else [
                {"old_scene_id": old_scenes[old_hashes.index(scene.content_hash)].id,
                 "new_scene_id": scene.id, "content_hash": scene.content_hash}
                for scene in new_scenes if scene.content_hash in old_hashes
            ]
            old_dialogue = [block.text for scene in old_scenes for block in scene.blocks
                            if block.kind == "dialogue"]
            new_dialogue = [block.text for scene in new_scenes for block in scene.blocks
                            if block.kind == "dialogue"]
            dialogue = [{"operation": tag, "before": old_dialogue[a:b],
                         "after": new_dialogue[c:d]}
                        for tag, a, b, c, d in SequenceMatcher(
                            None, old_dialogue, new_dialogue, autojunk=False).get_opcodes()
                        if tag != "equal"]
            available = [scene.id for scene in new_scenes] if not needs_reparse else []
            available_scenes = [
                {"id": scene.id, "heading": scene.heading.lstrip("# ").strip(),
                 "location": scene.location}
                for scene in new_scenes
            ] if not needs_reparse else []
        except (ValueError, TypeError):
            scene_changes, dialogue, reused, available, available_scenes, needs_reparse = [], [], [], [], [], True
        return {"text": text, "scenes": scene_changes, "dialogue": dialogue,
                "references": [], "reused_scenes": reused,
                "inferred_impacts": (["scene_semantics"] if scene_changes else []),
                "needs_reparse": needs_reparse, "available_scene_ids": available,
                "available_scenes": available_scenes}

    async def _fact_ack(self, db, document_id, refs, acknowledgement):
        mode = acknowledgement.get("mode")
        if mode not in {"unchecked", "checked"}:
            raise DocumentValidation("choose checked or explicitly acknowledge unchecked facts")
        if mode == "unchecked" and not str(acknowledgement.get("reason") or "").strip():
            raise DocumentValidation("unchecked facts require an explicit reason")
        rows = await (await db.execute(
            "SELECT data FROM script_consistency_runs ORDER BY created_at DESC,rowid DESC"
        )).fetchall()
        current_runs = []
        for row in rows:
            run = json.loads(row["data"])
            if (run.get("episode_document_id") != document_id or run.get("mode") != "actual"
                    or run.get("status") != "completed"):
                continue
            revisions = run.get("context_revisions") or {}
            is_current = True
            for doc_id, revision in revisions.items():
                head = await (await db.execute(
                    "SELECT current_revision_id FROM script_documents WHERE id=?", (doc_id,)
                )).fetchone()
                if head is None or head[0] != revision:
                    is_current = False
                    break
            if not is_current:
                continue
            current_runs.append(run)
        if mode == "checked":
            chosen = next((run for run in current_runs if run["id"] == acknowledgement.get("run_id")), None)
            if chosen is None or chosen.get("context_revisions") != refs:
                raise DocumentConflict("consistency check is missing or stale for these revisions")
        reasons = acknowledgement.get("issue_reasons") or {}
        if not isinstance(reasons, dict):
            raise DocumentValidation("issue reasons must be keyed by issue id")
        known = {}
        for run in current_runs:
            issues = await (await db.execute(
                "SELECT data FROM script_consistency_issues WHERE run_id=?", (run["id"],)
            )).fetchall()
            for row in issues:
                issue = json.loads(row["data"])
                if issue.get("category") != "fact" or issue.get("proposal_id"):
                    continue
                reason = str(issue.get("intentional_reason") or reasons.get(issue["id"]) or "").strip()
                if not reason:
                    raise DocumentValidation("known fact issue requires an intentional reason")
                known[issue["id"]] = {"run_id": run["id"], "reason": reason,
                                      "context_revisions": issue.get("context_revisions")}
        return {"mode": mode, "run_id": acknowledgement.get("run_id") if mode == "checked" else None,
                "reason": acknowledgement.get("reason") if mode == "unchecked" else None,
                "known_fact_issues": known}

    async def prepare(self, *, document_id, revision_id, reference_revisions,
                      selected_entity_ids, update_scope, fact_acknowledgement,
                      client_mutation_id):
        if not client_mutation_id:
            raise DocumentValidation("mutation id required")
        if not isinstance(reference_revisions, dict) or not isinstance(selected_entity_ids, list):
            raise DocumentValidation("references and selected entities required")
        if len(set(selected_entity_ids)) != len(selected_entity_ids):
            raise DocumentValidation("duplicate selected entity")
        mode = update_scope.get("mode") if isinstance(update_scope, dict) else None
        if mode not in {"none", "all", "selected"}:
            raise DocumentValidation("update scope must be none, all, or selected")
        scene_ids = update_scope.get("scene_ids") or []
        if mode == "selected" and (not isinstance(scene_ids, list) or not scene_ids or len(set(scene_ids)) != len(scene_ids)):
            raise DocumentValidation("selected scene ids required")
        if mode != "selected" and scene_ids:
            raise DocumentValidation("scene ids only apply to selected scope")
        if not isinstance(fact_acknowledgement, dict):
            raise DocumentValidation("fact acknowledgement required")
        # Selection order has no business meaning; normalize before hashing or freezing.
        selected_entity_ids = sorted(selected_entity_ids)
        if mode == "selected":
            update_scope = {**update_scope, "scene_ids": sorted(scene_ids)}
        request = dict(document_id=document_id, revision_id=revision_id,
                       reference_revisions=reference_revisions,
                       selected_entity_ids=selected_entity_ids, update_scope=update_scope,
                       fact_acknowledgement=fact_acknowledgement)
        request_hash = _digest(request)
        async with self.documents._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                replay = await (await db.execute(
                    "SELECT * FROM script_handoffs WHERE project_id=? AND prepare_mutation_id=?",
                    (self.project_id, client_mutation_id),
                )).fetchone()
                if replay:
                    if replay["prepare_hash"] != request_hash:
                        raise DocumentConflict("prepare mutation id reused with different payload")
                    await db.rollback()
                    return self._decode(replay)
                doc = await self.documents._document(db, document_id)
                if doc.kind != "episode_script" or not doc.episode_number:
                    raise DocumentValidation("handoff requires a saved episode script")
                if doc.current_revision_id != revision_id:
                    raise DocumentConflict("script revision changed", doc.current_revision_id)
                if not doc.revision.markdown.strip():
                    raise DocumentValidation("empty screenplay cannot be handed off")
                refs = {document_id: revision_id}
                frozen_references = []
                for ref_id, ref_rev in reference_revisions.items():
                    if ref_id == document_id:
                        raise DocumentValidation("episode document is implicit in references")
                    ref = await self.documents._document(db, ref_id)
                    if ref.current_revision_id != ref_rev:
                        raise DocumentConflict("reference revision changed", ref.current_revision_id)
                    refs[ref_id] = ref_rev
                    frozen_references.append({
                        "document_id": ref.id, "revision_id": ref_rev,
                        "kind": ref.kind, "title": ref.title,
                        "markdown": ref.revision.markdown,
                        "blocks": [{"id": block.id, "markdown": block.markdown}
                                   for block in ref.revision.blocks],
                    })
                entity_rows = await self.entities.snapshot(db)
                by_id = {item["entity_id"]: item for item in entity_rows}
                frozen = []
                selected_ids = set(selected_entity_ids)
                for entity_id in selected_entity_ids:
                    item = by_id.get(entity_id)
                    if not item or item["document_id"] not in reference_revisions:
                        raise DocumentValidation("selected entity missing from chosen reference documents")
                    if item["entry_missing"] or item["stale"] or item["asset_missing"]:
                        raise DocumentConflict("selected entity or asset changed")
                    for relation in item["relations"]:
                        if relation["missing"] or relation["stale"]:
                            raise DocumentConflict("selected relation is missing or stale")
                        target = by_id.get(relation["entity_id"])
                        if (target is None or target["entry_missing"] or target["stale"]
                                or target["asset_missing"]):
                            raise DocumentConflict("selected relation target changed")
                        if (target["entity_id"] not in selected_ids
                                or target["document_id"] not in reference_revisions):
                            raise DocumentValidation(
                                "selected relation requires its target entity and design document")
                    if any(a.get("status") == "written" and a.get("stale") for a in item["appearances"]):
                        raise DocumentConflict("written appearance reference is stale")
                    frozen.append(item)
                fact_ack = await self._fact_ack(db, document_id, refs, fact_acknowledgement)
                prior_source = await self._source(db, doc.episode_number)
                old_text = prior_source["raw_content"] if prior_source else ""
                diff = self._diff(old_text, doc.revision.markdown)
                if mode == "selected" and (diff["needs_reparse"] or
                        not set(scene_ids).issubset(diff["available_scene_ids"])):
                    raise DocumentValidation("selected scene ids require a reliable current parse")
                # Compare selected references only against a handoff that actually produced
                # the previous source. An imported source has no trusted selection baseline.
                prior_handoffs = await (await db.execute(
                    "SELECT data FROM script_handoffs WHERE project_id=? AND episode_number=? ORDER BY created_at DESC,rowid DESC",
                    (self.project_id, doc.episode_number),
                )).fetchall()
                previous_snapshot = None
                for prior_row in prior_handoffs:
                    prior_item = json.loads(prior_row["data"])
                    if (prior_source and prior_item.get("source_revision") == prior_source["source_revision"]
                            and prior_item.get("source_hash") == prior_source["content_hash"]):
                        previous_snapshot = prior_item["snapshot"]
                        break
                old_refs = previous_snapshot["reference_revisions"] if previous_snapshot else {}
                diff["references"] = [
                    {"document_id": ref_id, "before_revision": old_refs.get(ref_id),
                     "after_revision": reference_revisions.get(ref_id),
                     "operation": ("changed" if ref_id in old_refs and ref_id in reference_revisions
                                   else "removed" if ref_id in old_refs else "selected")}
                    for ref_id in sorted(set(old_refs) | set(reference_revisions))
                    if old_refs.get(ref_id) != reference_revisions.get(ref_id)
                ]
                old_entities = {e["entity_id"]: e for e in previous_snapshot["entities"]} if previous_snapshot else {}
                new_entities = {e["entity_id"]: e for e in frozen}
                diff["entity_references"] = [
                    {"entity_id": entity_id,
                     "before_asset_id": old_entities.get(entity_id, {}).get("asset_id"),
                     "after_asset_id": new_entities.get(entity_id, {}).get("asset_id"),
                     "operation": "changed" if entity_id in old_entities and entity_id in new_entities
                                  else "removed" if entity_id in old_entities else "selected"}
                    for entity_id in sorted(set(old_entities) | set(new_entities))
                    if old_entities.get(entity_id) != new_entities.get(entity_id)
                ]
                for reused in diff["reused_scenes"]:
                    reused.update(source_revision=prior_source["source_revision"] if prior_source else None,
                                  source_hash=prior_source["content_hash"] if prior_source else None)
                changed_scene_ids = {scene_id for change in diff["scenes"]
                                     for scene_id in change["new_scene_ids"]}
                selected_scene_ids = set(scene_ids) if mode == "selected" else set()
                diff["affected_nonupdated_scene_ids"] = sorted(
                    changed_scene_ids if mode == "none" else
                    changed_scene_ids - selected_scene_ids if mode == "selected" else set())
                stages = await (await db.execute(
                    "SELECT stage,consumed_revision,stale FROM episode_stage_revisions WHERE episode_number=?",
                    (doc.episode_number,),
                )).fetchall()
                previous_stage_revisions = {stage["stage"]: {
                    "consumed_revision": stage["consumed_revision"], "stale": bool(stage["stale"])}
                    for stage in stages}
                stamp = _now()
                snapshot = {"project_id": self.project_id, "episode_number": doc.episode_number,
                            "document_id": doc.id, "revision_id": revision_id,
                            "title": doc.title, "markdown": doc.revision.markdown,
                            "content_hash": content_sha256(doc.revision.markdown),
                            "reference_revisions": reference_revisions,
                            "references": frozen_references, "entities": frozen,
                            "fact_acknowledgement": fact_ack,
                            "fact_acknowledgement_request": fact_acknowledgement,
                            "update_scope": update_scope,
                            "previous_stage_revisions": previous_stage_revisions,
                            "previous_source": ({"revision": prior_source["source_revision"],
                                                 "hash": prior_source["content_hash"]} if prior_source else None)}
                source_project_revision = await self._project_source_revision(db)
                identity_hash = _digest({"project_id": self.project_id, "episode": doc.episode_number,
                                         "revision": revision_id, "references": reference_revisions,
                                         "entities": frozen, "scope": update_scope, "fact_ack": fact_ack,
                                         "source_project_revision": source_project_revision,
                                         "previous_source": snapshot["previous_source"]})
                existing = await (await db.execute(
                    "SELECT * FROM script_handoffs WHERE project_id=? AND identity_hash=?",
                    (self.project_id, identity_hash),
                )).fetchone()
                if existing:
                    await db.rollback()
                    return self._decode(existing)
                # An already written handoff remains the result for this exact action
                # while its own episode source is still current. Other episodes may
                # advance the project CAS without requiring another source write.
                for prior_row in prior_handoffs:
                    prior_item = json.loads(prior_row["data"])
                    prior_snap = prior_item["snapshot"]
                    if (prior_source and prior_item.get("source_revision") == prior_source["source_revision"]
                            and prior_item.get("source_hash") == prior_source["content_hash"]
                            and prior_item.get("document_id") == doc.id
                            and prior_item.get("revision_id") == revision_id
                            and prior_snap["reference_revisions"] == reference_revisions
                            and prior_snap["entities"] == frozen
                            and prior_snap["update_scope"] == update_scope
                            and prior_snap["fact_acknowledgement"] == fact_ack):
                        await db.rollback()
                        return prior_item
                item = {"id": _id(), "status": "prepared", "project_id": self.project_id,
                        "episode_number": doc.episode_number, "document_id": doc.id,
                        "revision_id": revision_id, "snapshot": snapshot, "diff": diff,
                        "expected_source_project_revision": source_project_revision,
                        "source_revision": None, "source_hash": None, "task_id": None,
                        "task_result": None, "error": None,
                        "created_at": stamp, "updated_at": stamp}
                await db.execute("INSERT INTO script_handoffs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (item["id"], self.project_id, doc.episode_number, doc.id,
                                  revision_id, identity_hash, client_mutation_id, request_hash,
                                  None, None, json.dumps(item, ensure_ascii=False), stamp, stamp))
                await db.commit()
                return item
            except BaseException:
                await db.rollback()
                raise

    async def confirm(self, handoff_id, *, expected_source_project_revision, client_mutation_id):
        if not client_mutation_id:
            raise DocumentValidation("mutation id required")
        confirm_hash = _digest({"handoff_id": handoff_id,
                                "expected_source_project_revision": expected_source_project_revision})
        async with self.documents._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await self._row(db, handoff_id)
                item = self._decode(row)
                # Replay precedes every CAS: later edits must not turn a successful replay into a new write.
                if row["confirm_mutation_id"]:
                    if row["confirm_mutation_id"] != client_mutation_id or row["confirm_hash"] != confirm_hash:
                        raise DocumentConflict("handoff already confirmed with another mutation")
                    await db.rollback()
                    return item
                if item["status"] != "prepared":
                    raise DocumentConflict("handoff cannot be confirmed")
                snap = item["snapshot"]
                doc = await self.documents._document(db, item["document_id"])
                if doc.current_revision_id != snap["revision_id"]:
                    raise DocumentConflict("script revision changed", doc.current_revision_id)
                for ref_id, revision in snap["reference_revisions"].items():
                    ref = await self.documents._document(db, ref_id)
                    if ref.current_revision_id != revision:
                        raise DocumentConflict("reference revision changed", ref.current_revision_id)
                all_entities = {e["entity_id"]: e for e in await self.entities.snapshot(db)}
                if any(all_entities.get(e["entity_id"]) != e for e in snap["entities"]):
                    raise DocumentConflict("selected entities changed")
                refs = {item["document_id"]: item["revision_id"], **snap["reference_revisions"]}
                current_ack = await self._fact_ack(
                    db, item["document_id"], refs, snap["fact_acknowledgement_request"])
                if current_ack != snap["fact_acknowledgement"]:
                    raise DocumentConflict("fact acknowledgement changed")
                prior = await self._source(db, item["episode_number"])
                expected_prior = snap["previous_source"]
                if (None if prior is None else {"revision": prior["source_revision"],
                                                "hash": prior["content_hash"]}) != expected_prior:
                    raise DocumentConflict("episode source changed")
                if expected_source_project_revision != item["expected_source_project_revision"]:
                    raise DocumentConflict("project source revision changed")
                candidate = EpisodeCandidate(source_filename=f"script-handoff-{handoff_id}.md",
                                             content=snap["markdown"], episode_number=item["episode_number"],
                                             number_source="manual", title=snap["title"],
                                             content_hash=snap["content_hash"])
                audit = ({"episode_number": item["episode_number"],
                          "status": "overwritten" if prior else "added"},)
                result = await self.sources._upsert_sources_in_transaction(
                    db, [candidate], expected_revision=expected_source_project_revision,
                    audit_id=handoff_id, audit_episodes=audit)
                source = await self._source(db, item["episode_number"])
                item.update(status="source_written", source_revision=source["source_revision"],
                            source_hash=source["content_hash"], source_project_revision=result.target_revision)
                scope = snap["update_scope"]
                envelope = {"project_id": self.project_id, "handoff_id": handoff_id,
                            "snapshot_id": handoff_id, "episode": item["episode_number"],
                            "source_revision": item["source_revision"],
                            "source_hash": item["source_hash"],
                            "reference_context": {"documents": snap["references"],
                                                  "entities": snap["entities"]},
                            "scene_ids": scope.get("scene_ids") if scope["mode"] == "selected" else None,
                            "update_scope": scope}
                stamp = _now()
                outbox_status = "pending" if scope["mode"] != "none" else "completed"
                if scope["mode"] == "none":
                    item.update(status="completed", task_result={"skipped": True})
                await db.execute("INSERT INTO script_handoff_outbox VALUES (?,?,?,?,?,?,?,?)",
                                 (handoff_id, json.dumps(envelope, ensure_ascii=False), None,
                                  outbox_status, json.dumps(item["task_result"]) if item["task_result"] else None,
                                  None, stamp, stamp))
                await db.execute("UPDATE script_documents SET adopted_revision_id=? WHERE id=?",
                                 (item["revision_id"], item["document_id"]))
                await db.execute("UPDATE script_handoffs SET confirm_mutation_id=?,confirm_hash=? WHERE id=?",
                                 (client_mutation_id, confirm_hash, handoff_id))
                await self._save(db, item)
                await db.commit()
                return item
            except EpisodeSourceRevisionConflict as exc:
                await db.rollback()
                raise DocumentConflict("project source revision changed") from exc
            except BaseException:
                await db.rollback()
                raise

    async def _set_dispatch(self, handoff_id, *, task_id=None, status=None,
                            error=None, result=None, attempt_token=None,
                            expected_task_id=None):
        async with self.documents._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await self._row(db, handoff_id)
                item = self._decode(row)
                if item["status"] == "completed":
                    await db.rollback()
                    return item
                if attempt_token is not None and item.get("dispatch_token") != attempt_token:
                    raise DocumentConflict("handoff dispatch superseded")
                if expected_task_id is not None and item.get("task_id") != expected_task_id:
                    raise DocumentConflict("handoff consumer superseded")
                if task_id and item.get("task_id") not in {None, task_id}:
                    raise DocumentConflict("handoff consumer superseded")
                if (item["status"] == "failed" and status == "dispatched"
                        and attempt_token is not None and item.get("dispatch_token") == attempt_token):
                    # The consumer can claim and fail before enqueue returns. Its
                    # terminal result wins over this attempt's late queue ACK.
                    await db.rollback()
                    return item
                item.update(status=status or item["status"], task_id=task_id or item.get("task_id"),
                            error=error, task_result=result if result is not None else item.get("task_result"))
                await db.execute("UPDATE script_handoff_outbox SET task_id=?,status=?,result=?,error=?,updated_at=? WHERE handoff_id=?",
                                 (item["task_id"], item["status"],
                                  json.dumps(item["task_result"], ensure_ascii=False) if item["task_result"] else None,
                                  error, _now(), handoff_id))
                await self._save(db, item)
                await db.commit()
                return item
            except BaseException:
                await db.rollback()
                raise

    async def _begin_dispatch(self, handoff_id, *, known_terminal=False):
        """Durably reserve a new attempt before a queue may execute inline."""
        async with self.documents._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await self._row(db, handoff_id)
                item = self._decode(row)
                if item["status"] in {"prepared", "completed", "needs_rebase"}:
                    await db.rollback()
                    return item, None
                source = await self._source(db, item["episode_number"])
                if (not source or source["source_revision"] != item["source_revision"]
                        or source["content_hash"] != item["source_hash"]):
                    item.update(status="needs_rebase", error="source changed")
                    await self._save(db, item)
                    await db.execute("UPDATE script_handoff_outbox SET status='needs_rebase',error=?,updated_at=? WHERE handoff_id=?",
                                     (item["error"], _now(), handoff_id))
                    await db.commit()
                    return item, None
                outbox = await (await db.execute(
                    "SELECT envelope FROM script_handoff_outbox WHERE handoff_id=?", (handoff_id,)
                )).fetchone()
                if outbox is None:
                    raise DocumentConflict("handoff outbox missing")
                envelope = json.loads(outbox["envelope"])
                if item["status"] == "dispatching" and item.get("dispatch_token") and not item.get("task_id"):
                    # A second caller can enqueue the same durable envelope; the task
                    # backend reservation deduplicates it, and both use one claim token.
                    await db.rollback()
                    return item, envelope
                if item["status"] == "dispatched" and item.get("task_id") and not known_terminal:
                    age = (datetime.now(timezone.utc) -
                           datetime.fromisoformat(item["updated_at"])).total_seconds()
                    if age < 60:
                        # Missing queue state may be a visibility gap during inline
                        # enqueue. A recent claimed consumer must retain ownership.
                        await db.rollback()
                        return item, None
                token = _id()
                item.update(status="dispatching", task_id=None, dispatch_token=token, error=None)
                envelope["dispatch_token"] = token
                await db.execute("UPDATE script_handoff_outbox SET envelope=?,task_id=NULL,status='dispatching',error=NULL,updated_at=? WHERE handoff_id=?",
                                 (json.dumps(envelope, ensure_ascii=False), _now(), handoff_id))
                await self._save(db, item)
                await db.commit()
                return item, envelope
            except BaseException:
                await db.rollback()
                raise

    async def dispatch(self, handoff_id, ctx):
        item = await self.get(handoff_id)
        if str(ctx.project_id) != self.project_id:
            raise DocumentNotFound("handoff not found in this project")
        if item["status"] in {"prepared", "completed", "needs_rebase"}:
            return item
        from novelvideo.task_identity import project_task_state_key
        scope = f"handoff:{handoff_id}"
        task = get_task_manager().get_task_for_project(
            ctx, "screenplay_semantics", item["episode_number"], scope=scope)
        persisted_result = item.get("task_result")
        queue_result = task.result if task else None
        failed_terminal_result = bool(
            task and task.status == "completed" and item["status"] == "failed"
            and item.get("task_id") == task.task_id
            and isinstance(persisted_result, dict) and isinstance(queue_result, dict)
            and persisted_result.get("semantic_revision_id")
            and persisted_result["semantic_revision_id"] == queue_result.get("semantic_revision_id")
            and _semantic_extraction_failure(persisted_result)
        )
        if (task and task.status == "completed" and not failed_terminal_result
                and isinstance(task.result, dict) and task.result.get("semantic_revision_id")):
            if item.get("task_id") is None:
                item = await self._set_dispatch(handoff_id, task_id=task.task_id, status="dispatched",
                                                attempt_token=item.get("dispatch_token"))
            return await self.complete(handoff_id, task_id=task.task_id, result=task.result)
        if task and task.status in {"submitting", "queued", "running"}:
            return await self._set_dispatch(handoff_id, task_id=task.task_id, status="dispatched",
                                            attempt_token=item.get("dispatch_token"))
        if task and task.status == "completed" and not failed_terminal_result:
            return await self._set_dispatch(
                handoff_id, status="failed", error="completed task result unavailable",
                expected_task_id=item.get("task_id"))
        item, envelope = await self._begin_dispatch(
            handoff_id, known_terminal=bool(task and task.status in {"failed", "cancelled", "interrupted"}))
        if envelope is None:
            return item
        token = envelope["dispatch_token"]
        try:
            queued = await enqueue_project_task(
                ctx, task_type="screenplay_semantics", queue_kind="default",
                episode=item["episode_number"], scope=scope, payload=envelope)
        except Exception as exc:
            return await self._set_dispatch(handoff_id, status="failed", error=str(exc),
                                            attempt_token=token)
        result = await self._set_dispatch(handoff_id, task_id=queued.task_state.task_id,
                                          status="dispatched", attempt_token=token)
        result["task_key"] = project_task_state_key(
            "screenplay_semantics", self.project_id, item["episode_number"], scope=scope)
        return result

    async def retry(self, handoff_id, ctx):
        if str(ctx.project_id) != self.project_id:
            raise DocumentNotFound("handoff not found in this project")
        # Repair the status of revisions archived by older builds which treated a
        # failed extraction as a successful handoff. Preserve their source/result.
        async with self.documents._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                item = self._decode(await self._row(db, handoff_id))
                failure = _semantic_extraction_failure(item.get("task_result"))
                if item["status"] == "completed" and failure:
                    item.update(status="failed", error=failure)
                    await db.execute("UPDATE script_handoff_outbox SET status='failed',error=?,updated_at=? WHERE handoff_id=?",
                                     (failure, _now(), handoff_id))
                    await self._save(db, item)
                    await db.commit()
                else:
                    await db.rollback()
            except BaseException:
                await db.rollback()
                raise
        return await self.dispatch(handoff_id, ctx)

    async def claim(self, handoff_id, *, task_id, dispatch_token):
        """Pin the current consumer before it runs; an old lease cannot publish."""
        async with self.documents._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await self._row(db, handoff_id)
                item = self._decode(row)
                if (item["status"] == "completed" or item.get("dispatch_token") != dispatch_token
                        or (item["task_id"] and item["task_id"] != task_id)):
                    raise DocumentConflict("handoff consumer superseded")
                source = await self._source(db, item["episode_number"])
                if (not source or source["source_revision"] != item["source_revision"]
                        or source["content_hash"] != item["source_hash"]):
                    raise DocumentConflict("handoff source superseded")
                item.update(task_id=task_id, status="dispatched", error=None)
                await db.execute("UPDATE script_handoff_outbox SET task_id=?,status='dispatched',updated_at=? WHERE handoff_id=?",
                                 (task_id, _now(), handoff_id))
                await self._save(db, item)
                await db.commit()
                return item
            except BaseException:
                await db.rollback()
                raise

    async def complete(self, handoff_id, *, task_id, result):
        async with self.documents._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await self._row(db, handoff_id)
                item = self._decode(row)
                if item["status"] == "completed" or (item["status"] == "failed" and item.get("task_result") == result):
                    await db.rollback()
                    return item
                if item["task_id"] != task_id:
                    raise DocumentConflict("handoff consumer superseded")
                source = await self._source(db, item["episode_number"])
                if (not source or source["source_revision"] != item["source_revision"]
                        or source["content_hash"] != item["source_hash"]):
                    raise DocumentConflict("handoff source superseded")
                failure = _semantic_extraction_failure(result)
                status = "failed" if failure else "completed"
                item.update(status=status, task_result=result, error=failure)
                await db.execute("UPDATE script_handoff_outbox SET status=?,result=?,error=?,updated_at=? WHERE handoff_id=?",
                                 (status, json.dumps(result, ensure_ascii=False), failure, _now(), handoff_id))
                await self._save(db, item)
                await db.commit()
                return item
            except BaseException:
                await db.rollback()
                raise

    def save_semantic_if_current(self, revision, *, task_id, dispatch_token, semantic_store):
        """Archive a semantic result only while source and consumer still match.

        The same SQLite writer lock serializes this check with source adoption and
        another dispatch claim. This does not activate or promote a semantic result.
        """
        connection = sqlite3.connect(self.documents.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT data FROM script_handoffs WHERE project_id=? AND episode_number=? "
                "AND json_extract(data,'$.dispatch_token')=?",
                (self.project_id, revision.episode, dispatch_token),
            ).fetchone()
            if row is None:
                raise DocumentConflict("handoff consumer superseded")
            item = json.loads(row["data"])
            if (item.get("task_id") != task_id or item.get("dispatch_token") != dispatch_token
                    or item["status"] != "dispatched"
                    or revision.source_revision != item["source_revision"]
                    or revision.source_hash != item["source_hash"]):
                raise DocumentConflict("handoff consumer superseded")
            source = connection.execute(
                "SELECT source_revision,content_hash FROM episode_sources WHERE episode_number=?",
                (revision.episode,),
            ).fetchone()
            if (source is None or source["source_revision"] != item["source_revision"]
                    or source["content_hash"] != item["source_hash"]):
                raise DocumentConflict("handoff source superseded")
            saved = semantic_store.save(revision)
            connection.commit()
            return saved
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def fail(self, handoff_id, *, task_id, error):
        return await self._set_dispatch(handoff_id, status="failed", error=str(error),
                                        expected_task_id=task_id)


__all__ = ["HandoffService"]
