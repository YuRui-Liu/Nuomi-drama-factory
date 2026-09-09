from __future__ import annotations

import json
import logging
from uuid import uuid4

from .models import (
    AssetDiff,
    AssetImportPreview,
    AssetImportResult,
    AssetType,
    FieldChange,
)
from .parsing import decode_asset_table, extract_candidates, normalize_asset_name

logger = logging.getLogger(__name__)

TECHNICAL_DEFAULTS = {
    AssetType.CHARACTER: {"age_group": "youth"},
    AssetType.SCENE: {"scene_type": "interior"},
    AssetType.PROP: {"prop_type": "object"},
}


def _empty(value, *, field: str, kind: AssetType) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    return TECHNICAL_DEFAULTS.get(kind, {}).get(field) == value


class AssetImportService:
    def __init__(self, store):
        self.store = store

    async def _matches(self, kind: AssetType, name: str):
        records = (
            self.store.get_all_characters()
            if kind == AssetType.CHARACTER
            else await (
                self.store.list_scenes()
                if kind == AssetType.SCENE
                else self.store.list_props()
            )
        )
        key = normalize_asset_name(name)
        exact = [item for item in records if normalize_asset_name(item.name) == key]
        aliases = [
            item
            for item in records
            if any(normalize_asset_name(alias) == key for alias in (item.aliases or []))
        ]
        return list({item.name: item for item in [*exact, *aliases]}.values())

    async def _diffs(self, kind: AssetType, candidates) -> list[AssetDiff]:
        diffs = []
        for item in candidates:
            matches = await self._matches(kind, item.name)
            if len(matches) > 1:
                diffs.append(
                    AssetDiff(
                        name=item.name,
                        source_code=item.source_code,
                        disposition="conflict",
                        warnings=["名称或别名匹配到多个现有资产"],
                    )
                )
                continue
            current = matches[0] if matches else None
            changes = []
            for field, proposed in item.fields.items():
                old = getattr(current, field, None) if current else None
                evidence = [ev for ev in item.evidence if ev.field == field]
                fillable = current is None or _empty(old, field=field, kind=kind)
                if current is not None and old == TECHNICAL_DEFAULTS.get(kind, {}).get(
                    field
                ):
                    fillable = fillable and bool(evidence)
                changes.append(
                    FieldChange(
                        field=field,
                        current=old,
                        proposed=proposed,
                        disposition="fill" if fillable else "preserve",
                        evidence=evidence,
                    )
                )
            if current is None:
                disposition = "create"
            elif any(change.disposition == "fill" for change in changes):
                disposition = "supplement"
            else:
                disposition = "skip"
            diffs.append(
                AssetDiff(
                    name=item.name,
                    canonical_name=current.name if current else item.name,
                    source_code=item.source_code,
                    disposition=disposition,
                    changes=changes,
                )
            )
        return diffs

    async def preview(
        self,
        *,
        asset_type: AssetType,
        filename: str,
        payload: bytes,
        project_id: str,
        user_id: str,
    ) -> AssetImportPreview:
        decoded = decode_asset_table(filename, payload)
        extracted = extract_candidates(decoded.text, asset_type)
        diffs = await self._diffs(asset_type, extracted.candidates)
        import_id = uuid4().hex
        preview = AssetImportPreview(
            import_id=import_id,
            asset_type=asset_type,
            filename=decoded.filename,
            content_sha256=decoded.sha256,
            diffs=diffs,
            ignored_sections=extracted.ignored_sections,
            warnings=extracted.warnings,
            created_count=sum(x.disposition == "create" for x in diffs),
            supplemented_count=sum(x.disposition == "supplement" for x in diffs),
            skipped_count=sum(x.disposition == "skip" for x in diffs),
            warning_count=len(extracted.warnings),
        )
        await self.store.save_asset_import_preview(
            import_id,
            project_id,
            user_id,
            asset_type.value,
            decoded.filename,
            decoded.sha256,
            json.dumps(
                [x.model_dump(mode="json") for x in extracted.candidates],
                ensure_ascii=False,
            ),
            preview.model_dump_json(),
        )
        return preview

    async def confirm(
        self, import_id: str, asset_type: AssetType, project_id: str, user_id: str
    ) -> AssetImportResult:
        record = await self.store.get_asset_import_preview(import_id)
        if not record:
            raise ValueError("导入预览不存在或已过期")
        if record["confirmed_at"]:
            raise ValueError("导入预览已确认")
        if (
            record["project_id"] != project_id
            or record["user_id"] != user_id
            or record["asset_type"] != asset_type.value
        ):
            raise ValueError("导入预览作用域不匹配")
        from .models import AssetCandidate

        candidates = [
            AssetCandidate.model_validate(x)
            for x in json.loads(record["candidates_json"])
        ]
        diffs = await self._diffs(asset_type, candidates)
        actual = await self.store.confirm_asset_import(
            import_id, asset_type.value, candidates, diffs, project_id, user_id
        )
        try:
            await self.store.load_graph_state()
        except Exception:
            logger.warning(
                "asset import committed but in-memory cache refresh failed",
                exc_info=True,
            )
        actual_diffs = [AssetDiff.model_validate(item) for item in actual]
        return self._result(import_id, asset_type, actual_diffs)

    @staticmethod
    def _result(
        import_id: str, asset_type: AssetType, actual_diffs: list[AssetDiff]
    ) -> AssetImportResult:
        return AssetImportResult(
            import_id=import_id,
            asset_type=asset_type,
            diffs=actual_diffs,
            created_count=sum(x.disposition == "create" for x in actual_diffs),
            supplemented_count=sum(x.disposition == "supplement" for x in actual_diffs),
            skipped_count=sum(
                x.disposition in {"skip", "conflict"} for x in actual_diffs
            ),
            warning_count=sum(len(x.warnings) for x in actual_diffs),
        )
