"""One concurrency and billing boundary for a complete GRSAI image call."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from novelvideo.media_capabilities.image.grsai import (
    GrsaiError,
    GrsaiPolicyViolation,
    GrsaiSnapshot,
)
from novelvideo.media_capabilities.models import ImageGenerationRequest
from novelvideo.ports import get_usage_meter


@dataclass(frozen=True, slots=True)
class GrsaiExecutionResult:
    task_id: str
    snapshot: GrsaiSnapshot
    content: bytes


async def _refund_once(meter: Any, reservation_id: str, error: BaseException) -> None:
    if not reservation_id:
        return
    try:
        await meter.refund_model_call_credit_reservation(
            reservation_id,
            metadata={
                "source": "grsai_image_runtime",
                "error": str(error)[:200],
            },
        )
    except Exception:
        # Billing telemetry must not replace the provider exception.
        return


async def _confirm_once(
    meter: Any,
    *,
    reservation_id: str,
    model: str,
    task_id: str,
) -> None:
    try:
        await meter.bump_model_call(
            user_id=None,
            model=model,
            provider_request_id=task_id,
            credit_reservation_id=reservation_id,
            metadata={"source": "grsai_image_runtime", "response_id": task_id},
        )
    except Exception:
        # Match the existing image gateways: usage reporting is best effort after
        # a provider result has already been downloaded successfully.
        return


def _result_url(snapshot: GrsaiSnapshot) -> str:
    if not snapshot.results:
        return ""
    first = snapshot.results[0]
    return next(
        (
            str(first.get(key) or "").strip()
            for key in ("url", "fileUrl", "downloadUrl")
            if first.get(key)
        ),
        "",
    )


def _is_wrapped_connect_error(error: GrsaiError) -> bool:
    return str(error).startswith("grsai.connect_error ")


async def execute_grsai_generation(
    runtime: Any,
    request: ImageGenerationRequest,
    *,
    usage_meter: Any | None = None,
    poll_interval_seconds: float = 2.0,
    timeout_seconds: float = 300.0,
) -> GrsaiExecutionResult:
    """Submit, poll and download one image under one lease and reservation."""

    meter = usage_meter or get_usage_meter()
    model = str(request.model or runtime.model)
    provider_id = str(runtime.account.id)
    async with runtime.concurrency.lease(provider_id, request.capability):
        reservation_id = await meter.reserve_current_model_call_credit(
            model=model,
            billing_kind="image",
            billing_params={
                "aspect_ratio": request.aspect_ratio,
                "image_size": request.image_size,
            },
            billing_quantity=1,
            metadata={
                "source": "grsai_image_runtime",
                "provider_id": provider_id,
                "capability": request.capability.value,
            },
        )
        client = None
        try:
            client = runtime.create_client()
            try:
                task_id = await client.submit(request, api_key=runtime.api_key)
                deadline = time.monotonic() + max(0.01, timeout_seconds)
                while True:
                    try:
                        snapshot = await client.query(
                            task_id, api_key=runtime.api_key
                        )
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if (
                            status not in {404, 408, 409, 425, 429}
                            and status < 500
                        ) or time.monotonic() >= deadline:
                            raise
                        await asyncio.sleep(max(0.01, poll_interval_seconds))
                        continue
                    except (
                        httpx.TimeoutException,
                        httpx.NetworkError,
                        json.JSONDecodeError,
                    ):
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f"GRSAI image generation timed out: {task_id}"
                            ) from None
                        await asyncio.sleep(max(0.01, poll_interval_seconds))
                        continue
                    except GrsaiError as exc:
                        if (
                            not _is_wrapped_connect_error(exc)
                            or time.monotonic() >= deadline
                        ):
                            raise
                        await asyncio.sleep(max(0.01, poll_interval_seconds))
                        continue
                    if snapshot.status == "succeeded":
                        break
                    if snapshot.status == "violation":
                        raise GrsaiPolicyViolation(
                            "grsai.policy_violation status=violation"
                        )
                    if snapshot.status == "failed":
                        raise GrsaiError("grsai.generation_failed status=failed")
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"GRSAI image generation timed out: {task_id}"
                        )
                    await asyncio.sleep(max(0.01, poll_interval_seconds))
                url = _result_url(snapshot)
                if not url:
                    raise GrsaiError("grsai.result_missing_url")
                while True:
                    try:
                        content = await client.download(url)
                        break
                    except GrsaiError as exc:
                        if (
                            not _is_wrapped_connect_error(exc)
                            or time.monotonic() >= deadline
                        ):
                            raise
                        await asyncio.sleep(max(0.01, poll_interval_seconds))
                if not content:
                    raise GrsaiError("grsai.download_empty_body")
            finally:
                await client.http.aclose()
        except (Exception, asyncio.CancelledError) as exc:
            refund = asyncio.create_task(_refund_once(meter, reservation_id, exc))
            try:
                await asyncio.shield(refund)
            except asyncio.CancelledError:
                await refund
            raise
        confirmation = asyncio.create_task(
            _confirm_once(
                meter,
                reservation_id=reservation_id,
                model=model,
                task_id=task_id,
            )
        )
        try:
            await asyncio.shield(confirmation)
        except asyncio.CancelledError:
            await confirmation
            raise
        return GrsaiExecutionResult(task_id, snapshot, content)


__all__ = ["GrsaiExecutionResult", "execute_grsai_generation"]
