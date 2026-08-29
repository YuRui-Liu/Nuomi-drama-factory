from __future__ import annotations

import importlib
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

import pytest
from fastapi import HTTPException

from novelvideo.knowledge_pipeline import (
    KNOWLEDGE_PIPELINE_STRUCTURED,
    STATUS_STRUCTURED_PENDING,
)


def test_aliyun_media_relay_sdk_is_packaged() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        dependency.split("[", 1)[0]
        .split("=", 1)[0]
        .split("<", 1)[0]
        .split(">", 1)[0]
        .strip()
        for dependency in pyproject["project"]["dependencies"]
    }

    assert "oss2" in dependencies


_STUB_MODULE_NAMES = (
    "novelvideo.api",
    "novelvideo.api.routes",
    "novelvideo.api.deps",
    "novelvideo.api.schemas",
    "novelvideo.api.routes.projects",
)
_MISSING_MODULE = object()
_saved_modules = {
    name: sys.modules.get(name, _MISSING_MODULE) for name in _STUB_MODULE_NAMES
}
_api_root = Path(__file__).parents[1] / "src" / "novelvideo" / "api"
_api_package = ModuleType("novelvideo.api")
_api_package.__path__ = [str(_api_root)]
_routes_package = ModuleType("novelvideo.api.routes")
_routes_package.__path__ = [str(_api_root / "routes")]
_deps = ModuleType("novelvideo.api.deps")
for _name in (
    "get_media_capability_store",
    "get_media_credential_resolver",
    "get_project_paths",
    "make_sqlite_store",
    "make_sqlite_store_for_context",
    "make_static_url_for_context",
    "validate_project_name",
):
    setattr(_deps, _name, None)
sys.modules["novelvideo.api"] = _api_package
sys.modules["novelvideo.api.routes"] = _routes_package
sys.modules["novelvideo.api.deps"] = _deps
ProjectCreate = importlib.import_module("novelvideo.api.schemas").ProjectCreate
projects = importlib.import_module("novelvideo.api.routes.projects")
for _module_name, _previous_module in _saved_modules.items():
    if _previous_module is _MISSING_MODULE:
        sys.modules.pop(_module_name, None)
    else:
        sys.modules[_module_name] = _previous_module



def _async_value(value):
    async def _value(*_args, **_kwargs):
        return value

    return _value


class _Registry:
    def __init__(self, root: Path) -> None:
        self.record = SimpleNamespace(
            id="proj-1",
            output_dir=root / "output",
            state_dir=root / "state",
            runtime_dir=root / "runtime",
        )

    async def create_project(self, **_kwargs):
        return self.record

    async def delete_uncommitted_project(self, _project_id: str) -> None:
        raise AssertionError("creation should not be compensated")


async def test_new_project_defaults_to_structured_pending_without_embedding_binding(
    tmp_path: Path, monkeypatch,
) -> None:
    registry = _Registry(tmp_path)
    monkeypatch.setattr(projects, "get_project_registry", lambda: registry)
    monkeypatch.setattr(projects, "user_id_from_api_user", _async_value("user-1"))
    monkeypatch.setattr(projects, "validate_project_name", lambda _name: None)
    response = await projects.create_project(
        ProjectCreate(name="Demo"), user={"username": "alice"}
    )
    assert response["data"]["id"] == "proj-1"
    assert response["data"]["knowledge_pipeline"] == KNOWLEDGE_PIPELINE_STRUCTURED
    assert response["data"]["knowledge_pipeline_status"] == STATUS_STRUCTURED_PENDING
    raw = json.loads(
        (registry.record.state_dir / "project_config.json").read_text(encoding="utf-8")
    )
    assert raw["knowledge_pipeline"] == KNOWLEDGE_PIPELINE_STRUCTURED
    assert raw["knowledge_pipeline_status"] == STATUS_STRUCTURED_PENDING
    assert "cognee_embedding_model" not in raw


class _Store:
    def __init__(self, count: int | Exception) -> None:
        self.count = count
        self.closed = False

    async def formal_asset_count(self) -> int:
        if isinstance(self.count, Exception):
            raise self.count
        return self.count

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("count", [1, RuntimeError("database unavailable")])
async def test_legacy_switch_is_fail_closed_for_assets_or_count_failure(
    tmp_path: Path, monkeypatch, count: int | Exception,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "project_config.json").write_text(
        json.dumps({
            "knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED,
            "knowledge_pipeline_status": STATUS_STRUCTURED_PENDING,
        }),
        encoding="utf-8",
    )
    ctx = SimpleNamespace(state_dir=state_dir)
    store = _Store(count)
    monkeypatch.setattr(projects, "resolve_project_context", _async_value(ctx))
    monkeypatch.setattr(projects, "require_project_home_node", lambda *_a, **_k: None)
    monkeypatch.setattr(
        projects, "make_sqlite_store_for_context", _async_value(store)
    )
    with pytest.raises(HTTPException) as exc_info:
        await projects.update_knowledge_pipeline(
            "proj-1",
            projects.KnowledgePipelineUpdate(knowledge_pipeline="cognee_legacy"),
            user={"username": "alice"},
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "KNOWLEDGE_PIPELINE_LOCKED"
    assert exc_info.value.detail["formal_asset_count"] == (
        count if isinstance(count, int) else None
    )
    assert store.closed is True
