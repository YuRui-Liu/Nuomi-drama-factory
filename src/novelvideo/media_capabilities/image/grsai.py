"""Async GRSAI image-generation adapter."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.media_capabilities.models import ImageGenerationRequest
from novelvideo.media_capabilities.models import DEFAULT_GRSAI_IMAGE_MODEL


class GrsaiError(RuntimeError):
    """Raised when GRSAI rejects a generation request."""


class GrsaiSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    results: list[dict[str, Any]] = Field(default_factory=list)


class GrsaiClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        default_model: str = DEFAULT_GRSAI_IMAGE_MODEL,
    ) -> None:
        self.http = http
        self.default_model = default_model

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}

    @staticmethod
    def _encode_reference(reference: str) -> str:
        if reference.startswith(("data:", "http://", "https://")):
            return reference

        path = Path(reference)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    async def submit(
        self,
        request: ImageGenerationRequest,
        *,
        api_key: str,
    ) -> str:
        model = request.model or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "prompt": request.prompt,
            "images": [
            self._encode_reference(reference) for reference in request.references
            ],
            "replyType": "async",
        }
        if model.startswith("nano-banana"):
            if request.aspect_ratio:
                payload["aspectRatio"] = request.aspect_ratio
            if request.image_size:
                payload["imageSize"] = request.image_size
        else:
            output_size = request.image_size or request.aspect_ratio
            if output_size:
                payload["aspectRatio"] = output_size
        response = await self.http.post(
            "/v1/api/generate",
            headers=self._headers(api_key),
            json=payload,
        )
        response.raise_for_status()
        response_payload = response.json()
        if response_payload.get("status") not in {"running", "succeeded"}:
            raise GrsaiError("grsai.rejected")
        return str(response_payload["id"])

    async def query(self, task_id: str, *, api_key: str) -> GrsaiSnapshot:
        response = await self.http.get(
            "/v1/api/result",
            params={"id": task_id},
            headers=self._headers(api_key),
        )
        response.raise_for_status()
        return GrsaiSnapshot.model_validate(response.json())


__all__ = ["GrsaiClient", "GrsaiError", "GrsaiSnapshot"]
