"""Single-team PostgreSQL persistence. Raw session credentials are never stored."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from novelvideo.ports.auth_contract import (
    AuthError, AuthFailureReason, AgentSessionToken, AgentAuthenticatedUser,
    DEFAULT_EXTERNAL_AGENT_SCOPES,
)


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12–128 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, salt, expected = encoded.split("$")
        if kind != "scrypt" or len(password) > 128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(row: dict) -> dict:
    return {key: row[key] for key in ("id", "username", "role", "enabled", "created_at")}


class TeamStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("ST_TEAM_DATABASE_URL", "").strip()
        if not self.dsn:
            raise RuntimeError("ST_TEAM_DATABASE_URL is required for ST_EDITION=team")

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def init_db(self):
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS team_users (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','member')),
                    enabled BOOLEAN NOT NULL DEFAULT TRUE, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS team_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES team_users(id) ON DELETE CASCADE,
                    expires_at TIMESTAMPTZ NOT NULL
                );
                CREATE INDEX IF NOT EXISTS team_sessions_user ON team_sessions(user_id);
                CREATE TABLE IF NOT EXISTS team_agent_sessions (
                    token_hash TEXT PRIMARY KEY, session_id TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL REFERENCES team_users(id) ON DELETE CASCADE,
                    scopes JSONB NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
                    current_scope_kind TEXT NOT NULL CHECK(current_scope_kind IN ('home','project')),
                    current_project_id TEXT, agent_kind TEXT NOT NULL, worker_id TEXT,
                    parent_session_id TEXT
                );
                CREATE INDEX IF NOT EXISTS team_agent_sessions_user ON team_agent_sessions(user_id);
                CREATE TABLE IF NOT EXISTS team_projects (
                    id TEXT PRIMARY KEY, owner_type TEXT NOT NULL DEFAULT 'user',
                    owner_id TEXT NOT NULL REFERENCES team_users(id), owner_username TEXT NOT NULL,
                    name TEXT NOT NULL, home_node_id TEXT NOT NULL, output_dir TEXT NOT NULL,
                    state_dir TEXT NOT NULL, runtime_dir TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, purged_at TEXT,
                    UNIQUE(owner_id, name)
                );
                CREATE TABLE IF NOT EXISTS team_grants (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES team_projects(id) ON DELETE CASCADE,
                    principal_type TEXT NOT NULL DEFAULT 'user' CHECK(principal_type = 'user'),
                    principal_id TEXT NOT NULL REFERENCES team_users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')), created_at TEXT NOT NULL,
                    UNIQUE(project_id, principal_id)
                );
            """)

    def create_user(self, username: str, password: str, role: str = "member") -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", username):
            raise ValueError("Username must be 3–64 letters, digits, underscores or hyphens")
        if role not in ("admin", "member"):
            raise ValueError("Invalid account role")
        encoded = hash_password(password)
        with self.connect() as db:
            try:
                row = db.execute("INSERT INTO team_users(id,username,password_hash,role,created_at) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                                 (str(uuid4()), username, encoded, role, datetime.now(timezone.utc).isoformat())).fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError("Username already exists") from exc
        return public_user(row)

    def get_user(self, *, user_id: str | None = None, username: str | None = None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM team_users WHERE id=%s OR username=%s", (user_id, username)).fetchone()
        return public_user(row) if row else None

    def list_users(self, query: str | None = None):
        with self.connect() as db:
            if query is None:
                rows = db.execute("SELECT * FROM team_users ORDER BY username").fetchall()
            else:
                rows = db.execute("SELECT * FROM team_users WHERE enabled AND strpos(lower(username),lower(%s)) > 0 ORDER BY username LIMIT 30", (query,)).fetchall()
        return [public_user(row) for row in rows]

    def update_user(self, user_id: str, *, enabled: bool | None = None, password: str | None = None):
        encoded = hash_password(password) if password is not None else None
        with self.connect() as db:
            # Serialize account changes so two administrators cannot disable the last administrators concurrently.
            db.execute("LOCK TABLE team_users IN SHARE ROW EXCLUSIVE MODE")
            row = db.execute("SELECT * FROM team_users WHERE id=%s", (user_id,)).fetchone()
            if not row:
                raise ValueError("User not found")
            if enabled is False and row["role"] == "admin" and row["enabled"]:
                count = db.execute("SELECT count(*) AS n FROM team_users WHERE role='admin' AND enabled").fetchone()["n"]
                if count <= 1:
                    raise ValueError("Cannot disable the last administrator")
            row = db.execute("UPDATE team_users SET enabled=COALESCE(%s,enabled), password_hash=COALESCE(%s,password_hash) WHERE id=%s RETURNING *", (enabled, encoded, user_id)).fetchone()
            if enabled is False or encoded is not None:
                db.execute("DELETE FROM team_sessions WHERE user_id=%s", (user_id,))
                db.execute("DELETE FROM team_agent_sessions WHERE user_id=%s", (user_id,))
        return public_user(row)

    def login(self, username: str, password: str, *, ttl_seconds: int = 604800):
        with self.connect() as db:
            row = db.execute("SELECT * FROM team_users WHERE username=%s FOR UPDATE", (username,)).fetchone()
            # Equal-cost verification even for unknown names.
            encoded = row["password_hash"] if row else "scrypt$" + "00" * 16 + "$" + "00" * 64
            valid = verify_password(password, encoded)
            if not row or not valid or not row["enabled"]:
                raise AuthError(AuthFailureReason.INVALID, "Invalid username or password")
            token = secrets.token_urlsafe(32)
            db.execute("DELETE FROM team_sessions WHERE expires_at <= now()")
            db.execute("INSERT INTO team_sessions VALUES (%s,%s,%s)", (token_hash(token), row["id"], datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)))
        return token, {"id": row["id"], "user_id": row["id"], "username": row["username"], "role": row["role"]}

    def verify_session(self, token: str | None):
        if not token:
            raise AuthError(AuthFailureReason.MISSING)
        with self.connect() as db:
            row = db.execute("SELECT u.* FROM team_sessions s JOIN team_users u ON u.id=s.user_id WHERE s.token_hash=%s AND s.expires_at>now() AND u.enabled", (token_hash(token),)).fetchone()
        if not row:
            raise AuthError(AuthFailureReason.INVALID)
        return {"id": row["id"], "user_id": row["id"], "username": row["username"], "role": row["role"]}

    def revoke_session(self, token: str):
        with self.connect() as db:
            row = db.execute("SELECT user_id FROM team_sessions WHERE token_hash=%s", (token_hash(token),)).fetchone()
            if row:
                db.execute("SELECT id FROM team_users WHERE id=%s FOR UPDATE", (row["user_id"],))
                db.execute("DELETE FROM team_agent_sessions WHERE user_id=%s", (row["user_id"],))
                db.execute("DELETE FROM team_sessions WHERE token_hash=%s", (token_hash(token),))

    @staticmethod
    def _check_agent_scope(db, user_id, scope_kind, project_id):
        if scope_kind == "home" and project_id is None:
            return
        if scope_kind != "project" or not project_id:
            raise AuthError(AuthFailureReason.INVALID, "Invalid agent scope")
        row = db.execute("""SELECT p.owner_id,g.role FROM team_projects p
            LEFT JOIN team_grants g ON g.project_id=p.id AND g.principal_id=%s
            WHERE p.id=%s AND p.purged_at IS NULL""", (user_id, project_id)).fetchone()
        if not row or (row["owner_id"] != user_id and row["role"] not in ("editor", "admin")):
            raise AuthError(AuthFailureReason.INVALID, "Agent requires project editor access")

    def create_agent_session(self, *, username, scopes, ttl_seconds=None, agent_kind="agent", worker_id=None,
                             parent_session_id=None, current_scope_kind="home", current_project_id=None, metadata=None):
        normalized = tuple(dict.fromkeys(scopes or ()))
        if any(scope not in DEFAULT_EXTERNAL_AGENT_SCOPES for scope in normalized):
            raise AuthError(AuthFailureReason.INVALID, "Unsupported agent scopes")
        ttl = 7200 if ttl_seconds is None else ttl_seconds
        if not isinstance(ttl, int) or not 1 <= ttl <= 86400:
            raise AuthError(AuthFailureReason.INVALID, "Agent TTL must be 1–86400 seconds")
        expires = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        raw, session_id = secrets.token_urlsafe(32), str(uuid4())
        with self.connect() as db:
            user = db.execute("SELECT * FROM team_users WHERE username=%s AND enabled FOR UPDATE", (username,)).fetchone()
            if not user:
                raise AuthError(AuthFailureReason.INVALID, "Unknown or disabled agent user")
            self._check_agent_scope(db, user["id"], current_scope_kind, current_project_id)
            db.execute("DELETE FROM team_agent_sessions WHERE expires_at <= now()")
            db.execute("""INSERT INTO team_agent_sessions
                (token_hash,session_id,user_id,scopes,expires_at,current_scope_kind,current_project_id,agent_kind,worker_id,parent_session_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (token_hash(raw), session_id, user["id"], Jsonb(list(normalized)), expires, current_scope_kind, current_project_id, agent_kind, worker_id, parent_session_id))
        return AgentSessionToken(value=raw, session_id=session_id, user=username, scopes=normalized,
                                 exp=int(expires.timestamp()), worker_id=worker_id or "", agent_kind=agent_kind)

    def _agent_row(self, db, token):
        if not token:
            raise AuthError(AuthFailureReason.MISSING)
        row = db.execute("""SELECT s.*,u.username FROM team_agent_sessions s JOIN team_users u ON u.id=s.user_id
            WHERE s.token_hash=%s AND s.expires_at>now() AND u.enabled""", (token_hash(token),)).fetchone()
        if not row:
            raise AuthError(AuthFailureReason.INVALID, "Invalid or expired agent session")
        self._check_agent_scope(db, row["user_id"], row["current_scope_kind"], row["current_project_id"])
        return row

    def verify_agent_session(self, token):
        with self.connect() as db:
            row = self._agent_row(db, token)
        return AgentAuthenticatedUser(
            id=row["user_id"], username=row["username"], role="member",
            agent_session_id=row["session_id"], agent_kind=row["agent_kind"], worker_id=row["worker_id"],
            scopes=tuple(row["scopes"]), current_scope_kind=row["current_scope_kind"],
            current_project_id=row["current_project_id"], parent_session_id=row["parent_session_id"],
        ).to_legacy_dict()

    def update_agent_session_scope(self, token_value, *, scope_kind, project_id):
        with self.connect() as db:
            row = self._agent_row(db, token_value)
            self._check_agent_scope(db, row["user_id"], scope_kind, project_id)
            db.execute("UPDATE team_agent_sessions SET current_scope_kind=%s,current_project_id=%s WHERE token_hash=%s",
                       (scope_kind, project_id, token_hash(token_value)))

    def revoke_agent_session(self, token_value):
        with self.connect() as db:
            db.execute("DELETE FROM team_agent_sessions WHERE token_hash=%s", (token_hash(token_value),))

    def list_grants(self, project_id: str):
        with self.connect() as db:
            return db.execute("SELECT g.*,u.username AS principal_username FROM team_grants g JOIN team_users u ON u.id=g.principal_id WHERE project_id=%s ORDER BY created_at", (project_id,)).fetchall()

    def create_grant(self, project_id: str, principal_id: str, role: str):
        if role not in ("admin", "editor", "viewer"):
            raise ValueError("Invalid project role")
        with self.connect() as db:
            user = db.execute("SELECT id FROM team_users WHERE id=%s AND enabled", (principal_id,)).fetchone()
            project = db.execute("SELECT owner_id FROM team_projects WHERE id=%s", (project_id,)).fetchone()
            if not user or not project or project["owner_id"] == principal_id:
                raise ValueError("Invalid grant recipient")
            try:
                row = db.execute("INSERT INTO team_grants(id,project_id,principal_id,role,created_at) VALUES (%s,%s,%s,%s,%s) RETURNING *", (str(uuid4()), project_id, principal_id, role, datetime.now(timezone.utc).isoformat())).fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError("User already has a grant") from exc
        return row

    def update_grant(self, project_id: str, grant_id: str, role: str):
        if role not in ("admin", "editor", "viewer"):
            raise ValueError("Invalid project role")
        with self.connect() as db:
            row = db.execute("UPDATE team_grants SET role=%s WHERE id=%s AND project_id=%s RETURNING *", (role, grant_id, project_id)).fetchone()
        if not row:
            raise ValueError("Grant not found")
        return row

    def delete_grant(self, project_id: str, grant_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM team_grants WHERE id=%s AND project_id=%s", (grant_id, project_id))
