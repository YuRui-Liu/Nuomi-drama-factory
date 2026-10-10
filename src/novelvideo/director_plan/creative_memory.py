"""Scoped lessons derived only from explicit, verified QC conclusions."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .store import _atomic_write_json


class CreativeMemoryStore:
    def __init__(self, project_dir: Path, *, project_id: str, task_kind: str):
        self.root = Path(project_dir) / "director_plans" / "creative_memory"
        self.project_id, self.task_kind = project_id, task_kind

    def _scope(self, source_hash, rules_version):
        return {"project_id": self.project_id, "task_kind": self.task_kind,
                "source_hash": source_hash, "rules_version": rules_version}

    def record(self, history, *, source_hash):
        scope = self._scope(source_hash, history.rules_version)
        if any(state.status == "regressed" for state in history.issues):
            self.invalidate()
        responses = {r.issue_id: r for turn in history.author_rounds for r in turn.responses}
        entries = []
        for state in history.issues:
            response = responses.get(state.issue.issue_id)
            verified = (state.status == "resolved"
                        and state.last_reviewed_revision_id == history.current_revision_id
                        and response is not None)
            entries.append({"issue_id": state.issue.issue_id,
                "source_revision_id": history.current_revision_id,
                "discovered_revision_id": state.issue.discovered_revision_id,
                "qc_round_id": state.last_reviewed_round_id,
                "applicability": scope,
                "problem": state.issue.description,
                "attempt": response.explanation if response else None,
                "outcome": "verified_resolved" if verified else "failed_attempt",
                "valid": verified, "evidence_paths": list(state.issue.evidence_paths)})
        key = hashlib.sha256(json.dumps([scope, history.initial_revision_id,
            history.current_revision_id], sort_keys=True).encode()).hexdigest()
        _atomic_write_json(self.root / (key + ".json"), {"scope": scope, "entries": entries})

    def load(self, *, source_hash, rules_version):
        scope = self._scope(source_hash, rules_version)
        result = []
        for path in self.root.glob("*.json"):
            value = json.loads(path.read_text())
            if value.get("scope") == scope:
                result.extend(entry for entry in value["entries"]
                              if entry.get("valid") and entry.get("outcome") == "verified_resolved")
        return result

    def invalidate(self):
        for path in self.root.glob("*.json"):
            value = json.loads(path.read_text())
            if (value["scope"]["project_id"], value["scope"]["task_kind"]) == (self.project_id, self.task_kind):
                for entry in value["entries"]:
                    entry["valid"] = False
                _atomic_write_json(path, value)
