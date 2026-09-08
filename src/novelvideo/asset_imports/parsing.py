from __future__ import annotations

import re
import unicodedata
from hashlib import sha256
from pathlib import Path

from .models import (
    AssetCandidate,
    AssetType,
    DecodedAssetTable,
    ExtractionResult,
    FieldEvidence,
)

_SECTION_KIND = {
    AssetType.CHARACTER: ("人物", "角色"),
    AssetType.SCENE: ("场景",),
    AssetType.PROP: ("道具",),
}
_EXCLUDED_HEADINGS = (
    "人物表",
    "角色表",
    "第一案人物",
    "后续单元案角色",
    "人物信息边界",
    "场景表",
    "场景连续性",
    "道具表",
    "道具使用红线",
    "稳定字段",
    "场景与道具",
)


def decode_asset_table(filename: str, payload: bytes) -> DecodedAssetTable:
    safe_name = Path(filename or "").name
    if Path(safe_name).suffix.lower() not in {".md", ".txt"}:
        raise ValueError("仅支持 .md 和 .txt 资产表")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("文件必须使用 UTF-8 编码") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError("导入文件为空")
    return DecodedAssetTable(
        filename=safe_name, text=text, sha256=sha256(payload).hexdigest()
    )


def _evidence(
    field: str,
    value: object,
    quote: str,
    text: str,
    *,
    search_start: int = 0,
    search_end: int | None = None,
) -> FieldEvidence:
    start = text.find(quote, search_start, search_end)
    if start < 0:
        raise ValueError(f"证据原文不存在：{field}")
    return FieldEvidence(
        field=field,
        value=value,
        source_start=start,
        source_end=start + len(quote),
        quote=quote,
    )


def _validate_asset_name(name: str) -> None:
    stripped = name.strip()
    if stripped in {".", ".."} or "/" in stripped or "\\" in stripped:
        raise ValueError(f"资产名包含非法路径字符：{name}")


def _table_rows(text: str, asset_type: AssetType) -> list[AssetCandidate]:
    raw_lines = text.splitlines(keepends=True)
    lines = [line.rstrip("\r\n") for line in raw_lines]
    line_starts: list[int] = []
    offset = 0
    for line in raw_lines:
        line_starts.append(offset)
        offset += len(line)
    result: list[AssetCandidate] = []
    section = ""
    i = 0
    while i < len(lines):
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", lines[i])
        if heading:
            section = heading.group(1)
        if (
            i + 2 < len(lines)
            and lines[i].lstrip().startswith("|")
            and re.match(r"^\s*\|?[\s:|-]+\|", lines[i + 1])
        ):
            headers = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
            wanted = any(key in section for key in _SECTION_KIND[asset_type])
            while i + 2 < len(lines) and lines[i + 2].lstrip().startswith("|"):
                raw = lines[i + 2]
                cells = [cell.strip() for cell in raw.strip().strip("|").split("|")]
                row = dict(zip(headers, cells))
                candidate = (
                    _candidate_from_row(
                        row, asset_type, raw, text, line_starts[i + 2]
                    )
                    if wanted
                    else None
                )
                if candidate:
                    result.append(candidate)
                i += 1
        i += 1
    return result


def _candidate_from_row(
    row: dict[str, str], kind: AssetType, raw: str, text: str, source_start: int
) -> AssetCandidate | None:
    name_keys = {
        AssetType.CHARACTER: ("角色", "人物", "姓名"),
        AssetType.SCENE: ("场景名", "场景"),
        AssetType.PROP: ("道具", "道具名"),
    }[kind]
    name = next(
        (row.get(key, "").strip() for key in name_keys if row.get(key, "").strip()), ""
    )
    if not name:
        return None
    fields: dict[str, str] = {}
    if kind == AssetType.CHARACTER:
        if row.get("功能"):
            fields["role"] = row["功能"]
        details = "；".join(
            f"{key}：{value}"
            for key, value in row.items()
            if value and key not in {*name_keys, "编号"}
        )
        if details:
            fields["description"] = details
    elif kind == AssetType.SCENE:
        mode = row.get("内／外", "")
        if mode:
            fields["scene_type"] = "exterior" if mode == "外" else "interior"
        if row.get("常用时段"):
            fields["time_of_day"] = row["常用时段"]
        if row.get("长期视觉特征"):
            fields["environment_prompt"] = row["长期视觉特征"]
        details = "；".join(
            filter(
                None,
                (
                    row.get("故事功能", ""),
                    f"首次出现：{row.get('首次出现')}" if row.get("首次出现") else "",
                ),
            )
        )
        if details:
            fields["description"] = details
    else:
        fields["prop_type"] = "object"
        if row.get("初始持有人"):
            fields["owner"] = row["初始持有人"]
        details = "；".join(
            f"{key}：{row[key]}"
            for key in ("来源／权利", "首次出现", "状态变化", "回收窗口")
            if row.get(key)
        )
        if details:
            fields["description"] = details
    evidence = [
        _evidence(
            field,
            value,
            raw,
            text,
            search_start=source_start,
            search_end=source_start + len(raw),
        )
        for field, value in fields.items()
    ]
    return AssetCandidate(
        name=name, fields=fields, evidence=evidence, source_code=row.get("编号", "")
    )


def _character_headings(text: str) -> list[AssetCandidate]:
    root = re.search(r"^(#{1,3})\s*(人物|角色)(表|设定|档案)?\s*$", text, re.MULTILINE)
    if root is None:
        return []
    root_level = len(root.group(1))
    following_root = re.search(
        rf"^#{{1,{root_level}}}\s+.+?\s*$", text[root.end() :], re.MULTILINE
    )
    section_end = (
        root.end() + following_root.start() if following_root is not None else len(text)
    )
    section = text[root.end() : section_end]
    matches = list(
        re.finditer(rf"^(#{{{root_level + 1},6}})\s+(.+?)\s*$", section, re.MULTILINE)
    )
    result: list[AssetCandidate] = []
    for index, match in enumerate(matches):
        name = match.group(2).strip()
        if any(token in name for token in _EXCLUDED_HEADINGS):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = section[match.start() : end]
        block_start = root.end() + match.start()
        block_end = root.end() + end
        pair_matches = list(
            re.finditer(r"^-\s*([^：:\n]+)[：:]\s*(.+)$", block, re.MULTILINE)
        )
        pairs = {item.group(1): item.group(2) for item in pair_matches}
        pair_quotes = {item.group(1): item.group(0) for item in pair_matches}
        identity_keys = {
            "身份／职业",
            "身份",
            "职业",
            "年龄",
            "性别",
            "视觉锚点",
            "前史",
            "核心缺陷",
            "人物弧线",
        }
        if not identity_keys.intersection(pairs):
            continue
        fields: dict[str, str] = {}
        role = pairs.get("身份／职业") or pairs.get("身份") or pairs.get("职业")
        if role:
            fields["role"] = role.strip()
        if pairs.get("性别"):
            fields["gender"] = pairs["性别"].strip("。 ")
        if pairs.get("别名"):
            fields["aliases"] = [
                part.strip()
                for part in re.split(r"[、,，/]", pairs["别名"])
                if part.strip()
            ]
        age = pairs.get("年龄", "")
        if age:
            number = re.search(r"\d+", age)
            if number:
                years = int(number.group())
                fields["age_group"] = (
                    "child"
                    if years < 16
                    else "youth"
                    if years < 36
                    else "middle"
                    if years < 60
                    else "elder"
                )
        visual = pairs.get("视觉锚点")
        details = [visual] if visual else []
        for key in ("前史", "核心缺陷", "人物弧线", "功能", "目标", "关键选择"):
            if pairs.get(key):
                details.append(f"{key}：{pairs[key]}")
        if details:
            fields["description"] = "；".join(details)
        evidence = []
        for field, value in fields.items():
            source_keys = {
                "role": ("身份／职业", "身份", "职业"),
                "gender": ("性别",),
                "age_group": ("年龄",),
                "aliases": ("别名",),
                "description": (
                    "视觉锚点",
                    "前史",
                    "核心缺陷",
                    "人物弧线",
                    "功能",
                    "目标",
                    "关键选择",
                ),
            }.get(field, ())
            quotes = [pair_quotes[key] for key in source_keys if pairs.get(key)]
            if field == "description":
                evidence.extend(
                    _evidence(
                        field,
                        value,
                        quote,
                        text,
                        search_start=block_start,
                        search_end=block_end,
                    )
                    for quote in quotes
                )
            else:
                evidence.append(
                    _evidence(
                        field,
                        value,
                        quotes[0] if quotes else match.group(0),
                        text,
                        search_start=block_start,
                        search_end=block_end,
                    )
                )
        result.append(AssetCandidate(name=name, fields=fields, evidence=evidence))
    return result


def extract_candidates(text: str, asset_type: AssetType) -> ExtractionResult:
    candidates = _table_rows(text, asset_type)
    if asset_type == AssetType.CHARACTER:
        candidates = _character_headings(text) + candidates
    deduped: dict[str, AssetCandidate] = {}
    conflicted_fields: dict[str, set[str]] = {}
    warnings: list[str] = []
    for item in candidates:
        _validate_asset_name(item.name)
        key = normalize_asset_name(item.name)
        if key not in deduped:
            deduped[key] = item
            continue
        existing = deduped[key]
        rejected = conflicted_fields.setdefault(key, set())
        for field, value in item.fields.items():
            if field in rejected:
                continue
            if field not in existing.fields:
                existing.fields[field] = value
            elif existing.fields[field] != value:
                existing.fields.pop(field, None)
                rejected.add(field)
                warnings.append(f"{item.name} 的 {field} 存在冲突，已跳过")
        existing.evidence.extend(item.evidence)
        existing.evidence = [
            evidence
            for evidence in existing.evidence
            if evidence.field in existing.fields and evidence.field not in rejected
        ]
    if not deduped:
        raise ValueError("未识别到明确的目标资产定义")
    headings = [
        m.group(1).strip()
        for m in re.finditer(r"^#{2,6}\s+(.+?)\s*$", text, re.MULTILINE)
    ]
    ignored = [
        heading
        for heading in headings
        if not any(key in heading for key in _SECTION_KIND[asset_type])
        and heading not in {item.name for item in deduped.values()}
    ]
    return ExtractionResult(
        candidates=list(deduped.values()), ignored_sections=ignored, warnings=warnings
    )


def normalize_asset_name(value: str) -> str:
    """Normalize presentation-only differences without fuzzy semantic matching."""
    normalized = (
        unicodedata.normalize("NFKC", value or "").replace("\u3000", " ").strip()
    )
    normalized = re.sub(
        r"^[\s\"'“”‘’《》【】()（）·,:：;；。.!！?？]+|[\s\"'“”‘’《》【】()（）·,:：;；。.!！?？]+$",
        "",
        normalized,
    )
    return "".join(normalized.split()).casefold()
