"""Resolve explicit file references and checkpoint successful uploads."""
from __future__ import annotations
import hashlib
from pathlib import Path
from .models import FileReference, WorkflowError


async def resolve_files(rules, values, client, account_id, checkpoint, save, *, artifact_resolver=None):
    plans = {}
    # Validate every file before uploading the first one.
    for name, value in values.items():
        rule = rules.get(name)
        if rule is None or rule.type not in {"file", "files"}:
            continue
        refs = value if rule.type == "files" else [value]
        plans[name] = []
        for raw in refs:
            ref = FileReference.model_validate(raw)
            if ref.kind == "provider_file":
                plans[name].append((None, ref.value, None))
                continue
            if ref.kind == "artifact_id":
                if artifact_resolver is None:
                    raise WorkflowError("asset_resolver_required", name)
                path = Path(artifact_resolver(ref.value))
            else:
                path = Path(ref.value)
            if path.is_symlink() or not path.is_file():
                raise WorkflowError("file_missing", name)
            if rule.extensions and path.suffix.lower() not in {e.lower() for e in rule.extensions}:
                raise WorkflowError("file_extension", name)
            if rule.max_bytes is not None and path.stat().st_size > rule.max_bytes:
                raise WorkflowError("file_too_large", name)
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            cache_key = hashlib.sha256(f"{account_id}:{digest}:{path.suffix.lower()}".encode()).hexdigest()
            plans[name].append((path, cache_key, digest))
    uploads = dict(checkpoint.get("uploads", {}))
    resolved = {}
    for name, items in plans.items():
        output = []
        for path, key, digest in items:
            if path is None:
                output.append(key)
                continue
            if key not in uploads:
                remote = await client.upload(path)
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise WorkflowError("file_changed", name)
                uploads[key] = remote
                save({"uploads": dict(uploads)})
            output.append(uploads[key])
        resolved[name] = output if rules[name].type == "files" else output[0]
    return resolved
