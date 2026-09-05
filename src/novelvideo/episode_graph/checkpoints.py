import json
import os
import tempfile
from pathlib import Path
from typing import Any


class EpisodeGraphCheckpointStore:
    def __init__(self, project_root: Path | str) -> None:
        self._root = Path(project_root) / "state" / "episode_graph" / "checkpoints"

    def _path(self, target_revision: int, group_key: str) -> Path:
        return self._root / f"rev_{target_revision}" / f"{group_key}.json"

    def save_success(
        self,
        *,
        target_revision: int,
        group_key: str,
        content_hash: str,
        result: dict[str, Any],
    ) -> None:
        path = self._path(target_revision, group_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "target_revision": target_revision,
            "group_key": group_key,
            "content_hash": content_hash,
            "result": result,
        }
        descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def load_success(
        self, target_revision: int, group_key: str, content_hash: str
    ) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path(target_revision, group_key).read_text(encoding="utf-8"))
            if (
                payload.get("schema_version") != 1
                or payload.get("target_revision") != target_revision
                or payload.get("group_key") != group_key
                or payload.get("content_hash") != content_hash
                or not isinstance(payload.get("result"), dict)
            ):
                return None
            return payload["result"]
        except (OSError, ValueError, TypeError, AttributeError):
            return None

