"""Trusted current-media evidence, archived only at explicit write boundaries."""
from __future__ import annotations

import hashlib
import errno
import os
from pathlib import Path
import tempfile

from novelvideo.production_workflow.store import ProductionWorkflowStore

MEDIA_KEY = '_media'
# Preserve only the passive extensions accepted by authenticated media preview.
# Unknown/active document types remain downloads; never archive SVG as an image.
_PASSIVE_MEDIA_SUFFIXES = frozenset({
    '.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif', '.bmp', '.ico',
    '.mp4', '.webm', '.mov', '.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac',
})


def controlled_path(root, value):
    root = Path(root).absolute()
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    if '..' in path.parts or not path.is_relative_to(root):
        raise ValueError('Media path escapes controlled output root')
    if any(p.is_symlink() for p in (path, *path.parents) if p == root or p.is_relative_to(root)):
        raise ValueError('Symlink media path is forbidden')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Media path escapes controlled output root')
    return path


def _digest(path, destination=None):
    if not path.is_file():
        raise ValueError('Current media file is unavailable')
    sha, size = hashlib.sha256(), 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(1024 * 1024):
            sha.update(chunk)
            size += len(chunk)
            if destination is not None:
                destination.write(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('Media changed while capturing snapshot')
    return sha.hexdigest(), size


def capture_blob(root, value, *, archive):
    path = controlled_path(root, value)
    digest, size = _digest(path)
    suffix = path.suffix.lower()
    suffix = suffix if suffix in _PASSIVE_MEDIA_SUFFIXES else '.bin'
    relative = Path('assets/.creation-media/sha256') / digest[:2] / (digest + suffix)
    target = controlled_path(root, relative)
    if archive:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            controlled_path(root, relative)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    if _digest(controlled_path(root, value), stream) != (digest, size):
                        raise ValueError('Media changed before archival')
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    pass
                except OSError as exc:
                    if exc.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM):
                        raise
                    # Object-backed mounts may not implement hardlinks. O_EXCL
                    # never replaces an existing archive, including a partial
                    # file left by process death; all paths are verified below.
                    try:
                        descriptor = os.open(controlled_path(root, relative),
                                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    except FileExistsError:
                        pass
                    else:
                        owned = os.fstat(descriptor)
                        try:
                            with os.fdopen(descriptor, 'wb') as destination:
                                if _digest(temporary, destination) != (digest, size):
                                    raise ValueError('Media changed during archival')
                                destination.flush()
                                os.fsync(destination.fileno())
                        except BaseException:
                            # Remove only this failed attempt's partial file.
                            # A killed process leaves a digest-rejected remnant.
                            current = target.lstat()
                            if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                                target.unlink()
                            raise
                descriptor = os.open(target.parent, os.O_RDONLY)
                try:
                    try:
                        os.fsync(descriptor)
                    except OSError as exc:
                        # Directory fsync is unsupported on some FUSE mounts;
                        # file flush/fsync above and post-write digest remain mandatory.
                        if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                            raise
                finally:
                    os.close(descriptor)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        if _digest(controlled_path(root, relative)) != (digest, size):
            raise ValueError('Content archive digest mismatch')
    return {'path': relative.as_posix(), 'sha256': digest, 'size': size}


def owns_slot(mapping, record, slot_id):
    if mapping['kind'] == 'character':
        if mapping['parent_id']:
            name, identity = record['character_name'], record['identity_id']
            return slot_id in {f'character:{name}:identity:{identity}:portrait', f'character:{name}:state:{identity}'}
        return slot_id == f"character:{record['name']}:portrait"
    if mapping['kind'] == 'scene':
        prefix = (f"scene:{record['base_scene_id']}:state:{record['name']}:" if mapping['parent_id']
                  else f"scene:{record['name']}:base:")
        return slot_id.startswith(prefix)
    return False


def snapshot(source, mapping, record, *, archive=True):
    """Keep source fields flat for old consumers; media is server-owned evidence."""
    payload = record.model_dump(mode='json') if hasattr(record, 'model_dump') else dict(record)
    workflow = ProductionWorkflowStore(Path(source.state_dir) / 'production_workflow.json')
    if workflow.read_only_reason:
        raise ValueError(workflow.read_only_reason)
    evidence, currents = [], []
    slots = workflow.list_slots()
    for slot in sorted(slots, key=lambda s: s.slot_id):
        if not owns_slot(mapping, payload, slot.slot_id) or not slot.current_version_id:
            continue
        _, versions = workflow.get_slot(slot.slot_id)
        current = versions.get(slot.current_version_id)
        if current is None or current.adoption_status.value not in ('adopted', 'provisional'):
            raise ValueError('Workflow current is not an adopted or provisional version')
        currents.append((slot, current, True))
    # Existing canonical media can predate the sidecar. Its read-only legacy
    # view is provisional, never invented as an explicit adoption or candidate.
    legacy = None
    if mapping['kind'] == 'character':
        if mapping['parent_id']:
            from novelvideo.utils.path_resolver import canonical_identity_portrait_path
            slot_id = f"character:{payload['character_name']}:identity:{payload['identity_id']}:portrait"
            path = canonical_identity_portrait_path(
                Path(source.project_dir), payload['character_name'], payload['identity_name'])
        else:
            slot_id = f"character:{payload['name']}:portrait"
            path = Path('assets/characters') / payload['name'] / 'portrait.png'
        legacy = (slot_id, 'character_portrait', path)
    elif mapping['kind'] == 'scene':
        slot_id = (f"scene:{payload['base_scene_id']}:state:{payload['name']}:master" if mapping['parent_id']
                   else f"scene:{payload['name']}:base:master")
        path = Path('assets/scenes') / payload['name'] / 'master.png'
        legacy = (slot_id, 'scene_state' if mapping['parent_id'] else 'scene_base', path)
    if legacy and legacy[0] not in {s.slot_id for s in slots}:
        path = controlled_path(source.project_dir, legacy[2])
        if path.exists():
            slot, current = workflow.read_legacy_current(slot_id=legacy[0], asset_kind=legacy[1],
                                                         asset_path=path.relative_to(Path(source.project_dir).absolute()).as_posix())
            currents.append((slot, current, False))
    for slot, current, persisted in sorted(currents, key=lambda row: row[0].slot_id):
        metadata = current.generation_metadata or {}
        item = {'slot_id': slot.slot_id, 'version_id': current.version_id,
                'adoption_status': current.adoption_status.value, 'origin': current.origin.value,
                'source_attempt_id': current.source_attempt_id, 'provenance': metadata,
                'workflow_persisted': persisted,
                'source_path': current.asset_path,
                'blob': capture_blob(source.project_dir, current.asset_path, archive=archive)}
        canonical = metadata.get('canonical_path')
        if canonical:
            path = controlled_path(source.project_dir, canonical)
            item['canonical_blob'] = capture_blob(source.project_dir, path, archive=archive) if path.exists() else None
        evidence.append(item)
    payload[MEDIA_KEY] = evidence
    return payload


def compatible_before(previous, actual):
    """Old flat snapshots are upgraded explicitly, never by a current read."""
    from .asset_sync import fingerprint
    if previous is not None and MEDIA_KEY not in previous and actual is not None:
        actual = {k: v for k, v in actual.items() if k != MEDIA_KEY}
    return fingerprint(previous) == fingerprint(actual)
