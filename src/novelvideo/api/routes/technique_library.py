"""Shared technique catalog and authenticated personal favorites."""

import logging
from pathlib import Path
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from novelvideo import config
from novelvideo.api.auth import get_api_user
from novelvideo.freezone.video_director.favorites import FavoriteStore
from novelvideo.freezone.video_director.techniques import CATALOG_VERSION, list_techniques

router = APIRouter(prefix="/techniques", tags=["technique-library"])
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


@router.get("")
def catalog(user: dict = Depends(get_api_user)) -> dict:
    _owner(user)
    return {"ok": True, "data": {
        "catalog_version": CATALOG_VERSION,
        "techniques": [card.model_dump(mode="json") for card in list_techniques()],
    }}


@router.get("/favorites")
def favorites(user: dict = Depends(get_api_user)) -> dict:
    return _favorites(_owner(user))


@router.put("/favorites/{card_id}")
def add_favorite(card_id: str, user: dict = Depends(get_api_user)) -> dict:
    owner = _owner(user)
    if not any(card.id == card_id for card in list_techniques()):
        raise HTTPException(404, "Technique not found")
    return _favorites(owner, "add", card_id)


@router.delete("/favorites/{card_id}")
def remove_favorite(card_id: str, user: dict = Depends(get_api_user)) -> dict:
    return _favorites(_owner(user), "remove", card_id)
