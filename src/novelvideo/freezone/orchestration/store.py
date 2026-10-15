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
            """)

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
               settings: dict, actor: dict, operation: str | None = None) -> dict:
        if not request_id.strip():
            raise OrchestrationError("request_id_required", "A stable request ID is required")
        order = execution_order(canvas["nodes"], canvas["edges"], target)
        snapshot = deepcopy(canvas)
        if operation:
            target_node = next(n for n in snapshot["nodes"] if n["id"] == target)
            target_node["data"]["operation"] = operation
            if operation == 'generate' and target_node['type'] in {'imageGenNode', 'imageNode', 'storyboardGenNode'}:
                target_node['data']['referenceOnly'] = False
        request_hash = fingerprint([snapshot, target, settings, operation, actor.get("id")])
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
                   "input_fingerprints": {node["id"]: input_fingerprint(node) for node in canvas["nodes"]},
                   "retry_requests": [], "created_at": utc_now_iso(), "updated_at": utc_now_iso()}
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
                    if attempts[-1]["status"] in {"cancelled", "blocked"}:
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
