"""Synchronous recovery primitives, deliberately independent of API and visual stores.

A prepared journal always rolls back. A committed journal always preserves the
new pair. Every reader/mutator must recover while holding the project lock before
looking at a current portrait, its bible, or the workflow pointer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from novelvideo.production_workflow import production_workflow_project_lock
from novelvideo.production_workflow.character_portraits import validate_character_name
from novelvideo.utils.path_resolver import canonical_identity_portrait_path


def controlled_path(root, relative):
    root = Path(root).absolute()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('casting path outside controlled root')
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('symlink casting path forbidden')
    return path


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_write(path, data):
    """Atomic replacement with durable contents and directory entry."""
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink casting path forbidden')
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        sync_directory(directory.parent)
    if data is None:
        path.unlink(missing_ok=True)
        sync_directory(path.parent)
        return
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


def capture(path):
    return base64.b64encode(path.read_bytes()).decode('ascii') if path.exists() else None


def restore(path, encoded):
    durable_write(path, base64.b64decode(encoded, validate=True) if encoded is not None else None)


def journal_path(state_dir):
    return controlled_path(state_dir, 'casting_adoptions.json')


def read_journal(state_dir):
    path = journal_path(state_dir)
    if not path.exists():
        return {'schema_version': 1, 'entries': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('entries'), dict):
        raise ValueError('invalid casting adoption journal')
    if any(not isinstance(entry, dict) or entry.get('status') not in {'prepared', 'committed'}
            for entry in data['entries'].values()):
        raise ValueError('invalid casting adoption journal state')
    return data


def write_journal(state_dir, data):
    durable_write(journal_path(state_dir), json_bytes(data))


def canonical_portrait_path(project_dir, character_id, identity_id=None, identity_name=None):
    name = validate_character_name(character_id)
    root = Path(project_dir).absolute()
    if identity_id is None:
        relative = Path('assets') / 'characters' / name / 'portrait.png'
    else:
        if not identity_id or not str(identity_name or '').strip():
            raise ValueError('identity portrait requires identity name')
        relative = canonical_identity_portrait_path(root, name, identity_name).relative_to(root)
    return controlled_path(root, relative)


def identity_record(state_dir, character_id, identity_id):
    db_path = controlled_path(state_dir, 'data.db')
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:
        row = db.execute('SELECT identities_json FROM characters WHERE name=?', (character_id,)).fetchone()
    if row is None:
        raise ValueError('character no longer exists')
    if identity_id is None:
        return None
    identities = json.loads(row[0] or '[]')
    matching = [i for i in identities if i.get('identity_id') == identity_id]
    if len(matching) != 1:
        raise ValueError('identity no longer exists or is ambiguous')
    return matching[0]


def assert_portrait_scope_path_available(project_dir, state_dir, character_id, identity_id, canonical):
    """A logical stage must never publish over another stage's physical slot.

    Called under the adoption project lock, after resolving the live identity.
    Check all live identity names, including stages without any adopted version,
    then historical journal ownership so removing an identity cannot make its
    previously adopted portrait available for an unrelated stage.
    """
    root = Path(project_dir).absolute()
    canonical = controlled_path(root, Path(canonical).relative_to(root))
    scope = (character_id, identity_id)
    db_path = controlled_path(state_dir, 'data.db')
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:
        row = db.execute('SELECT identities_json FROM characters WHERE name=?', (character_id,)).fetchone()
    if row is None:
        raise ValueError('character no longer exists')
    for identity in json.loads(row[0] or '[]'):
        other_id = identity.get('identity_id')
        if other_id == identity_id:
            continue
        other = canonical_portrait_path(root, character_id, other_id, identity.get('identity_name'))
        if other == canonical:
            raise ValueError('portrait path is already reserved by another casting scope')
    for entry in read_journal(state_dir)['entries'].values():
        if (entry['character_id'], entry['identity_id']) == scope:
            continue
        if controlled_path(root, entry['canonical_path']) == canonical:
            raise ValueError('portrait path is already adopted by another casting scope')


def update_identity_portrait_reference(project_dir, state_dir, character_id, identity_id, portrait_image):
    """Patch only one fresh JSON field; caller holds the project lock.

    project_dir is an explicit argument to keep callers from deriving state paths
    from output paths. Legacy references may be blank or absolute on rollback.
    """
    db_path = controlled_path(state_dir, 'data.db')
    with sqlite3.connect(f'file:{db_path}?mode=rw', uri=True) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT identities_json FROM characters WHERE name=?', (character_id,)).fetchone()
        if row is None:
            raise ValueError('character no longer exists')
        identities = json.loads(row[0] or '[]')
        matching = [i for i in identities if i.get('identity_id') == identity_id]
        if len(matching) != 1:
            raise ValueError('identity no longer exists or is ambiguous')
        matching[0]['portrait_image'] = portrait_image
        updated = db.execute('UPDATE characters SET identities_json=? WHERE name=?',
            (json.dumps(identities, ensure_ascii=False), character_id))
        if updated.rowcount != 1:
            raise ValueError('identity portrait update did not affect exactly one character')
        db.commit()


def recover_casting_adoptions(project_dir, state_dir):
    """Idempotent rollback of interrupted prepared transactions; no await/hooks."""
    # Do not create any directories when a legacy/read-only project has no journal.
    if not journal_path(state_dir).exists():
        return
    with production_workflow_project_lock(state_dir):
        journal = read_journal(state_dir)
        if journal['entries'] and (journal.get('project_dir'), journal.get('state_dir')) != (
                str(Path(project_dir).absolute()), str(Path(state_dir).absolute())):
            raise ValueError('casting journal root binding mismatch')
        prepared = [(key, entry) for key, entry in journal['entries'].items() if entry.get('status') == 'prepared']
        if len(prepared) > 1:
            raise ValueError('multiple unfinished casting transactions')
        for key, entry in prepared:
            canonical = canonical_portrait_path(project_dir, entry['character_id'], entry['identity_id'], entry['identity_name'])
            if canonical.relative_to(Path(project_dir).absolute()).as_posix() != entry['canonical_path']:
                raise ValueError('casting journal canonical path mismatch')
            # Fixed file names and derived canonical paths prevent journal path injection.
            restore(canonical, entry['before']['canonical'])
            restore(controlled_path(project_dir, 'state/character_visual_workspaces.json'), entry['before']['workspace'])
            if entry['identity_id'] is not None:
                update_identity_portrait_reference(project_dir, state_dir, entry['character_id'], entry['identity_id'], entry['before']['identity_portrait'])
            restore(controlled_path(state_dir, 'production_workflow.json'), entry['before']['workflow'])
            del journal['entries'][key]
            write_journal(state_dir, journal)


def assert_legacy_portrait_mutation_allowed(project_dir, state_dir, character_id, identity_id=None):
    """Protect the entire casting slot, including legacy versions without metadata."""
    with production_workflow_project_lock(state_dir):
        recover_casting_adoptions(project_dir, state_dir)
        path = controlled_path(project_dir, 'state/character_visual_workspaces.json')
        workspaces = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        workspace = workspaces.get(character_id, {})
        revision = workspace.get('casting_revision') if identity_id is None else workspace.get('identity_casting_revisions', {}).get(identity_id)
        if revision or (identity_id or 'base') in workspace.get('casting_limitation_reasons', {}):
            raise ValueError('CHARACTER_CASTING_REQUIRED: use explicit candidate adoption for this portrait')
        # A deleted draft cannot unprotect a previously adopted current.
        for entry in read_journal(state_dir)['entries'].values():
            if entry.get('status') == 'committed' and (entry.get('character_id'), entry.get('identity_id')) == (character_id, identity_id):
                raise ValueError('CHARACTER_CASTING_REQUIRED: use explicit candidate adoption for this portrait')


def resolve_casting_media_path(project_dir, state_dir, path):
    """Pin mutable current URLs to an immutable adopted file before FileResponse."""
    root = Path(project_dir).absolute()
    requested = Path(path).absolute()
    with production_workflow_project_lock(state_dir):
        recover_casting_adoptions(project_dir, state_dir)
        workflow_path = controlled_path(state_dir, 'production_workflow.json')
        if not workflow_path.exists():
            return requested
        workflow = json.loads(workflow_path.read_text(encoding='utf-8'))
        current_ids = {s.get('current_version_id') for s in workflow.get('slots', {}).values()}
        for version in workflow.get('versions', []):
            metadata = version.get('generation_metadata') or {}
            audit = metadata.get('casting_adoption')
            if not audit or version['version_id'] not in current_ids:
                continue
            canonical = controlled_path(root, metadata['canonical_path'])
            if canonical != requested:
                continue
            immutable = controlled_path(root, version['asset_path'])
            if immutable == canonical or hashlib.sha256(immutable.read_bytes()).hexdigest() != audit['asset_sha256']:
                raise ValueError('casting current immutable asset mismatch')
            return immutable
        return requested


def assert_casting_path_mutation_allowed(project_dir, state_dir, path):
    """Prevent another slot from writing/deleting casting-owned physical bytes.

    Slot authorization is insufficient: an otherwise unrelated version can carry
    a canonical_path or asset_path pointing at a protected portrait or history.
    The caller keeps the project lock through the subsequent file mutation.
    """
    root = Path(project_dir).absolute()
    requested = Path(path)
    relative = requested.relative_to(root) if requested.is_absolute() else requested
    requested = controlled_path(root, relative)
    with production_workflow_project_lock(state_dir):
        recover_casting_adoptions(root, state_dir)
        protected = set()
        history_roots = set()
        workspace_path = controlled_path(root, 'state/character_visual_workspaces.json')
        workspaces = json.loads(workspace_path.read_text(encoding='utf-8')) if workspace_path.exists() else {}
        for character_id, workspace in workspaces.items():
            scopes = set(workspace.get('identity_casting_revisions', {}))
            scopes.update(key for key in workspace.get('casting_limitation_reasons', {}) if key != 'base')
            base = bool(workspace.get('casting_revision')) or 'base' in workspace.get('casting_limitation_reasons', {})
            if not base and not scopes:
                continue
            character_root = controlled_path(root, Path('assets/characters') / validate_character_name(character_id))
            history_roots.update((character_root / 'portrait_versions', character_root / 'identities/portrait_versions'))
            if base:
                protected.add(canonical_portrait_path(root, character_id))
            for identity_id in scopes:
                try:
                    identity = identity_record(state_dir, character_id, identity_id)
                except (ValueError, sqlite3.Error):
                    # A stale/deleted identity has no live name to resolve. Keep
                    # its portrait namespace fail closed, while full sheets and
                    # costume assets continue to be allowed.
                    if requested.parent == character_root / 'identities' and requested.name.endswith('_portrait.png'):
                        protected.add(requested)
                    continue
                canonical = canonical_portrait_path(root, character_id, identity_id, identity['identity_name'])
                protected.add(canonical)
                # Older projects used the same filename without the character
                # prefix. It remains a current fallback until explicit adoption.
                protected.add(canonical.with_name(canonical.name.removeprefix(character_id + '_')))
                reference = identity.get('portrait_image')
                if reference:
                    reference_path = Path(reference)
                    reference_relative = reference_path.relative_to(root) if reference_path.is_absolute() else reference_path
                    protected.add(controlled_path(root, reference_relative))
        for entry in read_journal(state_dir)['entries'].values():
            protected.add(controlled_path(root, entry['canonical_path']))
            protected.add(controlled_path(root, entry['immutable_path']))
        if (requested.is_relative_to(root / 'assets/casting_candidates') or requested in protected
                or any(requested.is_relative_to(history) for history in history_roots)):
            raise ValueError('CHARACTER_CASTING_REQUIRED: casting portrait bytes require explicit adoption')
