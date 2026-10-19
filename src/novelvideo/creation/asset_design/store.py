"""项目级资产 brief 的读写层。

brief 落在项目目录下的 `assets/<kind>/<id>[/variants/<variant>]/brief.v1.json`，
写入走"临时文件 + 原子替换"，并可选地校验读取时拿到的 hash，避免覆盖别人的改动。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import AssetDesignBrief


class AssetBriefNotFound(FileNotFoundError):
    """brief 文件不存在。"""


class AssetBriefConflict(ValueError):
    """brief 在读取之后被改过，拒绝覆盖。"""


class AssetBriefStore:
    """以项目根目录为基准的 brief 存储。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def relative_path(self, kind: str, asset_id: str, variant: str | None = None) -> Path:
        base = Path("assets") / kind / asset_id
        if variant:
            base = base / "variants" / variant
        return base / "brief.v1.json"

    def resolve(self, ref: str) -> tuple[str, str, str | None]:
        parts = ref.split("/")
        if len(parts) == 2:
            return parts[0], parts[1], None
        if len(parts) == 4 and parts[2] == "variants":
            return parts[0], parts[1], parts[3]
        raise ValueError("ref must be <kind>/<id> or <kind>/<id>/variants/<variant>")

    @staticmethod
    def hash(brief: AssetDesignBrief) -> str:
        payload = json.dumps(brief.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def read(self, ref: str) -> AssetDesignBrief:
        kind, asset_id, variant = self.resolve(ref)
        path = self.root / self.relative_path(kind, asset_id, variant)
        if not path.exists():
            raise AssetBriefNotFound(str(path))
        return AssetDesignBrief.model_validate_json(path.read_text(encoding="utf-8"))

    def write(self, brief: AssetDesignBrief, *, expected_hash: str | None) -> dict[str, str]:
        if brief.variant_id:
            base = self.root / self.relative_path(brief.asset_kind, brief.asset_id, None)
            if not base.exists():
                raise ValueError("variant requires an existing base brief")
        path = self.root / self.relative_path(brief.asset_kind, brief.asset_id, brief.variant_id)
        if expected_hash is not None:
            current_hash = self.hash(self.read(brief_ref(brief))) if path.exists() else ""
            if current_hash != expected_hash:
                raise AssetBriefConflict("brief changed since it was read")
        path.parent.mkdir(parents=True, exist_ok=True)
        text = brief.model_dump_json(indent=2)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return {"path": str(path.relative_to(self.root)), "hash": self.hash(brief)}


def brief_ref(brief: AssetDesignBrief) -> str:
    ref = f"{brief.asset_kind}/{brief.asset_id}"
    if brief.variant_id:
        ref = f"{ref}/variants/{brief.variant_id}"
    return ref
