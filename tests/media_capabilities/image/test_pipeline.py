from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from novelvideo.media_capabilities.image.grid_plan import GridPlan
from novelvideo.media_capabilities.image.pipeline import ImageProductionPipeline
from novelvideo.media_capabilities.image.postprocess import split_grid
from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderResult,
    ProviderTaskSnapshot,
)


class SuccessfulUpscaler:
    def __init__(self, output: bytes) -> None:
        self.output = output
        self.submit_calls = 0

    async def upload(self, path: str | Path) -> str:
        return Path(path).name

    async def submit(self, workflow_id: str, node_info: object) -> str:
        self.submit_calls += 1
        return "upscale-task-1"

    async def query(self, task_id: str) -> ProviderTaskSnapshot:
        return ProviderTaskSnapshot(
            status="succeeded",
            results=(ProviderResult(url="https://cdn.invalid/upscaled.png"),),
        )

    async def download(self, url: str) -> bytes:
        return self.output


def _grid_png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (24, 32), (30, 60, 90)).save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.asyncio
async def test_retry_after_local_split_failure_does_not_resubmit_successful_upscale(
    tmp_path: Path,
) -> None:
    source = tmp_path / "grid.png"
    source.write_bytes(_grid_png())
    plan = GridPlan(
        layout="2x2",
        cell_aspect_ratio="9:16",
        cell_mapping=("s1", "s2", "s3", "s4"),
    )
    upscaler = SuccessfulUpscaler(_grid_png())
    split_calls = 0

    def fail_once_split(
        source_path: str | Path,
        grid_plan: GridPlan,
        output_dir: str | Path,
        options: object = None,
    ) -> list[Path]:
        nonlocal split_calls
        split_calls += 1
        if split_calls == 1:
            raise RuntimeError("local split failed")
        return split_grid(source_path, grid_plan, output_dir)

    pipeline = ImageProductionPipeline(
        checkpoint_dir=tmp_path / "checkpoints",
        runninghub=upscaler,
        upscale_workflow_id="upscale-workflow",
        split=fail_once_split,
    )
    arguments = {
        "capability": MediaCapability.IMAGE_GRID_UPSCALE_SPLIT,
        "group_id": "group-1",
        "source": source,
        "plan": plan,
        "output_dir": tmp_path / "output",
        "upscale_node_info": [{"nodeId": "1", "fieldName": "image"}],
    }

    with pytest.raises(RuntimeError, match="local split failed"):
        await pipeline.generate_group(**arguments)

    outputs = await pipeline.generate_group(**arguments)

    assert upscaler.submit_calls == 1
    assert split_calls == 2
    assert [path.name for path in outputs] == [
        "group-1.upscaled_s1.png",
        "group-1.upscaled_s2.png",
        "group-1.upscaled_s3.png",
        "group-1.upscaled_s4.png",
    ]
    assert all(path.is_file() for path in outputs)
