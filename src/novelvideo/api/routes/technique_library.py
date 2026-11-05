"""Shared technique catalog and authenticated personal favorites."""

import logging
from pathlib import Path
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from novelvideo.technique_library import catalog as case_catalog
from novelvideo.technique_library import experience as experience_service

from novelvideo import config
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes.freezone import _resolve_freezone_project
from novelvideo.freezone.video_director.favorites import FavoriteStore
from novelvideo.freezone.video_director.techniques import CATALOG_VERSION, list_techniques

router = APIRouter(prefix="/techniques", tags=["technique-library"])
project_router = APIRouter(prefix="/projects/{project}/techniques", tags=["technique-library"])
logger = logging.getLogger(__name__)


def _owner(user: dict) -> str:
    # AuthenticatedUser and AgentAuthenticatedUser expose the same stable ID.
    value = user.get("user_id") or user.get("id")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(401, "Authenticated user identity is required")
    return value


def get_favorite_store() -> FavoriteStore:
    return FavoriteStore(Path(config.STATE_DIR) / "technique_favorites.sqlite3")


def _favorites(owner: str, operation: str = "list_ids", card_id: str | None = None) -> dict:
    try:
        store = get_favorite_store()
        ids = store.list_ids(owner) if card_id is None else getattr(store, operation)(owner, card_id)
    except (sqlite3.Error, OSError) as exc:
        logger.exception("Technique favorites storage unavailable")
        raise HTTPException(503, "Technique favorites storage unavailable") from exc
    return {"ok": True, "data": {"ids": ids}}


def _search_experience(query: str, mode: str, duration_seconds: float, limit: int) -> dict:
    try:
        result = experience_service.search_experience(query, mode, duration_seconds, limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "data": result}


def _case_detail(case_id: str) -> dict:
    try:
        case = case_catalog.get_case(case_id)
    except case_catalog.CatalogUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if case is None:
        raise HTTPException(404, "Case not found")
    return {"ok": True, "data": _related(case)}


@router.get("")
def catalog(user: dict = Depends(get_api_user)) -> dict:
    _owner(user)
    return {"ok": True, "data": {
        "catalog_version": CATALOG_VERSION,
        "techniques": [card.model_dump(mode="json") for card in list_techniques()],
    }}


@router.get("/experience")
def experience(
    query: str = Query(..., min_length=1, max_length=300),
    mode: str = Query(..., pattern="^(i2v|i2va|fl2v|fl2va|ref_only|ref_plus_first|ref_plus_first_last|ref2va)$"),
    duration_seconds: float = Query(..., gt=0),
    limit: int = Query(5, ge=1, le=20),
    user: dict = Depends(get_api_user),
) -> dict:
    _owner(user)
    return _search_experience(query, mode, duration_seconds, limit)


@project_router.get("/experience")
async def project_experience(
    project: str,
    query: str = Query(..., min_length=1, max_length=300),
    mode: str = Query(..., pattern="^(i2v|i2va|fl2v|fl2va|ref_only|ref_plus_first|ref_plus_first_last|ref2va)$"),
    duration_seconds: float = Query(..., gt=0),
    limit: int = Query(5, ge=1, le=20),
    user: dict = Depends(get_api_user),
) -> dict:
    _owner(user)
    await _resolve_freezone_project(project, user, required_role="viewer")
    return _search_experience(query, mode, duration_seconds, limit)


@router.get("/favorites")
def favorites(user: dict = Depends(get_api_user)) -> dict:
    return _favorites(_owner(user))


def _related(case: dict) -> dict:
    return {**case, "related_technique_ids": [card.id for card in list_techniques()
             if case["id"] in getattr(card, "case_ids", ())]}


@router.get("/cases")
def cases(q: str = '', use_case: str = '', provenance: str = '',
          offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=100),
          user: dict = Depends(get_api_user)) -> dict:
    _owner(user)
    try:
        data = case_catalog.query_cases(q, use_case, provenance, offset, limit)
    except case_catalog.CatalogUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    data['items'] = [_related(case) for case in data['items']]
    return {'ok': True, 'data': data}


@router.get("/cases/{case_id}")
def case_detail(case_id: str, user: dict = Depends(get_api_user)) -> dict:
    _owner(user)
    return _case_detail(case_id)


@project_router.get("/cases/{case_id}")
async def project_case_detail(project: str, case_id: str, user: dict = Depends(get_api_user)) -> dict:
    _owner(user)
    await _resolve_freezone_project(project, user, required_role="viewer")
    return _case_detail(case_id)


@router.put("/favorites/{card_id}")
def add_favorite(card_id: str, user: dict = Depends(get_api_user)) -> dict:
    owner = _owner(user)
    if not any(card.id == card_id for card in list_techniques()):
        raise HTTPException(404, "Technique not found")
    return _favorites(owner, "add", card_id)


@router.delete("/favorites/{card_id}")
def remove_favorite(card_id: str, user: dict = Depends(get_api_user)) -> dict:
    return _favorites(_owner(user), "remove", card_id)
