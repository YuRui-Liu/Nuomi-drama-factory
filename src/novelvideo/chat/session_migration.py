"""Legacy sessions without project provenance require explicit user reset."""
from pathlib import Path


def needs_confirmation(user_root: Path, scope_kind: str, project_id: str | None) -> bool:
    # Old provider IDs and hashed CLI folders have no trustworthy scope
    # metadata. Never infer ownership from recency or the currently open tab.
    if (user_root / 'agent_sessions.json').exists():
        return True
    legacy_cli = user_root / '.cli-chat'
    if legacy_cli.exists() and any(legacy_cli.glob('*/history.json')):
        return True
    return False


def scoped_history(user_root: Path, scope_kind: str, project_id: str | None) -> Path | None:
    scope_dir = '_home' if scope_kind == 'home' else str(project_id)
    if scope_dir not in {'.', '..'} and '/' not in scope_dir and '\\' not in scope_dir:
        path = user_root / scope_dir / 'chat.db'
        if path.is_file():
            return path
    return None
