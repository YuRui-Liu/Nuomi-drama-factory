"""Portable character design cards never retain source-project media paths."""
from typing import Any
from pathlib import Path
from hashlib import sha256
import io


def portable_character(source: dict[str, Any]) -> dict[str, Any]:
    result = {key: str(source.get(key) or "") for key in ("name", "role", "gender", "age_group", "description", "face_prompt", "body_type")}
    result["is_main"] = False
    result["identities"] = [{key: str(identity.get(key) or "") for key in ("identity_name", "appearance_details", "face_prompt", "age_group", "body_type")} for identity in source.get("identities", []) if isinstance(identity, dict)]
    return result


def _verified_image(root: Path, path: Path) -> bytes:
    from PIL import Image
    root = root.resolve()
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("角色图像必须位于授权项目目录内")
    if not path.is_file():
        raise ValueError("已采纳角色图像不存在，请重新采纳或上传后保存个人卡")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("角色图像不能超过 20 MB")
    content = path.read_bytes()
    with Image.open(io.BytesIO(content)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("角色图像格式不支持")
        image.verify()
    return content


def capture_character_media(root: Path, character: dict, library: Path, card_id: str) -> list[dict]:
    """Snapshot canonical adopted slots; candidates and arbitrary paths are excluded."""
    from novelvideo.utils.path_resolver import canonical_portrait_path, compute_identity_path, compute_identity_portrait_path, compute_identity_costume_path
    name = character["name"]
    slots = [("portrait", None, canonical_portrait_path(root, name))]
    for index, identity in enumerate(character.get("identities", [])):
        for kind, resolver, explicit in (("identity", compute_identity_path, ""), ("identity_portrait", compute_identity_portrait_path, "portrait_image"), ("costume", compute_identity_costume_path, "costume_image")):
            path = resolver(root, name, identity["identity_name"])
            if path:
                slots.append((kind, index, Path(path)))
            elif explicit and identity.get(explicit):
                supplied = Path(identity[explicit])
                slots.append((kind, index, supplied if supplied.is_absolute() else root / supplied))
    if len(slots) > 100:
        raise ValueError("角色卡图像数量或总大小超过限制")
    verified = []
    total = 0
    for kind, index, path in slots:
        content = _verified_image(root, path)
        total += len(content)
        if total > 100 * 1024 * 1024:
            raise ValueError("角色卡图像数量或总大小超过限制")
        verified.append((kind, index, content))
    destination = library / "media" / card_id
    destination.mkdir(parents=True, exist_ok=False)
    result = []
    for index, (kind, identity, content) in enumerate(verified):
        filename = f"{index}.png"
        (destination / filename).write_bytes(content)
        result.append({"kind": kind, "identity_index": identity, "file": filename, "sha256": sha256(content).hexdigest()})
    return result


def restore_character_media(library: Path, card_id: str, media: list[dict], root: Path, name: str, identities: list) -> list[Path]:
    from novelvideo.utils.path_resolver import canonical_portrait_path, canonical_identity_path, canonical_identity_portrait_path, canonical_identity_costume_path
    from novelvideo.utils.safe_paths import validate_path_segment
    validate_path_segment(name)
    validate_path_segment(card_id)
    resolvers = {"identity": canonical_identity_path, "identity_portrait": canonical_identity_portrait_path, "costume": canonical_identity_costume_path}
    pending = []
    for item in media:
        validate_path_segment(item["file"])
        content = _verified_image(library, library / "media" / card_id / item["file"])
        if sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("个人角色卡图像校验失败，请重新保存角色卡")
        if item["kind"] == "portrait":
            destination = canonical_portrait_path(root, name)
        else:
            index = item["identity_index"]
            if not isinstance(index, int) or not 0 <= index < len(identities) or item["kind"] not in resolvers:
                raise ValueError("角色卡图像身份引用无效")
            identity = identities[index]
            destination = resolvers[item["kind"]](root, name, identity.identity_name)
            if item["kind"] in {"identity_portrait", "costume"}:
                setattr(identity, "portrait_image" if item["kind"] == "identity_portrait" else "costume_image", str(destination.relative_to(root)))
        if not destination.resolve().is_relative_to(root.resolve()):
            raise ValueError("目标图像必须位于项目目录内")
        pending.append((destination, content))
    if not pending:
        return []  # Legacy description-only cards remain explicitly supported.
    directory = root / "assets" / "characters" / name
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(exist_ok=False)  # Never overwrite existing character assets.
    written = []
    try:
        for destination, content in pending:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                output.write(content)
            written.append(destination)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return written


def references_character(value: Any, names: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"character", "character_name", "characterName", "identity_id", "identityId", "entity_key", "entity_id"} and isinstance(item, str) and item in names:
                return True
            if key in {"character_names", "identity_ids"} and isinstance(item, list) and any(isinstance(part, str) and part in names for part in item):
                return True
            if isinstance(item, (dict, list)) and references_character(item, names):
                return True
    if isinstance(value, list):
        return any(references_character(item, names) for item in value)
    return False
