import os
from uuid import uuid4

import pytest


def test_password_hash_is_salted_and_checked():
    from novelvideo.team.store import hash_password, verify_password
    first = hash_password("strong-password-123")
    assert first != hash_password("strong-password-123")
    assert verify_password("strong-password-123", first)
    assert not verify_password("wrong", first)


@pytest.fixture
def store():
    from novelvideo.team.store import TeamStore
    dsn = os.getenv("TEAM_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEAM_TEST_DATABASE_URL required for real PostgreSQL integration")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    schema = "test_team_" + uuid4().hex
    with psycopg.connect(dsn) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    result = TeamStore(make_conninfo(dsn, options=f"-csearch_path={schema}"))
    result.init_db()
    yield result
    with psycopg.connect(dsn) as db:
        db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_login_session_revocation_and_disable(store):
    from novelvideo.ports.auth_contract import AuthError
    user = store.create_user("alice", "strong-password-123")
    with pytest.raises(AuthError):
        store.login("alice", "incorrect")
    token, identity = store.login("alice", "strong-password-123")
    assert identity["id"] == user["id"] != "local"
    assert store.verify_session(token)["username"] == "alice"
    store.update_user(user["id"], password="new-password-123")
    with pytest.raises(AuthError):
        store.verify_session(token)
    token, _ = store.login("alice", "new-password-123")
    store.update_user(user["id"], enabled=False)
    with pytest.raises(AuthError):
        store.verify_session(token)
    with pytest.raises(AuthError):
        store.login("alice", "new-password-123")


def test_expired_and_revoked_sessions(store):
    from novelvideo.ports.auth_contract import AuthError
    store.create_user("alice", "strong-password-123")
    token, _ = store.login("alice", "strong-password-123", ttl_seconds=-1)
    with pytest.raises(AuthError):
        store.verify_session(token)
    token, _ = store.login("alice", "strong-password-123")
    store.revoke_session(token)
    with pytest.raises(AuthError):
        store.verify_session(token)


async def test_project_isolation_and_grants(store):
    from novelvideo.team.adapters import TeamProjectRegistry, TeamProjectAccess
    owner = store.create_user("owner", "strong-password-123")
    other = store.create_user("other", "strong-password-123")
    registry, access = TeamProjectRegistry(store), TeamProjectAccess(store)
    project = await registry.create_project(owner_user_id=owner["id"], owner_username="owner", name="demo")
    principals = await access.resolve_requester_principals(other["id"])
    assert await access.effective_project_role(project, principals) is None
    assert await registry.list_accessible_projects([("user", other["id"])]) == []
    grant = store.create_grant(project.id, other["id"], "viewer")
    assert await access.effective_project_role(project, principals) == "viewer"
    assert len(await registry.list_accessible_projects([("user", other["id"])])) == 1
    store.delete_grant(project.id, grant["id"])
    assert await access.effective_project_role(project, principals) is None
    with pytest.raises(ValueError):
        store.create_grant(project.id, other["id"], "owner")


def test_migrate_is_idempotent_and_refuses_conflict(store, tmp_path):
    import sqlite3
    from novelvideo.team.cli import migrate_ce
    owner = store.create_user("admin", "strong-password-123", "admin")
    source = tmp_path / "projects.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE projects(id,owner_type,owner_id,owner_username,name,home_node_id,output_dir,state_dir,runtime_dir,status,created_at,updated_at,purged_at)")
        db.execute("INSERT INTO projects VALUES('legacy','user','local','local','demo','local','/old/output','/old/state','/old/runtime','active','a','b',NULL)")
    assert migrate_ce(store, source, owner["username"]) == 1
    assert migrate_ce(store, source, owner["username"]) == 0
    with store.connect() as db:
        row = db.execute("SELECT * FROM team_projects WHERE id='legacy'").fetchone()
        assert row["output_dir"] == "/old/output"
        assert row["owner_id"] == owner["id"]
        assert row["owner_username"] == "local"
        db.execute("UPDATE team_projects SET output_dir='/different' WHERE id='legacy'")
    with pytest.raises(ValueError, match="conflict"):
        migrate_ce(store, source, owner["username"])


def test_path_maps_use_component_boundaries_and_longest_prefix():
    from novelvideo.team.cli import map_path
    assert map_path("/old/out/local/demo", ["/old=/data", "/old/out=/media"]) == "/media/local/demo"
    assert map_path("/older/out", ["/old=/data"]) == "/older/out"
    with pytest.raises(ValueError):
        map_path("/old/out", ["/old=relative"])


async def test_agent_session_identity_scope_and_revoked_grants(store):
    from novelvideo.team.adapters import TeamAgentSessions, TeamProjectRegistry
    from novelvideo.ports.auth_contract import AuthError
    owner = store.create_user("owner", "strong-password-123", "admin")
    store.create_user("other", "strong-password-123")
    port = TeamAgentSessions(store)
    token = await port.create_agent_session(username="owner", scopes=["projects:read"])
    identity = await port.verify_agent_session(token.value)
    assert identity["id"] == owner["id"]
    assert identity["role"] == "member"
    assert identity["credential_kind"] == "agent_session"
    assert identity["current_scope_kind"] == "home"
    with store.connect() as db:
        assert db.execute("SELECT token_hash FROM team_agent_sessions").fetchone()["token_hash"] != token.value
    project = await TeamProjectRegistry(store).create_project(owner_user_id=owner["id"], owner_username="owner", name="test")
    with pytest.raises(AuthError):
        await port.create_agent_session(username="other", scopes=[], current_scope_kind="project", current_project_id=project.id)
    other = store.get_user(username="other")
    grant = store.create_grant(project.id, other["id"], "editor")
    other_token = await port.create_agent_session(username="other", scopes=["projects:read"], current_scope_kind="project", current_project_id=project.id)
    assert (await port.verify_agent_session(other_token.value))["current_project_id"] == project.id
    store.update_grant(project.id, grant["id"], "viewer")
    with pytest.raises(AuthError):
        await port.verify_agent_session(other_token.value)
    store.update_grant(project.id, grant["id"], "editor")
    store.delete_grant(project.id, grant["id"])
    with pytest.raises(AuthError):
        await port.verify_agent_session(other_token.value)
    with pytest.raises(AuthError):
        await port.update_agent_session_scope(token.value, scope_kind="project", project_id="missing")
    await port.update_agent_session_scope(token.value, scope_kind="project", project_id=project.id)
    assert (await port.verify_agent_session(token.value))["current_project_id"] == project.id
    from novelvideo.team.store import token_hash
    with store.connect() as db:
        expiry = db.execute("SELECT expires_at FROM team_agent_sessions WHERE token_hash=%s", (token_hash(token.value),)).fetchone()["expires_at"]
    assert int(expiry.timestamp()) == token.exp


@pytest.mark.parametrize("action", ["expired", "revoke", "disable", "reset", "logout"])
async def test_agent_sessions_expire_and_revoke(store, action):
    from novelvideo.team.adapters import TeamAgentSessions
    from novelvideo.ports.auth_contract import AuthError
    user = store.create_user("alice", "strong-password-123")
    cookie, _ = store.login("alice", "strong-password-123")
    port = TeamAgentSessions(store)
    token = await port.create_agent_session(username="alice", scopes=[])
    if action == "expired":
        with store.connect() as db:
            db.execute("UPDATE team_agent_sessions SET expires_at=now()-interval '1 second'")
    elif action == "revoke":
        await port.revoke_agent_session(token.value)
    elif action == "disable":
        store.update_user(user["id"], enabled=False)
    elif action == "reset":
        store.update_user(user["id"], password="replacement-password")
    else:
        store.revoke_session(cookie)
    with pytest.raises(AuthError):
        await port.verify_agent_session(token.value)


async def test_agent_rejects_invalid_scopes_identity_and_ttl(store):
    from novelvideo.team.adapters import TeamAgentSessions
    from novelvideo.ports.auth_contract import AuthError
    store.create_user("alice", "strong-password-123")
    port = TeamAgentSessions(store)
    for kwargs in [{"scopes": ["*"]}, {"scopes": [], "ttl_seconds": -1}, {"scopes": [], "current_scope_kind": "global"}, {"scopes": [], "current_project_id": "foreign"}]:
        with pytest.raises(AuthError):
            await port.create_agent_session(username="alice", **kwargs)
    with pytest.raises(AuthError):
        await port.create_agent_session(username="missing", scopes=[])
