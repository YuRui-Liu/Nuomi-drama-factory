"""Local Codex and Ollama knowledge-runtime settings API."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.routes.media_capabilities import require_media_capability_admin
from novelvideo.knowledge_runtime import (
    KnowledgeRuntimeError,
    OllamaSettings,
    get_codex_cli_status,
    list_ollama_models,
    load_knowledge_runtime_settings,
    probe_ollama_embedding,
    save_knowledge_runtime_settings,
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OllamaProbeBody(_Body):
    base_url: str = Field(alias="baseUrl")
    model: str


class KnowledgeRuntimeSettingsBody(OllamaProbeBody):
    batch_size: int = Field(default=8, alias="batchSize", gt=0)


router = APIRouter(
    prefix="/knowledge-runtime",
    dependencies=[Depends(require_media_capability_admin)],
)


def _ollama_data(settings: OllamaSettings) -> dict[str, Any]:
    return {
        "provider": settings.provider,
        "baseUrl": settings.base_url,
        "model": settings.model,
        "dimension": settings.dimension,
        "digest": settings.digest,
        "batchSize": settings.batch_size,
        "probedAt": settings.probed_at,
        "configured": bool(settings.model and settings.dimension > 0),
    }


def _probe_data(probe: Any) -> dict[str, Any]:
    return {
        "model": probe.model,
        "dimension": probe.dimension,
        "digest": probe.digest,
        "probedAt": probe.probed_at,
    }


def _error_response(exc: KnowledgeRuntimeError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"ok": False, "errorCode": exc.code, "message": str(exc)},
    )


@router.get("/status")
async def get_status() -> Any:
    try:
        settings = load_knowledge_runtime_settings()
        codex = await get_codex_cli_status()
    except KnowledgeRuntimeError as exc:
        return _error_response(exc)

    ollama = _ollama_data(settings)
    ready = codex.installed and codex.authenticated and ollama["configured"]
    if ready:
        state = "ready"
        message = "Codex 与 Ollama 知识运行时已就绪"
    elif not codex.installed or (codex.installed and not codex.authenticated):
        state = "unavailable"
        message = codex.message or "Codex CLI 不可用"
    else:
        state = "unconfigured"
        message = "请选择 Ollama Embedding 模型并测试保存"
    return {
        "ok": True,
        "data": {
            "ready": ready,
            "state": state,
            "message": message,
            "codex": asdict(codex),
            "ollama": ollama,
        },
    }


@router.get("/ollama/models")
async def get_ollama_models(
    base_url: str = Query(alias="baseUrl"),
) -> Any:
    try:
        models = await list_ollama_models(base_url)
    except KnowledgeRuntimeError as exc:
        return _error_response(exc)
    return {"ok": True, "data": models}


@router.post("/ollama/probe")
async def probe_ollama(body: OllamaProbeBody) -> Any:
    try:
        probe = await probe_ollama_embedding(body.base_url, body.model)
    except KnowledgeRuntimeError as exc:
        return _error_response(exc)
    return {"ok": True, "data": _probe_data(probe)}


@router.put("/settings")
async def put_settings(
    body: KnowledgeRuntimeSettingsBody,
) -> Any:
    try:
        probe = await probe_ollama_embedding(body.base_url, body.model)
        saved = save_knowledge_runtime_settings(
            OllamaSettings(
                base_url=body.base_url,
                model=body.model,
                batch_size=body.batch_size,
            ),
            probe=probe,
        )
    except KnowledgeRuntimeError as exc:
        return _error_response(exc)
    return {"ok": True, "data": _ollama_data(saved)}


@router.post("/codex/test")
async def test_codex() -> dict[str, Any]:
    status = await get_codex_cli_status()
    if status.installed and status.authenticated:
        return {"ok": True, "data": asdict(status)}
    return {
        "ok": False,
        "errorCode": (
            "CODEX_NOT_AUTHENTICATED" if status.installed else "CODEX_NOT_INSTALLED"
        ),
        "message": status.message,
        "data": asdict(status),
    }
