"""360 场景 brief 的视角切片：复用 `director_world.pano_views` 的等距柱状投影。

切片结果只取决于源全景图与视角参数（同参数同字节），便于回归比对。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from novelvideo.director_world.pano_views import equirectangular_to_perspective


def slice_pano(
    source: str | Path,
    output: str | Path,
    *,
    yaw: float,
    pitch: float,
    fov: float,
    width: int = 1024,
    height: int = 768,
) -> Path:
    if not -180 <= yaw <= 180 or not -90 <= pitch <= 90 or not 10 <= fov <= 120:
        raise ValueError("yaw/pitch/fov out of range")
    with Image.open(source) as pano:
        sliced = equirectangular_to_perspective(
            pano,
            yaw_deg=yaw,
            pitch_deg=pitch,
            fov_deg=fov,
            width=width,
            height=height,
        )
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    sliced.save(target)
    return target
