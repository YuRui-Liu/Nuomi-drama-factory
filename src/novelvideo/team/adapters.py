"""Async adapters for the existing application ports."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import psycopg

from novelvideo.ports.project import Principal, ProjectRecord, ROLE_ORDER
from novelvideo.shared.project_dirs import default_project_dirs
from novelvideo.team.store import TeamStore


class TeamAuth:
    def __init__(self, store: TeamStore):
        self.store = store

    async def verify_session(self, raw_cookie):
        return await asyncio.to_thread(self.store.verify_session, raw_cookie)

    async def revoke_session(self, raw_cookie):
        await asyncio.to_thread(self.store.revoke_session, raw_cookie)


class TeamAgentSessions:
    def __init__(self, store: TeamStore):
        self.store = store

    async def verify_agent_session(self, token):
        return await asyncio.to_thread(self.store.verify_agent_session, token)

    async def create_agent_session(self, **kwargs):
        return await asyncio.to_thread(self.store.create_agent_session, **kwargs)

    async def update_agent_session_scope(self, *args, **kwargs):
        return await asyncio.to_thread(self.store.update_agent_session_scope, *args, **kwargs)

    async def revoke_agent_session(self, token_value):
        return await asyncio.to_thread(self.store.revoke_agent_session, token_value)


class TeamProjectRegistry:
    def __init__(self, store: TeamStore):
        self.store = store

    async def _query(self, sql, params=(), *, many=False):
        def run():
            with self.store.connect() as db:
                cursor = db.execute(sql, params)
                return cursor.fetchall() if many else cursor.fetchone()
        return await asyncio.to_thread(run)

    async def get_project(self, project_id):
        row = await self._query("SELECT * FROM team_projects WHERE id=%s", (project_id,))
        return ProjectRecord(**row) if row else None

    async def get_project_by_owner_name(self, owner_user_id, name):
        row = await self._query("SELECT * FROM team_projects WHERE owner_id=%s AND name=%s AND purged_at IS NULL", (owner_user_id, name))
        return ProjectRecord(**row) if row else None

    async def create_project(self, *, owner_user_id, owner_username, name, home_node_id=None, output_dir=None, state_dir=None, runtime_dir=None):
        user = await asyncio.to_thread(self.store.get_user, user_id=owner_user_id)
        if not user or not user["enabled"] or user["username"] != owner_username:
            raise ValueError("Invalid project owner")
        defaults = default_project_dirs(owner_username, name)
        now = datetime.now(timezone.utc).isoformat()
        try:
            row = await self._query("""INSERT INTO team_projects(id,owner_type,owner_id,owner_username,name,home_node_id,output_dir,state_dir,runtime_dir,status,created_at,updated_at)
                VALUES (%s,'user',%s,%s,%s,%s,%s,%s,%s,'active',%s,%s) RETURNING *""",
                (str(uuid4()), owner_user_id, owner_username, name, home_node_id or "local", output_dir or defaults[0], state_dir or defaults[1], runtime_dir or defaults[2], now, now))
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError(f"Project '{name}' already exists") from exc
        return ProjectRecord(**row)

    async def list_accessible_projects(self, principals):
        ids = [identifier for kind, identifier in principals if kind == "user"]
        if not ids:
            return []
        rows = await self._query("""SELECT DISTINCT p.* FROM team_projects p
            JOIN team_users u ON u.id=ANY(%s) AND u.enabled
            LEFT JOIN team_grants g ON g.project_id=p.id AND g.principal_id=u.id
            WHERE p.owner_id=u.id OR g.id IS NOT NULL ORDER BY p.updated_at DESC""", (ids,), many=True)
        return [ProjectRecord(**row) for row in rows]

    async def update_project_status(self, project_id, status):
        row = await self._query("UPDATE team_projects SET status=%s,updated_at=%s WHERE id=%s AND purged_at IS NULL RETURNING *", (status, datetime.now(timezone.utc).isoformat(), project_id))
        return ProjectRecord(**row) if row else None

    async def mark_project_purged(self, project_id):
        row = await self._query("DELETE FROM team_projects WHERE id=%s RETURNING *", (project_id,))
        now = datetime.now(timezone.utc).isoformat()
        return replace(ProjectRecord(**row), status="deleted", updated_at=now, purged_at=now) if row else None

    async def delete_uncommitted_project(self, project_id):
        await self._query("DELETE FROM team_projects WHERE id=%s RETURNING id", (project_id,))

    async def delete_project_home(self, project_id):
        return None

    async def resolve_username_by_user_id(self, user_id):
        user = await asyncio.to_thread(self.store.get_user, user_id=user_id)
        return user["username"] if user else None

    async def resolve_user_id_by_username(self, username):
        user = await asyncio.to_thread(self.store.get_user, username=username)
        return user["id"] if user and user["enabled"] else None


class TeamProjectAccess:
    def __init__(self, store: TeamStore):
        self.store = store

    async def resolve_requester_principals(self, user_id):
        user = await asyncio.to_thread(self.store.get_user, user_id=user_id)
        return [Principal("user", user_id)] if user and user["enabled"] else []

    async def effective_project_role(self, project, principals):
        ids = [principal.id for principal in principals if principal.type == "user"]
        def run():
            with self.store.connect() as db:
                enabled = {row["id"] for row in db.execute("SELECT id FROM team_users WHERE id=ANY(%s) AND enabled", (ids,))}
                if project.owner_id in enabled:
                    return "owner"
                roles = [row["role"] for row in db.execute("SELECT role FROM team_grants WHERE project_id=%s AND principal_id=ANY(%s)", (project.id, list(enabled)))]
                return max(roles, key=lambda role: ROLE_ORDER[role]) if roles else None
        return await asyncio.to_thread(run)

    async def count_project_task_eligible_users(self, *, project_id, owner_type, owner_id):
        def run():
            with self.store.connect() as db:
                return db.execute("""SELECT count(DISTINCT u.id) AS n FROM team_users u LEFT JOIN team_grants g ON g.principal_id=u.id AND g.project_id=%s
                    WHERE u.enabled AND (u.id=%s OR g.role IN ('editor','admin'))""", (project_id, owner_id)).fetchone()["n"]
        return await asyncio.to_thread(run)


class TeamLifecycle:
    def __init__(self, store):
        self.store = store

    async def on_startup(self, *, register_as_worker=True):
        await asyncio.to_thread(self.store.init_db)

    async def on_shutdown(self):
        return None
