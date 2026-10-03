"""Team login, account administration and project sharing endpoints."""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes.auth import _set_auth_cookie
from novelvideo.ports.auth_contract import AuthError
from novelvideo.ports.registry import get_port

router = APIRouter()
_attempts: OrderedDict[tuple[str, str], list[float]] = OrderedDict()


class Login(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class CreateUser(Login):
    role: Literal["admin", "member"] = "member"


class UpdateUser(BaseModel):
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)


class Grant(BaseModel):
    principal_type: Literal["user"] = "user"
    principal_id: str | None = None
    principal_username: str | None = None
    role: Literal["admin", "editor", "viewer"]

    @model_validator(mode="after")
    def one_recipient(self):
        selectors = [value for value in (self.principal_id, self.principal_username) if value is not None]
        if len(selectors) != 1 or not selectors[0].strip():
            raise ValueError("Exactly one nonblank principal_id or principal_username is required")
        return self


class UpdateGrant(BaseModel):
    role: Literal["admin", "editor", "viewer"]


def store():
    return get_port("team_store")


async def call(method, *args, **kwargs):
    try:
        return await asyncio.to_thread(method, *args, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def admin(user=Depends(get_api_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Instance administrator required")
    return user


@router.post("/auth/login")
async def login(payload: Login, request: Request):
    # Trust only the socket peer; do not accept spoofable forwarded headers here.
    peer = request.client.host if request.client else "unknown"
    now = time.monotonic()
    # A reverse proxy is the peer for every user. Account-specific buckets prevent
    # failed attempts for one account from locking out everyone behind that proxy.
    key = (peer, payload.username.strip().casefold())
    attempts = [stamp for stamp in _attempts.get(key, []) if now - stamp < 60]
    if len(attempts) >= 20:
        raise HTTPException(429, "Too many login attempts; retry in one minute", headers={"Retry-After": "60"})
    _attempts[key] = attempts + [now]
    _attempts.move_to_end(key)
    while len(_attempts) > 4096:
        _attempts.popitem(last=False)
    try:
        token, user = await call(store().login, payload.username, payload.password)
    except AuthError as exc:
        raise HTTPException(401, "Invalid username or password") from exc
    response = JSONResponse({"ok": True, "data": user})
    _set_auth_cookie(response, token)
    return response


@router.get("/admin/users")
async def users(user=Depends(admin)):
    return {"ok": True, "data": await call(store().list_users)}


@router.post("/admin/users")
async def create_user(payload: CreateUser, user=Depends(admin)):
    return {"ok": True, "data": await call(store().create_user, payload.username, payload.password, payload.role)}


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: str, payload: UpdateUser, user=Depends(admin)):
    return {"ok": True, "data": await call(store().update_user, user_id, **payload.model_dump(exclude_unset=True))}


@router.get("/users/search")
async def search_users(q: str = "", user=Depends(get_api_user)):
    rows = await call(store().list_users, q.strip()) if 3 <= len(q.strip()) <= 64 else []
    return {"ok": True, "data": [{"id": row["id"], "username": row["username"]} for row in rows]}


async def project_admin(project_id, user):
    project = await get_port("project_registry").get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    access = get_port("project_access")
    principals = await access.resolve_requester_principals(user.get("user_id") or user["id"])
    role = await access.effective_project_role(project, principals)
    if role not in ("owner", "admin"):
        raise HTTPException(403, "Project administrator required")
    return project


@router.get("/projects/{project_id}/grants")
async def grants(project_id: str, user=Depends(get_api_user)):
    await project_admin(project_id, user)
    return {"ok": True, "data": await call(store().list_grants, project_id)}


@router.post("/projects/{project_id}/grants")
async def create_grant(project_id: str, payload: Grant, user=Depends(get_api_user)):
    await project_admin(project_id, user)
    target = await call(store().get_user, user_id=payload.principal_id, username=payload.principal_username)
    if not target:
        raise HTTPException(404, "User not found")
    return {"ok": True, "data": await call(store().create_grant, project_id, target["id"], payload.role)}


@router.patch("/projects/{project_id}/grants/{grant_id}")
async def update_grant(project_id: str, grant_id: str, payload: UpdateGrant, user=Depends(get_api_user)):
    await project_admin(project_id, user)
    return {"ok": True, "data": await call(store().update_grant, project_id, grant_id, payload.role)}


@router.delete("/projects/{project_id}/grants/{grant_id}")
async def delete_grant(project_id: str, grant_id: str, user=Depends(get_api_user)):
    await project_admin(project_id, user)
    await call(store().delete_grant, project_id, grant_id)
    return {"ok": True, "data": {"grant_id": grant_id}}
