"""Revision-bound screenplay proposals and atomic review decisions."""
from __future__ import annotations

import json
from dataclasses import replace

from .store import (DocumentConflict, DocumentNotFound, DocumentValidation, DocumentStore,
                    _as_json_blocks, _blocks, _digest, _id, _now)


def _proposal(row):
    value = dict(row)
    value["start"] = value.pop("start_offset")
    value["end"] = value.pop("end_offset")
    value["before"] = value.pop("before_text")
    value["after"] = value.pop("after_text")
    value["dependencies"] = json.loads(value["dependencies"])
    value["context_revisions"] = json.loads(value["context_revisions"])
    return value


async def consistency_issue_for_proposal(db, proposal_id: str, document_id: str):
    """Follow rewrite refinements back to their selected consistency issue."""
    seen = set()
    while proposal_id:
        if proposal_id in seen:
            raise DocumentConflict("proposal reference cycle")
        seen.add(proposal_id)
        proposal = await (await db.execute(
            "SELECT document_id,round_id FROM script_proposals WHERE id=?", (proposal_id,))).fetchone()
        if proposal is None or proposal["document_id"] != document_id:
            raise DocumentConflict("proposal reference changed")
        linked = await (await db.execute(
            "SELECT i.data FROM script_consistency_target_jobs j "
            "JOIN script_consistency_issues i ON i.id=j.issue_id "
            "WHERE j.rewrite_job_id=? AND j.document_id=?",
            (proposal["round_id"], document_id))).fetchone()
        if linked:
            return json.loads(linked["data"])
        job_row = await (await db.execute(
            "SELECT data FROM script_rewrite_jobs WHERE id=?", (proposal["round_id"],))).fetchone()
        if job_row is None:
            return None
        job = json.loads(job_row["data"])
        if job["proposal_id"] != proposal_id or job["document_id"] != document_id:
            raise DocumentConflict("proposal rewrite origin changed")
        proposal_id = job.get("reference_proposal_id")
    return None


class ProposalService:
    def __init__(self, store: DocumentStore):
        self.store = store

    async def list(self, document_id: str):
        async with self.store._db() as db:
            await self.store._document(db, document_id)
            rows = await (await db.execute("SELECT * FROM script_proposals WHERE document_id=? ORDER BY created_at,rowid", (document_id,))).fetchall()
            return [_proposal(row) for row in rows]

    async def get(self, proposal_id: str):
        async with self.store._db() as db:
            row = await (await db.execute("SELECT * FROM script_proposals WHERE id=?", (proposal_id,))).fetchone()
            if row is None:
                raise DocumentNotFound("proposal not found")
            return _proposal(row)

    async def create(self, *, document_id, base_revision_id, block_id, start, end,
                     before, after, reason, round_id, client_mutation_id,
                     dependencies=None, context_revisions=None, source_candidate_id=None):
        if not client_mutation_id or not round_id or not reason.strip():
            raise DocumentValidation("mutation, round and reason required")
        context_revisions = context_revisions or {}
        dependencies = dependencies or []
        async with self.store._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = await (await db.execute("SELECT * FROM script_proposals WHERE id=?", (client_mutation_id,))).fetchone()
                if existing:
                    result = _proposal(existing)
                    if any(result[key] != value for key, value in {
                        "document_id": document_id, "base_revision_id": base_revision_id,
                        "block_id": block_id, "start": start, "end": end, "before": before,
                        "after": after, "reason": reason, "round_id": round_id,
                        "dependencies": dependencies, "context_revisions": context_revisions}.items()):
                        raise DocumentConflict("proposal mutation id reused")
                    await db.rollback()
                    return result
                doc = await self.store._document(db, document_id)
                if doc.current_revision_id != base_revision_id:
                    raise DocumentConflict("proposal baseline changed", doc.current_revision_id)
                block = next((b for b in doc.revision.blocks if b.id == block_id), None)
                valid_range = (start == 0 and end == len(doc.revision.markdown) and
                               before == doc.revision.markdown) if block_id is None else (
                               block is not None and 0 <= start <= end <= len(block.markdown) and
                               block.markdown[start:end] == before)
                if not valid_range:
                    raise DocumentConflict("proposal range changed", doc.current_revision_id)
                for ref_id, revision_id in context_revisions.items():
                    ref = await self.store._document(db, ref_id)
                    if ref.current_revision_id != revision_id:
                        raise DocumentConflict("proposal context changed", ref.current_revision_id)
                stamp = _now()
                await db.execute("INSERT INTO script_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (client_mutation_id, document_id, base_revision_id, block_id, start, end,
                     before, after, reason, json.dumps(dependencies),
                     json.dumps(context_revisions), round_id, "pending", source_candidate_id, stamp))
                row = await (await db.execute("SELECT * FROM script_proposals WHERE id=?", (client_mutation_id,))).fetchone()
                await db.commit()
                return _proposal(row)
            except Exception:
                await db.rollback()
                raise

    async def discard(self, proposal_id: str):
        async with self.store._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (await db.execute("SELECT * FROM script_proposals WHERE id=?", (proposal_id,))).fetchone()
                if row is None:
                    raise DocumentNotFound("proposal not found")
                if row["status"] == "adopted":
                    raise DocumentConflict("adopted proposal cannot be discarded")
                if row["status"] == "pending":
                    await db.execute("UPDATE script_proposals SET status='discarded' WHERE id=?", (proposal_id,))
                row = await (await db.execute("SELECT * FROM script_proposals WHERE id=?", (proposal_id,))).fetchone()
                await db.commit()
                return _proposal(row)
            except Exception:
                await db.rollback()
                raise

    async def accept(self, proposal_ids: list[str], *, base_revision_id: str, client_mutation_id: str):
        if not proposal_ids or len(set(proposal_ids)) != len(proposal_ids) or not client_mutation_id:
            raise DocumentValidation("unique proposal ids and mutation id required")
        digest = _digest({"proposal_ids": sorted(proposal_ids), "base_revision_id": base_revision_id})
        async with self.store._db() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                rows = []
                for proposal_id in proposal_ids:
                    row = await (await db.execute("SELECT * FROM script_proposals WHERE id=?", (proposal_id,))).fetchone()
                    if row is None:
                        raise DocumentNotFound("proposal not found")
                    rows.append(_proposal(row))
                doc_ids = {p["document_id"] for p in rows}
                rounds = {p["round_id"] for p in rows}
                if len(doc_ids) != 1 or len(rounds) != 1:
                    raise DocumentConflict("batch must use one document and round")
                document_id = rows[0]["document_id"]
                replay = await self.store._replay(db, "proposal:" + document_id, client_mutation_id, digest)
                if replay:
                    await db.rollback()
                    return replay
                doc = await self.store._document(db, document_id)
                if doc.current_revision_id != base_revision_id or len({p["base_revision_id"] for p in rows}) != 1:
                    raise DocumentConflict("proposal baseline changed", doc.current_revision_id)
                if any(p["status"] != "pending" for p in rows):
                    raise DocumentConflict("proposal already reviewed", doc.current_revision_id)
                original_base = rows[0]["base_revision_id"]
                prior = []
                cursor = doc.revision
                while cursor.id != original_base:
                    adopted = await (await db.execute(
                        "SELECT p.* FROM script_proposal_adoptions a JOIN script_proposals p ON p.id=a.proposal_id WHERE a.revision_id=?",
                        (cursor.id,))).fetchall()
                    if not adopted or any(p["document_id"] != document_id or p["round_id"] != rows[0]["round_id"]
                                          or p["base_revision_id"] != original_base for p in adopted):
                        raise DocumentConflict("proposal baseline changed", doc.current_revision_id)
                    prior.append([_proposal(item) for item in adopted])
                    if not cursor.parent_revision_id:
                        raise DocumentConflict("proposal baseline changed", doc.current_revision_id)
                    cursor = await self.store._revision(db, cursor.parent_revision_id)
                prior = [item for group in reversed(prior) for item in group]
                ids = set(proposal_ids)
                pending_rows = await (await db.execute(
                    "SELECT * FROM script_proposals WHERE document_id=? AND round_id=? AND status='pending'",
                    (document_id, rows[0]["round_id"]))).fetchall()
                pending = [_proposal(item) for item in pending_rows]
                for left in rows:
                    if not set(left["dependencies"]).issubset(ids):
                        raise DocumentConflict("dependent proposals must be reviewed together", doc.current_revision_id)
                    for right in pending:
                        if right["id"] == left["id"]:
                            continue
                        if left["id"] in right["dependencies"] and right["id"] not in ids:
                            raise DocumentConflict("dependent proposals must be reviewed together", doc.current_revision_id)
                        if left["block_id"] is not None and left["block_id"] == right["block_id"] and (
                            max(left["start"], right["start"]) < min(left["end"], right["end"]) or
                            left["start"] == left["end"] == right["start"] == right["end"]):
                            raise DocumentConflict("overlapping proposals require whole-range regeneration", doc.current_revision_id)
                for p in rows:
                    for ref_id, revision_id in p["context_revisions"].items():
                        ref = await self.store._document(db, ref_id)
                        if ref.current_revision_id != revision_id:
                            raise DocumentConflict("proposal context changed", ref.current_revision_id)
                for p in rows:
                    linked_issue = await consistency_issue_for_proposal(db, p["id"], document_id)
                    if linked_issue:
                        for ref_id, revision_id in linked_issue["context_revisions"].items():
                            ref = await self.store._document(db, ref_id)
                            if ref.current_revision_id != revision_id:
                                raise DocumentConflict("consistency issue context changed", ref.current_revision_id)
                        source_id = linked_issue.get("proposal_id")
                        if source_id:
                            source = await (await db.execute(
                                "SELECT status FROM script_proposals WHERE id=?", (source_id,))).fetchone()
                            if source is None or source["status"] != "pending":
                                raise DocumentConflict("hypothetical source candidate changed", doc.current_revision_id)
                block_map = {b.id: b for b in doc.revision.blocks}
                whole = [p for p in rows if p["block_id"] is None]
                if whole:
                    if len(rows) != 1 or prior or whole[0]["before"] != doc.revision.markdown:
                        raise DocumentConflict("whole document proposal changed", doc.current_revision_id)
                    markdown = whole[0]["after"]
                    blocks = _blocks(markdown, previous=doc.revision.blocks)
                else:
                    by_block = {}
                    for p in rows:
                        start, end = p["start"], p["end"]
                        for adopted in prior:
                            if adopted["block_id"] != p["block_id"]:
                                continue
                            if adopted["end"] <= p["start"]:
                                delta = len(adopted["after"]) - len(adopted["before"])
                                start += delta
                                end += delta
                            elif adopted["start"] < p["end"]:
                                raise DocumentConflict("proposal range changed", doc.current_revision_id)
                        block = block_map.get(p["block_id"])
                        if block is None or block.markdown[start:end] != p["before"]:
                            raise DocumentConflict("proposal range changed", doc.current_revision_id)
                        by_block.setdefault(p["block_id"], []).append((start, end, p["after"]))
                    for group in by_block.values():
                        ordered = sorted(group)
                        if any(left[1] > right[0] or (left[0] == left[1] == right[0] == right[1])
                               for left, right in zip(ordered, ordered[1:])):
                            raise DocumentConflict("overlapping proposals require whole-range regeneration", doc.current_revision_id)
                    rendered = []
                    for block in doc.revision.blocks:
                        text = block.markdown
                        for start, end, after in sorted(by_block.get(block.id, []), reverse=True):
                            text = text[:start] + after + text[end:]
                        rendered.append({"id": block.id, "markdown": text})
                    markdown = "".join(item["markdown"] for item in rendered)
                    blocks = _blocks(markdown, rendered, doc.revision.blocks)
                revision_id, stamp = _id(), _now()
                await db.execute("INSERT INTO script_revisions VALUES (?,?,?,?,?,?,?,?)",
                    (revision_id, document_id, base_revision_id, markdown,
                     json.dumps(_as_json_blocks(blocks), ensure_ascii=False), client_mutation_id, stamp, None))
                await db.execute("UPDATE script_documents SET current_revision_id=?,updated_at=? WHERE id=?",
                                 (revision_id, stamp, document_id))
                for proposal_id in proposal_ids:
                    await db.execute("UPDATE script_proposals SET status='adopted' WHERE id=?", (proposal_id,))
                    await db.execute("INSERT INTO script_proposal_adoptions VALUES (?,?)", (proposal_id, revision_id))
                await db.execute("INSERT INTO script_mutations VALUES (?,?,?,?,?)",
                    ("proposal:" + document_id, client_mutation_id, digest, document_id, revision_id))
                result = await self.store._document(db, document_id)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    async def from_candidate(self, candidate_id: str):
        async with self.store._db() as db:
            row = await (await db.execute("SELECT * FROM script_generation_candidates WHERE id=?", (candidate_id,))).fetchone()
            if row is None:
                raise DocumentNotFound("candidate not found")
            if row["target_document_id"] is None or row["target_revision_id"] is None:
                raise DocumentValidation("candidate has no target")
            base = await self.store._revision(db, row["target_revision_id"])
            existing = await (await db.execute("SELECT * FROM script_proposals WHERE source_candidate_id=?", (candidate_id,))).fetchone()
            if existing:
                return _proposal(existing)
            data = dict(row)
        return await self.create(document_id=data["target_document_id"],
            base_revision_id=data["target_revision_id"], block_id=None,
            start=0, end=len(base.markdown), before=base.markdown,
            after=data["markdown"], reason="整文生成候选", round_id="candidate:" + candidate_id,
            client_mutation_id="candidate:" + candidate_id,
            context_revisions=json.loads(data["context_revisions"]), source_candidate_id=candidate_id)
