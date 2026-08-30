from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from novelvideo.director_plan.models import ShotPlan, StyleProjections, StyleSnapshot
from novelvideo.extension_styles.schema import FRAGMENT_KEYS, ExtensionStyle


@dataclass(frozen=True)
class ProjectionStyle:
    id: str
    version: str
    prompt_fragment: Mapping[str, tuple[str, ...]]
    panel_tag: str = ""


class StyleResolver:
    def __init__(self, catalog: Sequence[ExtensionStyle | ProjectionStyle]) -> None:
        self._styles = {style.id: _as_projection_style(style) for style in catalog}
        self._catalog_hash = _stable_hash(
            [_style_payload(self._styles[style_id]) for style_id in sorted(self._styles)]
        )

    def resolve(self, project_style: str, override: str | None) -> StyleSnapshot:
        style_id = override or project_style
        try:
            style = self._styles[style_id]
        except KeyError as exc:
            raise ValueError(f"Style {style_id!r} not found") from exc
        fragments = style.prompt_fragment
        projections = StyleProjections(
            director=_compile(fragments, ("medium", "camera", "constraints")),
            image=_compile(fragments, FRAGMENT_KEYS),
            video=_compile(fragments, ("medium", "rendering", "lighting", "color")),
            panel_tag=style.panel_tag.strip() or _panel_tag(fragments),
        )
        hash_payload = {
            "style_id": style.id,
            "style_version": style.version,
            "catalog_hash": self._catalog_hash,
            "projections": projections.model_dump(mode="json"),
        }
        style_hash = _stable_hash(hash_payload)
        return StyleSnapshot(
            snapshot_id=f"style-snapshot-{style_hash[:24]}",
            style_id=style.id,
            style_version=style.version,
            catalog_hash=self._catalog_hash,
            style_hash=style_hash,
            projections=projections,
        )


def apply_director_projection(shot: ShotPlan, snapshot: StyleSnapshot) -> ShotPlan:
    """Keep explicit shot camera language authoritative over style tendencies."""
    del snapshot
    return shot


def _as_projection_style(style: ExtensionStyle | ProjectionStyle) -> ProjectionStyle:
    if isinstance(style, ProjectionStyle):
        return style
    return ProjectionStyle(
        id=style.id,
        version=style.version,
        prompt_fragment=style.prompt_fragment,
    )


def _compile(fragments: Mapping[str, tuple[str, ...]], keys: Sequence[str]) -> str:
    return ", ".join(
        phrase.strip()
        for key in keys
        for phrase in fragments[key]
        if phrase.strip()
    )


def _panel_tag(fragments: Mapping[str, tuple[str, ...]]) -> str:
    return ", ".join(
        phrases[0].strip()
        for key in FRAGMENT_KEYS
        if (phrases := tuple(phrase for phrase in fragments[key] if phrase.strip()))
    )


def _style_payload(style: ProjectionStyle) -> dict[str, object]:
    return {
        "style_id": style.id,
        "style_version": style.version,
        "prompt_fragment": {
            key: list(style.prompt_fragment[key]) for key in FRAGMENT_KEYS
        },
        "panel_tag": style.panel_tag,
    }


def _stable_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
