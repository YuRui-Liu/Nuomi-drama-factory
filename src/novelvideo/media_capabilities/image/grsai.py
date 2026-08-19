"""Async GRSAI image-generation adapter."""

from __future__ import annotations

import base64
import json
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
        self._submitted_snapshots: dict[str, GrsaiSnapshot] = {}

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}

    @staticmethod
    def _response_detail(response: httpx.Response) -> str:
        content_type = str(response.headers.get("content-type") or "unknown")
        body_preview = " ".join(response.text[:500].split()) or "<empty>"
        return (
            f"status={response.status_code} content-type={content_type} "
            f"body={body_preview}"
        )

    @staticmethod
    def _encode_reference(reference: str) -> str:
        if reference.startswith(("data:", "http://", "https://")):
            return reference

        path = Path(reference)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    @staticmethod
    def _gpt_image_size(*, aspect_ratio: str | None, image_size: str | None) -> str:
        supported_sizes = {"1024x1024", "1024x1536", "1536x1024"}
        requested_size = str(image_size or "").strip()
        if requested_size in supported_sizes:
            return requested_size
        ratio = str(aspect_ratio or "").strip()
        if ratio in {"9:16", "2:3", "3:4", "4:5"}:
            return "1024x1536"
        if ratio in {"16:9", "3:2", "4:3", "5:4"}:
            return "1536x1024"
        return "1024x1024"

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
            "replyType": "json",
        }
        if model.startswith("nano-banana"):
            if request.aspect_ratio:
                payload["aspectRatio"] = request.aspect_ratio
            if request.image_size:
                payload["imageSize"] = request.image_size
        else:
            payload["aspectRatio"] = self._gpt_image_size(
                aspect_ratio=request.aspect_ratio,
                image_size=request.image_size,
            )
        response = await self.http.post(
            "/v1/api/generate",
            headers=self._headers(api_key),
            json=payload,
            timeout=300,
        )
        if not response.is_success:
            raise GrsaiError(f"grsai.http_error {self._response_detail(response)}")
        try:
            response_payload = response.json()
        except json.JSONDecodeError as exc:
            raise GrsaiError(
                f"grsai.invalid_response {self._response_detail(response)}"
            ) from exc
        snapshot = GrsaiSnapshot.model_validate(response_payload)
        if snapshot.status not in {"running", "succeeded"}:
            raise GrsaiError("grsai.rejected")
        if snapshot.status == "succeeded":
            self._submitted_snapshots[snapshot.id] = snapshot
        return snapshot.id

    async def query(self, task_id: str, *, api_key: str) -> GrsaiSnapshot:
        submitted = self._submitted_snapshots.get(task_id)
        if submitted is not None:
            return submitted
        response = await self.http.get(
            "/v1/api/result",
            params={"id": task_id},
            headers=self._headers(api_key),
        )
        response.raise_for_status()
        return GrsaiSnapshot.model_validate(response.json())


__all__ = ["GrsaiClient", "GrsaiError", "GrsaiSnapshot"]
