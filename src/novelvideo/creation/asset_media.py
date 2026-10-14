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
    '.sog', '.ply', '.splat', '.ksplat', '.glb', '.json',
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
            return slot_id in {f'character:{name}:identity:{identity}:portrait', f'character:{name}:state:{identity}', f'character:{name}:identity:{identity}:costume'}
        return slot_id == f"character:{record['name']}:portrait"
    if mapping['kind'] == 'scene':
        prefix = (f"scene:{record['base_scene_id']}:state:{record['name']}:" if mapping['parent_id']
                  else f"scene:{record['name']}:base:")
        return slot_id.startswith(prefix)
    return False


SCENE_MANIFEST_PATHS = {
    'pano_path': None, 'ply_path': 'active', 'pano_ply_path': 'pano',
    'pano_depth_ply_path': 'pano_depth', 'master_ply_path': 'master',
    'reverse_ply_path': 'reverse', 'custom_scene_path': 'custom',
    'collision_glb_path': None, 'voxel_json_path': None,
}
SCENE_MANIFEST_PARAMETERS = {'source', 'pano_correction', 'pano_sharp_args',
    'single_face_sharp_args', 'master_sharp_args', 'reverse_sharp_args',
    'scene_360_args', 'splat_transform_args', 'active_source_id', 'active_source',
    'scene', 'scenes_by_source_id'}


def scene_media_layouts(root, name):
    """Resolve the existing scene layouts; a manifest cannot name other assets."""
    import json
    from novelvideo.director_world import stage_manifest
    from novelvideo.utils.path_resolver import canonical_scene_master_path, canonical_scene_reverse_master_path
    root = Path(root)
    directory = controlled_path(root, stage_manifest.stage_dir(root, name))
    manifest_path = controlled_path(root, stage_manifest.manifest_path(root, name))
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    selected = {key: value for key, value in manifest.items()
                if key in SCENE_MANIFEST_PATHS or key in SCENE_MANIFEST_PARAMETERS}
    for field in SCENE_MANIFEST_PATHS:
        value = manifest.get(field)
        if value:
            path = controlled_path(root, directory / value)
            if not path.is_relative_to(directory):
                raise ValueError('Scene manifest path escapes its scene directory')
    paths = {'master':canonical_scene_master_path(root,name),
             'reverse_master':canonical_scene_reverse_master_path(root,name),
             'spatial_layout':root/'assets/scenes'/name/'spatial_layout.png'}
    relations = {}
    for field, kind in SCENE_MANIFEST_PATHS.items():
        if kind:
            path = stage_manifest.resolve_ply_path(root,name,ply_kind=kind)
        elif field == 'pano_path':
            path = stage_manifest.resolve_pano_path(root,name)
        else:
            path = directory / manifest[field] if manifest.get(field) else None
        if path is not None:
            path = controlled_path(root,path)
            if not path.is_relative_to(directory):
                raise ValueError('Scene manifest path escapes its scene directory')
            role = field.removesuffix('_path')
            paths[role] = path
            relations[role] = field
    return paths, relations, selected


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
    legacy = []
    if mapping['kind'] == 'character':
        if mapping['parent_id']:
            from novelvideo.utils.path_resolver import canonical_identity_portrait_path, canonical_identity_path, canonical_identity_costume_path, compute_identity_portrait_path
            name, identity = payload['character_name'], payload['identity_id']
            for slot_id, kind, path in (
                (f'character:{name}:identity:{identity}:portrait', 'character_portrait', canonical_identity_portrait_path),
                (f'character:{name}:state:{identity}', 'character_state', canonical_identity_path),
                (f'character:{name}:identity:{identity}:costume', 'character_state', canonical_identity_costume_path),
            ):
                value = path(Path(source.project_dir), name, payload['identity_name'])
                if path is canonical_identity_portrait_path:
                    value = compute_identity_portrait_path(Path(source.project_dir), name, payload['identity_name']) or value
                legacy.append((slot_id, kind, value))
        else:
            legacy.append((f"character:{payload['name']}:portrait", 'character_portrait',
                           Path('assets/characters') / payload['name'] / 'portrait.png'))
    elif mapping['kind'] == 'scene':
        prefix = (f"scene:{payload['base_scene_id']}:state:{payload['name']}:" if mapping['parent_id']
                  else f"scene:{payload['name']}:base:")
        paths, relations, manifest = scene_media_layouts(source.project_dir,payload['name'])
        if manifest:
            payload['_scene_manifest'] = manifest
        legacy.extend((prefix+role,'scene_state' if mapping['parent_id'] else 'scene_base',path)
                      for role,path in paths.items())
    layouts = {slot_id: path for slot_id, _, path in legacy}
    for slot_id, kind, value in legacy:
        path = controlled_path(source.project_dir, value)
        if slot_id not in {s.slot_id for s in slots if s.current_version_id} and path.exists():
            slot, current = workflow.read_legacy_current(slot_id=slot_id, asset_kind=kind,
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
        if mapping['kind'] == 'scene' and slot.slot_id.removeprefix(prefix) in relations:
            item['manifest_field'] = relations[slot.slot_id.removeprefix(prefix)]
        if mapping['kind'] == 'scene':
            item['media_role'] = slot.slot_id.removeprefix(prefix)
        canonical = (layouts.get(slot.slot_id) or metadata.get('canonical_path')
                     if mapping['kind'] == 'character'
                     else metadata.get('canonical_path') or layouts.get(slot.slot_id))
        if mapping['kind'] == 'scene' and layouts.get(slot.slot_id):
            visible = controlled_path(source.project_dir,layouts[slot.slot_id])
            if visible.is_file():
                canonical = visible
        if canonical:
            path = controlled_path(source.project_dir, canonical)
            item['canonical_blob'] = capture_blob(source.project_dir, path, archive=archive) if path.exists() else None
        item['current_blob'] = item.get('canonical_blob') or item['blob']
        overridden = item['current_blob']['sha256'] != item['blob']['sha256']
        item['current_source'] = {
            'kind': 'canonical_override' if overridden else ('workflow' if persisted else 'legacy_canonical'),
            'path': item['current_blob']['path'],
            'source_attempt_id': None if overridden else current.source_attempt_id,
        }
        evidence.append(item)
    payload[MEDIA_KEY] = evidence
    return payload


def compatible_before(previous, actual):
    """Old flat snapshots are upgraded explicitly, never by a current read."""
    from .asset_sync import fingerprint
    if previous is not None and MEDIA_KEY not in previous and actual is not None:
        actual = {k: v for k, v in actual.items() if k != MEDIA_KEY}
    return fingerprint(previous) == fingerprint(actual)
