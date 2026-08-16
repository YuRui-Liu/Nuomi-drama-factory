"""Checkpointed orchestration for image production stages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from novelvideo.media_capabilities.image.grid_plan import GridPlan
from novelvideo.media_capabilities.image.grsai import GrsaiClient, GrsaiSnapshot
from novelvideo.media_capabilities.image.postprocess import SplitOptions, split_grid
from novelvideo.media_capabilities.models import (
    ImageGenerationRequest,
    MediaCapability,
)
from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderTaskSnapshot,
    RunningHubClient,
)


STAGES = ("grsai_generate", "upscale", "local_split", "quality", "register")


class ImagePipelinePending(RuntimeError):
    """Raised when a submitted remote stage has not completed yet."""


class _RunningHub(Protocol):
    async def upload(self, path: str | Path) -> str: ...

    async def submit(self, workflow_id: str, node_info: Any) -> str: ...

    async def query(self, task_id: str) -> ProviderTaskSnapshot: ...

    async def download(self, url: str) -> bytes: ...


SplitGrid = Callable[[str | Path, GridPlan, str | Path, SplitOptions], list[Path]]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _input_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ImageProductionPipeline:
    """Run image stages while preserving completed remote work across retries."""

    def __init__(
        self,
        *,
        checkpoint_dir: str | Path,
        grsai: GrsaiClient | None = None,
        runninghub: RunningHubClient | _RunningHub | None = None,
        upscale_workflow_id: str | None = None,
        split: SplitGrid = split_grid,
    ) -> None:
        self._checkpoint_dir = Path(checkpoint_dir)
        self._grsai = grsai
        self._runninghub = runninghub
        self._upscale_workflow_id = upscale_workflow_id
        self._split = split

    async def generate_group(
        self,
        *,
        capability: MediaCapability | str,
        group_id: str,
        output_dir: str | Path,
        plan: GridPlan | None = None,
        source: str | Path | None = None,
        request: ImageGenerationRequest | None = None,
        grsai_api_key: str | None = None,
        upscale_node_info: Any = None,
        split_options: SplitOptions | None = None,
    ) -> list[Path]:
        capability = MediaCapability(capability)
        if not group_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in group_id):
            raise ValueError("group_id must contain only letters, digits, '.', '_', or '-'")

        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        checkpoint_path = self._checkpoint_dir / f"{group_id}.json"
        checkpoint = self._load_checkpoint(checkpoint_path, group_id)

        if capability == MediaCapability.IMAGE_GRID_UPSCALE_SPLIT:
            if source is None or plan is None:
                raise ValueError("image.grid_upscale_split requires source and plan")
            current = Path(source)
            self._record_skipped(checkpoint_path, checkpoint, "grsai_generate", capability)
        else:
            if request is None or grsai_api_key is None:
                raise ValueError("generated image capabilities require request and grsai_api_key")
            current = await self._generate(
                checkpoint_path,
                checkpoint,
                group_id=group_id,
                output_dir=destination,
                request=request,
                api_key=grsai_api_key,
                plan=plan,
            )

        if capability in {
            MediaCapability.IMAGE_STORYBOARD_GRID,
            MediaCapability.IMAGE_GRID_UPSCALE_SPLIT,
        }:
            if plan is None:
                raise ValueError(f"{capability.value} requires plan")
            current = await self._upscale(
                checkpoint_path,
                checkpoint,
                group_id=group_id,
                source=current,
                node_info=upscale_node_info,
                output_dir=destination,
            )
            outputs = self._local_split(
                checkpoint_path,
                checkpoint,
                source=current,
                plan=plan,
                output_dir=destination,
                options=split_options or SplitOptions(),
            )
        else:
            self._record_skipped(checkpoint_path, checkpoint, "upscale", capability)
            self._record_skipped(checkpoint_path, checkpoint, "local_split", capability)
            outputs = [current]

        self._quality(checkpoint_path, checkpoint, outputs)
        self._register(checkpoint_path, checkpoint, outputs)
        return outputs

    async def _generate(
        self,
        checkpoint_path: Path,
        checkpoint: dict[str, Any],
        *,
        group_id: str,
        output_dir: Path,
        request: ImageGenerationRequest,
        api_key: str,
        plan: GridPlan | None,
    ) -> Path:
        if self._grsai is None:
            raise ValueError("GRSAI client is required")
        stage_hash = _input_hash(
            {
                "request": request.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json") if plan else None,
            }
        )
        stage = self._matching_stage(checkpoint, "grsai_generate", stage_hash)
        output = output_dir / f"{group_id}.generated.png"
        if self._valid_output(stage, output):
            return output

        task_id = stage.get("task_id") if stage else None
        if not task_id:
            self._save_stage(checkpoint_path, checkpoint, "grsai_generate", stage_hash, "running")
            task_id = await self._grsai.submit(request, api_key=api_key)
            self._save_stage(
                checkpoint_path,
                checkpoint,
                "grsai_generate",
                stage_hash,
                "submitted",
                task_id=task_id,
            )

        snapshot = await self._grsai.query(str(task_id), api_key=api_key)
        if snapshot.status != "succeeded":
            status = "running" if snapshot.status in {"running", "queued", "submitted"} else "failed"
            self._save_stage(
                checkpoint_path,
                checkpoint,
                "grsai_generate",
                stage_hash,
                status,
                task_id=task_id,
            )
            if status == "running":
                raise ImagePipelinePending("grsai_generate.pending")
            raise RuntimeError("grsai_generate.failed")

        url = self._grsai_result_url(snapshot)
        data = await self._download_grsai(url)
        _atomic_write(output, data)
        self._save_stage(
            checkpoint_path,
            checkpoint,
            "grsai_generate",
            stage_hash,
            "succeeded",
            task_id=task_id,
            output=str(output),
            output_sha256=_sha256_bytes(data),
        )
        return output

    async def _upscale(
        self,
        checkpoint_path: Path,
        checkpoint: dict[str, Any],
        *,
        group_id: str,
        source: Path,
        node_info: Any,
        output_dir: Path,
    ) -> Path:
        if self._runninghub is None or not self._upscale_workflow_id:
            raise ValueError("RunningHub client and upscale_workflow_id are required")
        if not source.is_file():
            raise FileNotFoundError(source)
        stage_hash = _input_hash(
            {
                "source_sha256": _sha256_file(source),
                "workflow_id": self._upscale_workflow_id,
                "node_info": node_info,
            }
        )
        stage = self._matching_stage(checkpoint, "upscale", stage_hash)
        output = output_dir / f"{group_id}.upscaled.png"
        if self._valid_output(stage, output):
            return output

        task_id = stage.get("task_id") if stage else None
        if not task_id:
            self._save_stage(checkpoint_path, checkpoint, "upscale", stage_hash, "running")
            remote_source = await self._runninghub.upload(source)
            submitted_node_info = self._bind_uploaded_source(node_info, remote_source)
            task_id = await self._runninghub.submit(
                self._upscale_workflow_id, submitted_node_info
            )
            self._save_stage(
                checkpoint_path,
                checkpoint,
                "upscale",
                stage_hash,
                "submitted",
                task_id=task_id,
            )

        snapshot = await self._runninghub.query(str(task_id))
        if snapshot.status != "succeeded":
            status = "running" if snapshot.status in {"running", "queued", "submitted"} else "failed"
            self._save_stage(
                checkpoint_path,
                checkpoint,
                "upscale",
                stage_hash,
                status,
                task_id=task_id,
            )
            if status == "running":
                raise ImagePipelinePending("upscale.pending")
            raise RuntimeError("upscale.failed")
        if not snapshot.results:
            raise RuntimeError("upscale.missing_result")

        data = await self._runninghub.download(snapshot.results[0].url)
        _atomic_write(output, data)
        self._save_stage(
            checkpoint_path,
            checkpoint,
            "upscale",
            stage_hash,
            "succeeded",
            task_id=task_id,
            output=str(output),
            output_sha256=_sha256_bytes(data),
        )
        return output

    def _local_split(
        self,
        checkpoint_path: Path,
        checkpoint: dict[str, Any],
        *,
        source: Path,
        plan: GridPlan,
        output_dir: Path,
        options: SplitOptions,
    ) -> list[Path]:
        stage_hash = _input_hash(
            {
                "source_sha256": _sha256_file(source),
                "plan": plan.model_dump(mode="json"),
                "options": options.model_dump(mode="json"),
            }
        )
        stage = self._matching_stage(checkpoint, "local_split", stage_hash)
        if stage and stage.get("status") == "succeeded":
            outputs = [Path(path) for path in stage.get("outputs", [])]
            hashes = stage.get("output_sha256", [])
            if len(outputs) == len(hashes) and all(
                path.is_file() and _sha256_file(path) == digest
                for path, digest in zip(outputs, hashes, strict=True)
            ):
                return outputs

        self._save_stage(checkpoint_path, checkpoint, "local_split", stage_hash, "running")
        try:
            outputs = self._split(source, plan, output_dir, options)
        except Exception:
            self._save_stage(checkpoint_path, checkpoint, "local_split", stage_hash, "failed")
            raise
        self._save_stage(
            checkpoint_path,
            checkpoint,
            "local_split",
            stage_hash,
            "succeeded",
            outputs=[str(path) for path in outputs],
            output_sha256=[_sha256_file(path) for path in outputs],
        )
        return outputs

    def _quality(
        self,
        checkpoint_path: Path,
        checkpoint: dict[str, Any],
        outputs: Sequence[Path],
    ) -> None:
        stage_hash = _input_hash({"outputs": self._output_hashes(outputs)})
        stage = self._matching_stage(checkpoint, "quality", stage_hash)
        if stage and stage.get("status") == "succeeded":
            return
        self._save_stage(checkpoint_path, checkpoint, "quality", stage_hash, "running")
        try:
            for output in outputs:
                with Image.open(output) as image:
                    image.verify()
        except Exception:
            self._save_stage(checkpoint_path, checkpoint, "quality", stage_hash, "failed")
            raise
        self._save_stage(checkpoint_path, checkpoint, "quality", stage_hash, "succeeded")

    def _register(
        self,
        checkpoint_path: Path,
        checkpoint: dict[str, Any],
        outputs: Sequence[Path],
    ) -> None:
        output_hashes = self._output_hashes(outputs)
        stage_hash = _input_hash({"outputs": output_hashes})
        stage = self._matching_stage(checkpoint, "register", stage_hash)
        if stage and stage.get("status") == "succeeded":
            return
        self._save_stage(
            checkpoint_path,
            checkpoint,
            "register",
            stage_hash,
            "succeeded",
            outputs=[str(path) for path in outputs],
            output_sha256=output_hashes,
        )

    @staticmethod
    def _load_checkpoint(path: Path, group_id: str) -> dict[str, Any]:
        if not path.exists():
            return {"version": 1, "group_id": group_id, "stages": {}}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("group_id") != group_id:
            raise ValueError("image.invalid_checkpoint")
        stages = payload.get("stages")
        if not isinstance(stages, dict):
            raise ValueError("image.invalid_checkpoint")
        return payload

    @staticmethod
    def _matching_stage(
        checkpoint: Mapping[str, Any], stage: str, stage_hash: str
    ) -> dict[str, Any] | None:
        record = checkpoint["stages"].get(stage)
        if isinstance(record, dict) and record.get("input_hash") == stage_hash:
            return record
        return None

    @staticmethod
    def _save_stage(
        checkpoint_path: Path,
        checkpoint: dict[str, Any],
        stage: str,
        stage_hash: str,
        status: str,
        **values: Any,
    ) -> None:
        checkpoint["stages"][stage] = {
            "input_hash": stage_hash,
            "status": status,
            **values,
        }
        _atomic_write(
            checkpoint_path,
            json.dumps(
                checkpoint,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )

    def _record_skipped(
        self,
        checkpoint_path: Path,
        checkpoint: dict[str, Any],
        stage: str,
        capability: MediaCapability,
    ) -> None:
        stage_hash = _input_hash(
            {"capability": capability.value, "stage": stage, "skipped": True}
        )
        matching = self._matching_stage(checkpoint, stage, stage_hash)
        if not matching or matching.get("status") != "skipped":
            self._save_stage(
                checkpoint_path, checkpoint, stage, stage_hash, "skipped"
            )

    @staticmethod
    def _valid_output(stage: Mapping[str, Any] | None, output: Path) -> bool:
        return bool(
            stage
            and stage.get("status") == "succeeded"
            and stage.get("output") == str(output)
            and output.is_file()
            and _sha256_file(output) == stage.get("output_sha256")
        )

    @staticmethod
    def _output_hashes(outputs: Sequence[Path]) -> list[str]:
        return [_sha256_file(path) for path in outputs]

    @staticmethod
    def _bind_uploaded_source(node_info: Any, remote_source: str) -> Any:
        if node_info is None:
            return [{"fieldName": "image", "fieldValue": remote_source}]
        if not isinstance(node_info, list):
            return node_info
        bound: list[Any] = []
        replaced = False
        for item in node_info:
            if isinstance(item, dict) and item.get("fieldName") in {"image", "file", "filename"}:
                item = {**item, "fieldValue": remote_source}
                replaced = True
            bound.append(item)
        if not replaced:
            bound.append({"fieldName": "image", "fieldValue": remote_source})
        return bound

    @staticmethod
    def _grsai_result_url(snapshot: GrsaiSnapshot) -> str:
        if not snapshot.results:
            raise RuntimeError("grsai_generate.missing_result")
        result = snapshot.results[0]
        for key in ("url", "fileUrl", "downloadUrl"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
        raise RuntimeError("grsai_generate.missing_result_url")

    async def _download_grsai(self, url: str) -> bytes:
        assert self._grsai is not None
        download = getattr(self._grsai, "download", None)
        if download is not None:
            return await download(url)
        response = await self._grsai.http.get(url)
        response.raise_for_status()
        return response.content


__all__ = ["ImagePipelinePending", "ImageProductionPipeline", "STAGES"]
