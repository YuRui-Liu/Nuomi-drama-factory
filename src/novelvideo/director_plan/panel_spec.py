"""B 切片（导演分镜）PanelSpec 接口契约。

本模块只冻结字段名与函数签名，编译逻辑由 B 切片实现。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PanelSection = Literal[
    "count",
    "shot",
    "axis",
    "blocking",
    "lighting",
    "performance",
    "props",
    "continuity",
]


class PanelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: dict[str, str] = Field(default_factory=dict)
    prompt_text: str = ""
    negatives: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def compile_panel_spec(shot, group) -> PanelSpec:
    raise NotImplementedError("B slice implements compile_panel_spec")
