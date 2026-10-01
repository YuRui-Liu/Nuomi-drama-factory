"""Offline sample acceptance, deliberately independent of production voices."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

LABELS = {"child": "儿童男声", "middle": "中年男声", "elder": "老年男声",
          "feifei_1": "朏朏 · 轻呜与呼噜", "feifei_2": "朏朏 · 短促叫声"}


class VoiceAcceptanceStore:
    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.directory = self.project_dir / "assets" / "voice_acceptance"
        self.db_path = self.directory / "acceptance.sqlite3"

    @contextmanager
    def connection(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS samples (
                sample_id TEXT PRIMARY KEY, metadata TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', notes TEXT NOT NULL DEFAULT '',
                reviewed_by TEXT NOT NULL DEFAULT '', reviewed_at TEXT NOT NULL DEFAULT ''
            )""")
            with db:
                yield db
        finally:
            db.close()

    def list_samples(self):
        if not self.db_path.exists():
            return []
        with self.connection() as db:
            return [dict(json.loads(row["metadata"]), **{
                key: row[key] for key in ("sample_id", "status", "notes", "reviewed_by", "reviewed_at")
            }) for row in db.execute("SELECT * FROM samples ORDER BY rowid")]

    def review(self, sample_id: str, *, status: str, notes: str, actor: str):
        if status not in {"pending", "passed", "rejected"} or len(notes) > 2000:
            raise ValueError("invalid review")
        with self.connection() as db:
            result = db.execute(
                "UPDATE samples SET status=?, notes=?, reviewed_by=?, reviewed_at=? WHERE sample_id=?",
                (status, notes.strip(), actor, datetime.now(timezone.utc).isoformat(), sample_id),
            )
            if result.rowcount != 1:
                raise KeyError(sample_id)
        return next(row for row in self.list_samples() if row["sample_id"] == sample_id)

    def import_batch(self, source: Path):
        """Import local receipts and existing audio only; never invoke a provider."""
        source = Path(source)
        summary = json.loads((source / "summary.json").read_text())
        prepared = []
        for item in summary["results"]:
            key = item["sample"]
            if key not in LABELS:
                raise ValueError("unknown sample")
            receipt = json.loads((source / f"{key}.json").read_text())
            original = (source / f"{key}.wav").read_bytes()
            if hashlib.sha256(original).hexdigest() != receipt["sha256"]:
                raise ValueError(f"audio hash mismatch: {key}")
            if receipt["task_id"] != item["task_id"]:
                raise ValueError("task mismatch")
            coins = receipt["provider_accounting"]["usage.consumeCoins"]
            if Decimal(coins) != Decimal(item["coins"]):
                raise ValueError("accounting mismatch")
            preview = (source / f"{key}.preview.wav").read_bytes()
            sample_id = hashlib.sha256((key + receipt["task_id"]).encode()).hexdigest()[:24]
            relative = Path("assets/voice_acceptance")
            metadata = {
                "label": LABELS[key], "kind": receipt.get("kind", "dialogue"),
                "instruction": receipt["instruction"], "text": receipt.get("text", ""),
                "coins": str(coins), "duration": float(item["duration"]),
                "task_id": receipt["task_id"], "sha256": receipt["sha256"],
                "path": str(relative / f"{sample_id}.wav"),
                "original_path": str(relative / f"{sample_id}.original"),
                "quality": receipt.get("quality", {}),
            }
            prepared.append((sample_id, metadata, original, preview))
        with self.connection() as db:
            for sample_id, metadata, original, preview in prepared:
                if db.execute("SELECT 1 FROM samples WHERE sample_id=?", (sample_id,)).fetchone():
                    continue
                (self.project_dir / metadata["path"]).write_bytes(preview)
                (self.project_dir / metadata["original_path"]).write_bytes(original)
                db.execute("INSERT INTO samples (sample_id, metadata) VALUES (?, ?)",
                           (sample_id, json.dumps(metadata, ensure_ascii=False)))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Import existing voice acceptance samples without generation")
    parser.add_argument("source", type=Path)
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    store = VoiceAcceptanceStore(args.project_dir)
    store.import_batch(args.source)
    print(json.dumps({"samples": len(store.list_samples()), "paid_calls": 0}))
