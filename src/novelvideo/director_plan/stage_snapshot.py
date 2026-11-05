"""Shared wire contract for the editable 3D proxy stage (not reconstructed geometry)."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue, model_validator

Vector = tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class StageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StageCamera(StageModel):
    azim: FiniteFloat
    elev: FiniteFloat
    distance: float = Field(ge=0, allow_inf_nan=False)
    focalPoint: Vector
    fov: float = Field(default=60, gt=0, lt=180, allow_inf_nan=False)


class WorldPlacement(StageModel):
    space: Literal["world"]
    position: Vector
    yawDeg: FiniteFloat


class PanoramaPlacement(StageModel):
    space: Literal["pano_view"]
    yawDeg: FiniteFloat
    pitchDeg: FiniteFloat
    distance: float = Field(gt=0, allow_inf_nan=False)


class StageMount(StageModel):
    kind: Literal["prop", "staging"]
    index: int = Field(ge=0)
    attachPointId: str = Field(min_length=1, max_length=128)


class StageMarker(StageModel):
    id: str | None = Field(default=None, min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    position: Vector
    yawDeg: FiniteFloat
    scale: tuple[Annotated[FiniteFloat, Field(gt=0)], Annotated[FiniteFloat, Field(gt=0)], Annotated[FiniteFloat, Field(gt=0)]]
    placement: Annotated[WorldPlacement | PanoramaPlacement, Field(discriminator="space")] | None = None
    pose: str | None = Field(default=None, max_length=128)
    actionPlaying: bool | None = None
    shapeHint: str | None = Field(default=None, max_length=128)
    mount: StageMount | None = None


class StageLight(StageModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    intensity: float = Field(ge=0, allow_inf_nan=False)
    position: Vector
    direction: Vector
    enabled: bool


class StageSnapshot(StageModel):
    schemaVersion: Literal[1]
    savedAt: float = Field(ge=0, allow_inf_nan=False)
    actors: list[StageMarker] = Field(max_length=100)
    props: list[StageMarker] = Field(max_length=100)
    stagings: list[StageMarker] = Field(max_length=100)
    camera: StageCamera | None = None
    outputCamera: StageCamera | None = None
    lights: list[StageLight] = Field(default_factory=list, max_length=100)
    # Viewer-specific source transforms and optional animation remain lossless.
    world: dict[str, JsonValue] | None = None
    timeline: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def valid_references(self):
        objects = [*self.actors, *self.props, *self.stagings]
        ids = [obj.id for obj in objects if obj.id is not None]
        if len(ids) != len(set(ids)) or len({light.id for light in self.lights}) != len(self.lights):
            raise ValueError("stage object and light ids must be unique")
        for obj in objects:
            if obj.mount:
                targets = self.props if obj.mount.kind == "prop" else self.stagings
                if obj.mount.index >= len(targets):
                    raise ValueError("stage mount target does not exist")
        # Reject NaN/Infinity also inside optional viewer metadata.
        import json
        json.dumps([self.world, self.timeline], allow_nan=False)
        return self


class StageCapture(StageModel):
    filename: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=2048)
    captured_at: float = Field(ge=0, allow_inf_nan=False)
    scene_id: str = Field(min_length=1, max_length=128)


def vector_text(value: Vector) -> str:
    return "(" + ", ".join(f"{item:g}" for item in value) + ")"


def marker_position(marker: StageMarker) -> str:
    placement = marker.placement
    if isinstance(placement, PanoramaPlacement):
        return (f"panorama-relative yaw={placement.yawDeg:g}°, pitch={placement.pitchDeg:g}°, "
                f"proxy distance={placement.distance:g}; not reconstructed world geometry")
    position = placement.position if isinstance(placement, WorldPlacement) else marker.position
    return f"3D proxy world xyz={vector_text(position)}"


def screen_position(marker: StageMarker, snapshot: StageSnapshot) -> str:
    """Horizontal camera projection is independent of the viewer's orbit scale."""
    import math
    camera = snapshot.outputCamera
    if camera is None or isinstance(marker.placement, PanoramaPlacement):
        return "position defined by the current captured 3D stage frame"
    position = marker.placement.position if isinstance(marker.placement, WorldPlacement) else marker.position
    delta = tuple(position[i] - camera.focalPoint[i] for i in range(3))
    azimuth = math.radians(camera.azim)
    horizontal = delta[0] * math.cos(azimuth) - delta[2] * math.sin(azimuth)
    side = "screen-center" if abs(horizontal) < 0.05 else "screen-right" if horizontal > 0 else "screen-left"
    radius = (snapshot.world or {}).get("sceneRadius")
    if isinstance(radius, (float, int)) and not isinstance(radius, bool) and radius > 0:
        elevation = math.radians(camera.elev)
        back = (math.cos(elevation) * math.sin(azimuth), -math.sin(elevation), math.cos(elevation) * math.cos(azimuth))
        distance = camera.distance * radius / max(1e-6, math.sin(math.radians(camera.fov) / 2))
        depth = distance - sum(delta[i] * back[i] for i in range(3))
        if depth <= 0:
            return "behind the current output camera; outside the frame"
    return f"{side} of the current 3D output camera optical axis"


def stage_composition(snapshot: StageSnapshot) -> str:
    rows = [f"{marker.id or marker.label}: {screen_position(marker, snapshot)}"
            for marker in [*snapshot.actors, *snapshot.props]]
    return "; ".join(rows) or "current 3D output camera framing of the scene landmarks"
