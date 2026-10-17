"""Project-local transactional run journal; no credentials are persisted."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from novelvideo.freezone.canvas_store import utc_now_iso
from .graph import execution_order
from .models import OrchestrationError, fingerprint, input_fingerprint

TERMINAL = {"succeeded", "failed", "cancelled"}


def new_attempt(node_id: str, number: int) -> dict:
    return {"id": uuid4().hex, "node_id": node_id, "number": number, "status": "pending",
            "submission": None, "result": None, "artifacts": [], "error": None}


class RunStore:
    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.root = self.project_dir / "freezone" / "orchestration"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "runs.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, canvas_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(canvas_id, request_id));
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS node_actions (
                    canvas_id TEXT NOT NULL, root_delegation_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(canvas_id, root_delegation_id, request_id));
                CREATE TABLE IF NOT EXISTS node_run_reservations (
                    root_delegation_id TEXT NOT NULL, reservation_id TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    delegation_id TEXT NOT NULL DEFAULT '',
                    root_limit INTEGER,
                    delegation_limit INTEGER,
                    PRIMARY KEY(root_delegation_id, reservation_id));
                CREATE TABLE IF NOT EXISTS node_run_budget_entries (
                    root_delegation_id TEXT NOT NULL, reservation_id TEXT NOT NULL,
                    delegation_id TEXT NOT NULL, status TEXT NOT NULL,
                    max_node_runs INTEGER,
                    PRIMARY KEY(root_delegation_id, reservation_id, delegation_id));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(node_run_reservations)")}
            for name, definition in (("delegation_id", "TEXT NOT NULL DEFAULT ''"),
                                     ("root_limit", "INTEGER"),
                                     ("delegation_limit", "INTEGER")):
                if name not in columns:
                    db.execute(f"ALTER TABLE node_run_reservations ADD COLUMN {name} {definition}")
            # Preserve accounting from reservations created before ancestor
            # budgets existed. New reservations always write every ancestor.
            for row in db.execute("SELECT * FROM node_run_reservations").fetchall():
                db.execute("INSERT OR IGNORE INTO node_run_budget_entries(root_delegation_id,reservation_id,delegation_id,status,max_node_runs) VALUES(?,?,?,?,?)",
                           (row["root_delegation_id"], row["reservation_id"],
                            row["root_delegation_id"], row["status"], row["root_limit"]))
                delegation_id = row["delegation_id"] or row["root_delegation_id"]
                if delegation_id != row["root_delegation_id"]:
                    db.execute("INSERT OR IGNORE INTO node_run_budget_entries(root_delegation_id,reservation_id,delegation_id,status,max_node_runs) VALUES(?,?,?,?,?)",
                               (row["root_delegation_id"], row["reservation_id"], delegation_id,
                                row["status"], row["delegation_limit"]))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, canvas: dict, *, target: str, request_id: str, project_id: str,
               settings: dict, actor: dict, operation: str | None = None,
               target_only: bool = False, agent_execution: dict | None = None) -> dict:
        if not request_id.strip():
            raise OrchestrationError("request_id_required", "A stable request ID is required")
        dependency_order = execution_order(canvas["nodes"], canvas["edges"], target,
                                           operation=operation)
        if target_only and not agent_execution:
            raise OrchestrationError("invalid_agent_run", "Target-only execution requires a trusted agent marker")
        order = [target] if target_only else dependency_order
        snapshot = deepcopy(canvas)
        if operation:
            target_node = next(n for n in snapshot["nodes"] if n["id"] == target)
            target_node["data"]["operation"] = operation
            if agent_execution:
                # A direct agent operation is explicit even when the canvas node
                # was previously saved as a fixed/reference-only source.
                target_node['data']['referenceOnly'] = False
            elif operation == 'generate' and target_node['type'] in {'imageGenNode', 'imageNode', 'storyboardGenNode'}:
                target_node['data']['referenceOnly'] = False
        request_hash = (fingerprint([snapshot, target, settings, operation, actor.get("id")])
                        if agent_execution is None and not target_only else
                        fingerprint([snapshot, target, settings, operation, actor.get("id"),
                                     agent_execution, target_only]))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM runs WHERE canvas_id=? AND request_id=?",
                                  (canvas["canvas_id"], request_id)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise OrchestrationError("idempotency_conflict", "Request ID reused for a different snapshot")
                return json.loads(existing["payload"])
            active = db.execute("SELECT id,payload FROM runs WHERE canvas_id=? AND status NOT IN ('succeeded','failed','cancelled')",
                                (canvas["canvas_id"],)).fetchall()
            for running in active:
                if set(order).intersection(json.loads(running['payload'])['order']):
                    raise OrchestrationError("canvas_busy", "These nodes have an active run", run_id=running["id"])
            run = {"schema_version": 1, "id": uuid4().hex, "project_id": project_id,
                   "canvas_id": canvas["canvas_id"], "target": target, "request_id": request_id,
                   "snapshot": snapshot, "settings": deepcopy(settings),
                   "actor": {key: actor[key] for key in ("id", "username") if key in actor},
                   "order": order, "status": "pending", "cancel_requested": False,
                   "attempts": {key: [new_attempt(key, 1)] for key in order},
                   # Projection guards are against the live saved inputs. The
                   # execution snapshot may contain an explicitly selected op
                   # (or referenceOnly override) that is not yet persisted.
                   "input_fingerprints": {node["id"]: input_fingerprint(node)
                                           for node in canvas["nodes"]},
                   "retry_requests": [], "created_at": utc_now_iso(), "updated_at": utc_now_iso()}
            if agent_execution:
                run["agent_execution"] = deepcopy(agent_execution)
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (run["id"], run["canvas_id"], request_id,
                                                               request_hash, run["status"], json.dumps(run)))
            self._event(db, run, "created")
            return run

    def get(self, run_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise OrchestrationError("run_not_found", "Run does not exist")
        return json.loads(row["payload"])

    def find_request(self, canvas_id: str, request_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM runs WHERE canvas_id=? AND request_id=?", (canvas_id, request_id)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_runs(self, canvas_id: str) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM runs WHERE canvas_id=? ORDER BY rowid DESC LIMIT 50", (canvas_id,))]

    def list_active(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM runs WHERE status NOT IN ('succeeded','failed','cancelled')")]

    def save(self, run: dict, event: str) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT payload FROM runs WHERE id=?", (run["id"],)).fetchone()
            if current is None:
                raise OrchestrationError("run_not_found", "Run does not exist")
            # HTTP cancellation may arrive while a tick awaits the task backend.
            if json.loads(current["payload"])["cancel_requested"]:
                run["cancel_requested"] = True
            self._write(db, run, event)
        return run

    @contextmanager
    def adoption_fence(self, run_id: str):
        """Serialize agent adoption with stop requests through canvas projection."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise OrchestrationError("run_not_found", "Run does not exist")
            yield db, json.loads(row["payload"])

    def _write(self, db, run, event):
        run["updated_at"] = utc_now_iso()
        db.execute("UPDATE runs SET status=?,payload=? WHERE id=?", (run["status"], json.dumps(run), run["id"]))
        self._event(db, run, event)

    @staticmethod
    def _event(db, run, event):
        payload = {"event": event, "status": run["status"],
                   "nodes": {key: attempts[-1]["status"] for key, attempts in run["attempts"].items()}}
        db.execute("INSERT INTO events(run_id,created_at,payload) VALUES(?,?,?)",
                   (run["id"], utc_now_iso(), json.dumps(payload)))

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        self.get(run_id)
        with self.connection() as db:
            return [{"sequence": row["sequence"], "created_at": row["created_at"], **json.loads(row["payload"])}
                    for row in db.execute("SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 1000", (run_id, after))]

    def get_node_action(self, canvas_id: str, root_delegation_id: str, request_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM node_actions WHERE canvas_id=? AND root_delegation_id=? AND request_id=?",
                             (canvas_id, root_delegation_id, request_id)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def prepare_node_action(self, canvas_id: str, root_delegation_id: str, request_id: str,
                            request_hash: str, payload: dict) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM node_actions WHERE canvas_id=? AND root_delegation_id=? AND request_id=?",
                             (canvas_id, root_delegation_id, request_id)).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise OrchestrationError("idempotency_conflict", "Request ID reused for a different node action")
                return {**dict(row), "payload": json.loads(row["payload"])}
            db.execute("INSERT INTO node_actions VALUES(?,?,?,?,?,?)",
                       (canvas_id, root_delegation_id, request_id, request_hash, "prepared", json.dumps(payload)))
            return {"canvas_id": canvas_id, "root_delegation_id": root_delegation_id,
                    "request_id": request_id, "request_hash": request_hash,
                    "status": "prepared", "payload": deepcopy(payload)}

    def complete_node_action(self, canvas_id: str, root_delegation_id: str, request_id: str,
                             request_hash: str, receipt: dict) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT request_hash,status,payload FROM node_actions WHERE canvas_id=? AND root_delegation_id=? AND request_id=?",
                             (canvas_id, root_delegation_id, request_id)).fetchone()
            if row is None or row["request_hash"] != request_hash:
                raise OrchestrationError("idempotency_conflict", "Node action journal does not match request")
            if row["status"] == "complete":
                return json.loads(row["payload"]).get("receipt", receipt)
            payload = json.loads(row["payload"])
            payload["receipt"] = deepcopy(receipt)
            db.execute("UPDATE node_actions SET status='complete',payload=? WHERE canvas_id=? AND root_delegation_id=? AND request_id=?",
                       (json.dumps(payload), canvas_id, root_delegation_id, request_id))
        return receipt

    def reserve_node_run(self, root_delegation_id: str, reservation_id: str, limit: int | None = None,
                         *, delegation_id: str | None = None, root_limit: int | None = None,
                         delegation_limit: int | None = None,
                         budget_chain: list[dict] | None = None) -> bool:
        """Atomically reserve an external attempt against every delegation ancestor."""
        if root_limit is None:
            root_limit = limit
        if delegation_limit is None:
            delegation_limit = root_limit
        delegation_id = delegation_id or ""
        if budget_chain is None:
            budget_chain = [{"delegation_id": root_delegation_id, "max_node_runs": root_limit}]
            if delegation_id and delegation_id != root_delegation_id:
                budget_chain.append({"delegation_id": delegation_id, "max_node_runs": delegation_limit})
        if (not isinstance(budget_chain, list) or not budget_chain
                or any(not isinstance(entry, dict) for entry in budget_chain)
                or budget_chain[0].get("delegation_id") != root_delegation_id
                or (delegation_id and budget_chain[-1].get("delegation_id") != delegation_id)):
            raise OrchestrationError("node_run_budget_invalid", "Node run budget chain is invalid")
        budget_ids = [entry.get("delegation_id") for entry in budget_chain if isinstance(entry, dict)]
        if len(budget_ids) != len(budget_chain) or len(set(budget_ids)) != len(budget_ids):
            raise OrchestrationError("node_run_budget_invalid", "Node run budget chain is invalid")
        for entry in budget_chain:
            if (not isinstance(entry.get("delegation_id"), str) or not entry["delegation_id"]
                    or type(entry.get("max_node_runs")) is not int or entry["max_node_runs"] < 1):
                raise OrchestrationError("node_run_budget_exhausted", "No node run allowance remains")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT delegation_id,status,max_node_runs FROM node_run_budget_entries WHERE root_delegation_id=? AND reservation_id=?",
                              (root_delegation_id, reservation_id)).fetchall()
            if rows:
                old_chain = {row["delegation_id"]: row["max_node_runs"] for row in rows}
                new_chain = {entry["delegation_id"]: entry["max_node_runs"] for entry in budget_chain}
                if old_chain != new_chain:
                    raise OrchestrationError("node_run_budget_conflict", "Attempt budget chain changed")
                if any(row["status"] in {"reserved", "consumed"} for row in rows):
                    return False
            for entry in budget_chain:
                used = db.execute("SELECT COUNT(*) FROM node_run_budget_entries WHERE root_delegation_id=? AND delegation_id=? AND status IN ('reserved','consumed')",
                                  (root_delegation_id, entry["delegation_id"])).fetchone()[0]
                if used >= entry["max_node_runs"]:
                    raise OrchestrationError("node_run_budget_exhausted", "Node run allowance is exhausted",
                                             delegation_id=entry["delegation_id"],
                                             limit=entry["max_node_runs"], used=used)
            now = utc_now_iso()
            for entry in budget_chain:
                db.execute("INSERT INTO node_run_budget_entries(root_delegation_id,reservation_id,delegation_id,status,max_node_runs) VALUES(?,?,?,?,?) "
                           "ON CONFLICT(root_delegation_id,reservation_id,delegation_id) DO UPDATE SET status='reserved',max_node_runs=excluded.max_node_runs",
                           (root_delegation_id, reservation_id, entry["delegation_id"], "reserved",
                            entry["max_node_runs"]))
            current_limit = budget_chain[-1]["max_node_runs"]
            db.execute("INSERT INTO node_run_reservations(root_delegation_id,reservation_id,status,created_at,delegation_id,root_limit,delegation_limit) VALUES(?,?,?,?,?,?,?) "
                       "ON CONFLICT(root_delegation_id,reservation_id) DO UPDATE SET status='reserved',delegation_id=excluded.delegation_id,root_limit=excluded.root_limit,delegation_limit=excluded.delegation_limit",
                       (root_delegation_id, reservation_id, "reserved", now, budget_chain[-1]["delegation_id"],
                        budget_chain[0]["max_node_runs"], current_limit))
        return True

    def consume_node_run(self, root_delegation_id: str, reservation_id: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE node_run_reservations SET status='consumed' WHERE root_delegation_id=? AND reservation_id=? AND status='reserved'",
                       (root_delegation_id, reservation_id))
            db.execute("UPDATE node_run_budget_entries SET status='consumed' WHERE root_delegation_id=? AND reservation_id=? AND status='reserved'",
                       (root_delegation_id, reservation_id))

    def release_node_run(self, root_delegation_id: str, reservation_id: str, *,
                         definitive_not_submitted: bool = False) -> None:
        # Consumed reservations count as actual attempts unless an adapter
        # provides definitive evidence that the provider accepted no request.
        allowed = "('reserved','consumed')" if definitive_not_submitted else "('reserved')"
        with self.connection() as db:
            db.execute(f"UPDATE node_run_reservations SET status='released' WHERE root_delegation_id=? AND reservation_id=? AND status IN {allowed}",
                       (root_delegation_id, reservation_id))
            db.execute(f"UPDATE node_run_budget_entries SET status='released' WHERE root_delegation_id=? AND reservation_id=? AND status IN {allowed}",
                       (root_delegation_id, reservation_id))

    def node_run_count(self, root_delegation_id: str, delegation_id: str | None = None) -> int:
        with self.connection() as db:
            scoped_id = delegation_id or root_delegation_id
            return db.execute("SELECT COUNT(*) FROM node_run_budget_entries WHERE root_delegation_id=? AND delegation_id=? AND status IN ('reserved','consumed')",
                              (root_delegation_id, scoped_id)).fetchone()[0]

    def cancel(self, run_id: str) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise OrchestrationError("run_not_found", "Run does not exist")
            run = json.loads(row["payload"])
            if run["status"] not in TERMINAL:
                run["cancel_requested"] = True
                run["status"] = "cancel_requested"
                self._write(db, run, "cancel_requested")
            return run

    def _assert_no_other_active(self, db, run):
        row = db.execute("SELECT id FROM runs WHERE canvas_id=? AND id<>? AND status NOT IN ('succeeded','failed','cancelled')",
                         (run["canvas_id"], run["id"])).fetchone()
        if row:
            raise OrchestrationError("canvas_busy", "Canvas has another active run", run_id=row["id"])

    def resume(self, run_id: str) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise OrchestrationError("run_not_found", "Run does not exist")
            run = json.loads(row["payload"])
            self._assert_no_other_active(db, run)
            if run["status"] == "cancelled":
                run["cancel_requested"] = False
                for attempts in run["attempts"].values():
                    attempt = attempts[-1]
                    if (attempt.get("result") is not None
                            and attempt["status"] in {"cancelled", "blocked"}):
                        # A settled provider result remains available after a
                        # stopped or stale projection. Resume verification;
                        # never buy the same result a second time.
                        attempt.update(status="verifying")
                    elif attempt["status"] in {"cancelled", "blocked"}:
                        attempts[-1].update(status="pending", error=None)
                # Failed paid calls require explicit retry, even after resuming.
                run["status"] = "failed" if any(a[-1]["status"] == "failed" for a in run["attempts"].values()) else "pending"
                self._write(db, run, "resumed")
            return run

    def retry_failed(self, run_id: str, *, request_id: str) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise OrchestrationError("run_not_found", "Run does not exist")
            run = json.loads(row["payload"])
            if request_id in run["retry_requests"]:
                return run
            if run["status"] != "failed":
                raise OrchestrationError("retry_not_allowed", "Only a failed, settled run may retry")
            self._assert_no_other_active(db, run)
            for key, attempts in run["attempts"].items():
                if attempts[-1]["status"] == "failed":
                    attempts.append(new_attempt(key, len(attempts) + 1))
                elif attempts[-1]["status"] == "blocked":
                    attempts[-1].update(status="pending", error=None)
            run["retry_requests"].append(request_id)
            run["status"] = "pending"
            self._write(db, run, "retry_failed")
            return run
