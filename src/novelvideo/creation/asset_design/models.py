"""A 切片（资产设计）接口契约。

本模块只冻结字段名与类型，不含业务逻辑；编译器与校验器由后续切片实现。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AssetKind = Literal["character", "scene", "prop"]
FactField = Literal[
    "age",
    "gender",
    "hair",
    "face",
    "body",
    "wardrobe",
    "carried_item",
    "other",
    "location",
    "era",
    "weather",
    "time_of_day",
    "light",
    "landmark",
    "material",
    "color",
    "scale",
    "state",
    "owner",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactSource(_Model):
    episode: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)


class Fact(_Model):
    field: FactField
    value: str = Field(min_length=1, max_length=400)
    source: FactSource


class Wardrobe(_Model):
    piece: str = Field(min_length=1, max_length=120)
    color: str = ""
    material: str = ""
    state: str = ""


class Consistency(_Model):
    anchors: list[str] = Field(default_factory=list, max_length=12)
    forbidden: list[str] = Field(default_factory=list, max_length=12)


class Render(_Model):
    material: str = ""
    volume: str = ""
    negative: list[str] = Field(default_factory=list, max_length=40)


class Admission(_Model):
    confirmed: bool = False
    confirmed_by: str = ""
    basis: str = ""


class PanoViewpoint(_Model):
    label: str = Field(min_length=1, max_length=40)
    yaw: float
    pitch: float = 0.0
    fov: float = 60.0


class PanoDesign(_Model):
    front_anchor: str = ""
    back_anchor: str = ""
    viewpoints: list[PanoViewpoint] = Field(default_factory=list, max_length=24)


class AssetDesignBrief(_Model):
    schema_version: Literal[1] = 1
    asset_kind: AssetKind
    asset_id: str = Field(min_length=1, max_length=128)
    variant_id: str | None = Field(default=None, max_length=128)
    facts: list[Fact] = Field(default_factory=list, max_length=200)
    design: dict[str, Any] = Field(default_factory=dict)
    consistency: Consistency = Field(default_factory=Consistency)
    render: Render = Field(default_factory=Render)
    pano: PanoDesign | None = None
    admission: Admission = Field(default_factory=Admission)


class Issue(_Model):
    code: str = Field(min_length=1, max_length=80)
    severity: Literal["error", "warning"]
    message: str = Field(min_length=1, max_length=1000)
    field: str = ""


class Fingerprint(_Model):
    brief_hash: str = ""
    style_id: str = ""
    style_revision: str = ""
    design_schema_revision: int = 0
    compiler_version: str = "1"


class CompileResult(_Model):
    compiled: dict[str, Any] = Field(default_factory=dict)
    prompt_text: str = ""
    style_fragment: str = ""
    fingerprint: Fingerprint = Field(default_factory=Fingerprint)
    issues: list[Issue] = Field(default_factory=list)
