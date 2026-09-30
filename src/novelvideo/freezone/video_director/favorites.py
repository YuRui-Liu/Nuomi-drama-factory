"""Durable, user-scoped technique favorites independent of project state."""

from contextlib import closing
from pathlib import Path
import sqlite3


class FavoriteStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS technique_favorites "
                "(owner TEXT NOT NULL, card_id TEXT NOT NULL, PRIMARY KEY(owner, card_id))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _ids(conn: sqlite3.Connection, owner: str) -> list[str]:
        if not owner.strip():
            raise ValueError("favorite owner is required")
        return [row[0] for row in conn.execute(
            "SELECT card_id FROM technique_favorites WHERE owner = ? ORDER BY card_id", (owner,)
        )]

    def list_ids(self, owner: str) -> list[str]:
        with closing(self._connect()) as conn:
            return self._ids(conn, owner)

    def add(self, owner: str, card_id: str) -> list[str]:
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO technique_favorites VALUES (?, ?)", (owner, card_id))
            return self._ids(conn, owner)

    def remove(self, owner: str, card_id: str) -> list[str]:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM technique_favorites WHERE owner = ? AND card_id = ?", (owner, card_id))
            return self._ids(conn, owner)
