"""准入：新建资产必须有人显式确认，并留下确认人与依据。

已有资产（`asset_exists=True`）不走准入，避免每次改 brief 都重新确认。
"""

from __future__ import annotations

from collections.abc import Mapping

from .models import Issue


def check_admission(brief: Mapping, *, asset_exists: bool) -> list[Issue]:
    if asset_exists:
        return []
    admission = brief.get("admission") or {}
    if not admission.get("confirmed"):
        return [
            Issue(
                code="brief_admission_missing",
                severity="error",
                message="new asset requires explicit admission confirmation",
                field="admission",
            )
        ]
    if not admission.get("confirmed_by") or not admission.get("basis"):
        return [
            Issue(
                code="brief_admission_missing",
                severity="error",
                message="admission requires confirmed_by and basis",
                field="admission",
            )
        ]
    return []
