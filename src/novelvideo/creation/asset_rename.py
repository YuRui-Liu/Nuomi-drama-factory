"""Recoverable renames. SQL evidence, media copies and semantic CAS are separate steps.

The source journal also exists for legacy projects; it never initializes creation.
Identity IDs, immutable media, workflow versions and adoption events are retained.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import shutil
import tempfile
from uuid import uuid4

from novelvideo.production_workflow.coordination import asset_project_lock
from novelvideo.utils.safe_paths import validate_path_segment
from .asset_sync import SourceSync, SourceSyncPending


@contextmanager
def _connect(source):
    conn = sqlite3.connect(source.db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def assert_no_pending_rename(source, *, kind=None, names=None):
    with _connect(source) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='asset_rename_intents'").fetchone():
            for row in conn.execute("SELECT token,evidence FROM asset_rename_intents WHERE complete=0"):
                changes = json.loads(row['evidence'])['changes']
                affected = {c[side]['name'] for c in changes for side in ('before', 'after')
                            if c['table'] == (kind + 's' if kind else '')}
                if kind is None or (affected and (names is None or affected.intersection(names))):
                    raise SourceSyncPending(f"Asset rename {row['token']} requires reconciliation before this source edit")


def assert_asset_locators_writable(source, kind, names):
    """Retired source names still own immutable media and workflow history.

    Call under the project lock, before journaling or changing any source row.
    A new SQL row at an old name is not proof of a new owner: external SQL may
    have bypassed this boundary. Current names are never reserved by a rename
    merely because they occur in its *after* image.
    """
    names = set(names or ())
    if not names or kind not in ('character', 'scene'):
        return
    with _connect(source) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='asset_rename_intents'").fetchone():
            return
        for row in conn.execute('SELECT request FROM asset_rename_intents'):
            renamed_kind, old, new, *_ = json.loads(row['request'])
            if renamed_kind == kind and old != new and old in names:
                raise ValueError(f'Asset locator {kind}:{old} is retired; use its current name')


def previous_rename_source(source, kind, name):
    """PATCH retry context only; ordinary reads never recover or migrate state."""
    with _connect(source) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='asset_rename_intents'").fetchone():
            return None
        for row in conn.execute('SELECT request,evidence FROM asset_rename_intents ORDER BY rowid DESC'):
            request = json.loads(row['request'])
            if request[:2] == [kind, name]:
                table = 'scenes' if kind == 'scene' else 'characters'
                return next((c['before'] for c in json.loads(row['evidence'])['changes']
                             if c['table'] == table and c['before']['name'] == name), None)
    return None


def _checkpoint(phase):
    """Fault-injection boundary; durable evidence precedes every side effect."""


def _rows(conn, table):
    return [dict(r) for r in conn.execute(f'SELECT * FROM {table}')]


def _digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {'sha256': digest.hexdigest(), 'size': size}


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _files(root):
    if not root.exists():
        return {}
    paths = [root] if root.is_file() else sorted(root.rglob('*'))
    if root.is_symlink() or any(p.is_symlink() for p in paths):
        raise ValueError('Symlink assets cannot be renamed')
    return {str(p.relative_to(root)) if p != root else '': _digest(p)
            for p in paths if p.is_file()}


def _copy_plan(old, new, *, manifest_name=None):
    if any(p.is_symlink() for p in [old, new, *old.parents, *new.parents]
           if p.name not in ('tmp', 'var')):
        raise ValueError('Symlink asset paths cannot be renamed')
    if new.exists():
        raise ValueError('Target asset directory already exists')
    contents = _files(old)
    updated = copy.deepcopy(contents)
    manifests = {}
    if manifest_name:
        for relative in contents:
            if Path(relative).name in ('manifest.json', 'stage_manifest.json'):
                manifest = json.loads((old / relative).read_text())
                manifest['scene_id'] = manifest_name
                text = json.dumps(manifest, ensure_ascii=False, indent=2)
                manifests[relative] = text
                data = text.encode()
                updated[relative] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    return {'old': str(old), 'new': str(new), 'before': contents, 'after': updated,
            'manifests': manifests, 'directory': not old.is_file(), 'present': old.exists()}


def _install_files(plan):
    """Retain originals; copy bounded chunks and atomically install verified files."""
    old, new = Path(plan['old']), Path(plan['new'])
    if (old.exists() != plan['present'] or (old.exists() and old.is_dir() != plan['directory'])
            or _files(old) != plan['before']):
        raise SourceSyncPending('Rename source media drifted')
    if new.exists():
        if new.is_dir() != plan['directory'] or _files(new) != plan['after']:
            raise SourceSyncPending('Rename target media collided or drifted')
        return
    if not plan['present']:
        return
    temporary = new.with_name(new.name + '.rename-' + plan['token'])
    observed = _files(temporary)
    if any(plan['after'].get(k) != v for k, v in observed.items()):
        raise SourceSyncPending('Rename staging files drifted')
    new.parent.mkdir(parents=True, exist_ok=True)
    for relative, expected in plan['after'].items():
        destination = temporary / relative if plan['directory'] else temporary
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            continue
        # A crash can leave an unreferenced scratch file, but never a partial
        # installed file or an ambiguous half-populated final destination.
        scratch = None
        try:
            with tempfile.NamedTemporaryFile(dir=new.parent, prefix='.rename-copy-', delete=False) as stream:
                scratch = Path(stream.name)
                if relative in plan['manifests']:
                    stream.write(plan['manifests'][relative].encode())
                else:
                    source_path = old / plan.get('sources', {}).get(relative, relative) if plan['directory'] else old
                    with source_path.open('rb') as origin:
                        shutil.copyfileobj(origin, stream, 1024 * 1024)
                stream.flush()
                os.fsync(stream.fileno())
            if _digest(scratch) != expected:
                raise SourceSyncPending('Rename source media changed while copying')
            if destination.exists():
                raise SourceSyncPending('Rename staging target appeared')
            os.link(scratch, destination)  # Atomic no-clobber install for files.
            _sync_directory(destination.parent)
        finally:
            if scratch:
                scratch.unlink(missing_ok=True)
    if plan['directory']:
        temporary.mkdir(parents=True, exist_ok=True)
    if new.exists():
        raise SourceSyncPending('Rename target appeared during copy')
    os.rename(temporary, new)
    _sync_directory(new.parent)


def _repath(value, plans, project_dir):
    """Rewrite only a declared media field backed by an exact copied file."""
    if not isinstance(value, str) or not value or '://' in value:
        return value
    original = Path(value)
    path = original if original.is_absolute() else Path(project_dir) / original
    for plan in plans:
        old, new = Path(plan['old']), Path(plan['new'])
        if plan['directory'] and path.is_relative_to(old):
            relative = str(path.relative_to(old))
            if relative not in plan['before']:
                continue
            target = new / relative
        elif not plan['directory'] and path == old and '' in plan['before']:
            target = new
        else:
            continue
        return str(target if original.is_absolute() else target.relative_to(project_dir))
    return value


def _workflow_plan(source, kind, old, new, *, scene_base=''):
    path = Path(source.state_dir) / 'production_workflow.json'
    if not path.exists() or kind == 'identity':
        return None
    before = path.read_text()
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    workflow = ProductionWorkflowStore(path)
    if workflow.read_only_reason:
        raise ValueError(workflow.read_only_reason)
    payload = json.loads(before)
    aliases = payload.setdefault('slot_aliases', {})
    additions, display, path_rules, metadata_rules = {}, {}, {}, {}
    prefix, replacement = f'{kind}:{old}:', f'{kind}:{new}:'
    if kind == 'scene' and scene_base:
        prefix, replacement = f'scene:{scene_base}:state:{old}:', f'scene:{scene_base}:state:{new}:'
    for slot in [*payload.get('slots', {}), *list(aliases)]:
        if slot.startswith(prefix):
            target = replacement + slot[len(prefix):]
            canonical = workflow.resolve_slot_id(slot)
            if target in payload.get('slots', {}) or (target in aliases and aliases[target] != canonical):
                raise ValueError('Target workflow slot already exists')
            additions[target] = canonical
            display[canonical] = target
            rules = dict(payload.get('slot_path_aliases', {}).get(slot, {}))
            folders = ['assets/characters'] if kind == 'character' else ['assets/scenes', 'director_worlds']
            for folder in folders:
                for root in (Path(folder), Path(source.project_dir) / folder):
                    before_root, after_root = str(root / old), str(root / new)
                    rules = {key: after_root if value == before_root else value for key, value in rules.items()}
                    rules[before_root] = after_root
            path_rules[target] = rules
            metadata = dict(payload.get('slot_metadata_aliases', {}).get(slot, {}))
            if kind == 'character':
                metadata['character_name'] = new
            elif scene_base:
                metadata['scene_id'] = new
            else:
                metadata['base_scene_id'] = new
                if slot.startswith(f'scene:{old}:base:'):
                    metadata['scene_id'] = new
            metadata_rules[target] = metadata
    return {'path': str(path), 'aliases_before': {key: aliases.get(key) for key in additions},
            'aliases_after': additions,
            'display_before': {key: payload.get('slot_display_names', {}).get(key) for key in display},
            'display_after': display,
            'paths_before': {key: payload.get('slot_path_aliases', {}).get(key) for key in path_rules},
            'paths_after': path_rules,
            'metadata_before': {key: payload.get('slot_metadata_aliases', {}).get(key) for key in metadata_rules},
            'metadata_after': metadata_rules,
            'slots': {key: payload['slots'][key] for key in display},
            'versions': [v for v in payload.get('versions', []) if v['slot_id'] in display],
            'events': [e for e in payload.get('adoption_events', []) if e['slot_id'] in display]}


def _install_workflow(plan):
    if not plan:
        return
    path = Path(plan['path'])
    if not path.exists():
        raise SourceSyncPending('Workflow disappeared during rename')
    payload = json.loads(path.read_text())
    affected = plan['slots']
    if ({key: payload.get('slots', {}).get(key) for key in affected} != affected
        or [v for v in payload.get('versions', []) if v['slot_id'] in affected] != plan['versions']
        or [e for e in payload.get('adoption_events', []) if e['slot_id'] in affected] != plan['events']):
        raise SourceSyncPending('Affected workflow slot changed during rename; evidence retained')
    for field, prefix in [('slot_aliases', 'aliases'), ('slot_display_names', 'display'),
                          ('slot_path_aliases', 'paths'), ('slot_metadata_aliases', 'metadata')]:
        values = payload.setdefault(field, {})
        for key, target in plan[prefix + '_after'].items():
            if values.get(key) not in (plan[prefix + '_before'][key], target):
                raise SourceSyncPending('Workflow alias changed during rename')
            values[key] = target
    from novelvideo.production_workflow.store import _atomic_write_json
    _atomic_write_json(path, payload)
    with path.open('rb') as stream:
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


async def rename_asset(source, kind, old, new, *, identity_id=None, updates=None):
    updates = copy.deepcopy(updates or {})
    old, new = validate_path_segment(old), validate_path_segment(new)
    request = json.dumps([kind, old, new, identity_id, updates], sort_keys=True)
    async with asset_project_lock(source.state_dir):
        await source._ensure_db()
        with _connect(source) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS asset_rename_intents (token TEXT PRIMARY KEY, request TEXT UNIQUE NOT NULL, evidence TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0)')
            existing = conn.execute('SELECT * FROM asset_rename_intents WHERE request=?', (request,)).fetchone()
        if existing:
            if not existing['complete']:
                await _resume(source, dict(existing))
            else:
                with _connect(source) as conn:
                    table = 'scenes' if kind == 'scene' else 'characters'
                    prior = conn.execute(f'SELECT * FROM {table} WHERE name=?', (old,)).fetchone()
                    target = conn.execute(f'SELECT * FROM {table} WHERE name=?', (new,)).fetchone()
                if kind == 'identity':
                    identities = json.loads(prior['identities_json']) if prior else []
                    current = next((i for i in identities if i['identity_id'] == identity_id and i['identity_name'] == new), None)
                    if current is None:
                        raise ValueError('Previous rename result is no longer current')
                    # Identity editors submit the current display name with
                    # ordinary metadata edits too. A completed rename must not
                    # swallow a later explicit edit that reuses its old body.
                    metadata = {k: v for k, v in updates.items() if k not in ('identity_id', 'character_name', 'identity_name')}
                    if any(current.get(key) != value for key, value in metadata.items()):
                        await source._update_character_identity_metadata(old, identity_id, **metadata)
                elif prior is not None or target is None:
                    raise ValueError('Previous rename locator was reused or its result is no longer current')
            return True
        assert_no_pending_rename(source)
        assert_asset_locators_writable(source, 'character' if kind == 'identity' else kind,
                                       [old] if kind == 'identity' else [old, new])
        token = uuid4().hex
        with _connect(source) as conn:
            tables = {table: _rows(conn, table) for table in ('characters', 'scenes', 'episodes')}
        before = copy.deepcopy(tables)
        files = []
        root = Path(source.project_dir) / 'assets'
        if kind in ('character', 'identity'):
            character = next((r for r in tables['characters'] if r['name'] == old), None)
            if character is None:
                raise ValueError(f'Character {old} not found')
            identities = json.loads(character['identities_json'])
            if kind == 'character':
                if any(r['name'] == new or new in json.loads(r['aliases_json'] or '[]') for r in tables['characters']):
                    raise ValueError(f'Character {new} already exists')
                character['name'] = new
                for identity in identities:
                    identity['character_name'] = new
                for episode in tables['episodes']:
                    names = json.loads(episode['character_names'] or '[]')
                    episode['character_names'] = json.dumps([new if n == old else n for n in names], ensure_ascii=False)
                    defaults = json.loads(episode['identity_default_map_json'] or '{}')
                    if old in defaults:
                        if new in defaults:
                            raise ValueError('Episode identity default target already exists')
                        defaults[new] = defaults.pop(old)
                    episode['identity_default_map_json'] = json.dumps(defaults, ensure_ascii=False)
                character_files = _copy_plan(root / 'characters' / old, root / 'characters' / new)
                from novelvideo.utils.path_resolver import canonical_identity_portrait_path
                for identity in identities:
                    prior = canonical_identity_portrait_path(Path(source.project_dir), old, identity['identity_name'])
                    target = canonical_identity_portrait_path(Path(source.project_dir), new, identity['identity_name'])
                    prior_key = str(prior.relative_to(root / 'characters' / old))
                    target_key = str(target.relative_to(root / 'characters' / new))
                    if prior_key in character_files['before'] and prior_key != target_key:
                        if target_key in character_files['after']:
                            raise ValueError('Target identity portrait filename already exists')
                        character_files['after'][target_key] = character_files['before'][prior_key]
                        character_files.setdefault('sources', {})[target_key] = prior_key
                files.append(character_files)
            else:
                identity = next((i for i in identities if i['identity_id'] == identity_id), None)
                if identity is None:
                    raise ValueError(f'Identity {identity_id} not found')
                if any(i['identity_id'] != identity_id and i['identity_name'] == new for i in identities):
                    raise ValueError('Identity name already exists')
                safe = lambda value: re.sub(r'[/\\:*?"<>|]', '_', value)
                old_image = root / 'characters' / old / 'identities' / (safe(identity['identity_name']) + '.png')
                new_image = old_image.with_name(safe(new) + '.png')
                if old_image != new_image:
                    from novelvideo.utils.path_resolver import canonical_identity_path, canonical_identity_portrait_path, canonical_identity_costume_path
                    for resolver in (canonical_identity_path, canonical_identity_portrait_path, canonical_identity_costume_path):
                        prior_path, target_path = resolver(Path(source.project_dir), old, identity['identity_name']), resolver(Path(source.project_dir), old, new)
                        if prior_path != target_path:
                            files.append(_copy_plan(prior_path, target_path))
                    folder = old_image.parent
                    files.append(_copy_plan(folder / (safe(identity['identity_name']) + '_portrait.png'),
                                            folder / (safe(new) + '_portrait.png')))
                    files.append(_copy_plan(folder / safe(identity['identity_name']), folder / safe(new)))
                identity.update(updates)
                identity['identity_name'] = new
                identity['identity_id'] = identity_id
                identity['character_name'] = old
            character['identities_json'] = json.dumps(identities, ensure_ascii=False)
            if kind == 'character':
                for key, value in updates.items():
                    column = {'aliases': 'aliases_json', 'voice_facts': 'voice_facts_json', 'voice_samples_by_age_group': 'voice_samples_by_age_group_json'}.get(key, key)
                    if column in character and column not in ('name', 'identities_json', 'created_at', 'updated_at'):
                        character[column] = json.dumps(value, ensure_ascii=False) if column.endswith('_json') and not isinstance(value, str) else value
                if updates.get('is_main'):
                    for row in tables['characters']:
                        if row is not character:
                            row['is_main'] = 0
        elif kind == 'scene':
            scene = next((r for r in tables['scenes'] if r['name'] == old), None)
            if scene is None:
                raise ValueError(f'Scene {old} not found')
            if any(r['name'] == new or new in json.loads(r['aliases_json'] or '[]') for r in tables['scenes']):
                raise ValueError(f'Scene {new} already exists')
            if 'base_scene_id' in updates and updates['base_scene_id'] != scene['base_scene_id']:
                raise ValueError('Rename cannot move a scene into another base asset')
            scene['name'] = new
            for row in tables['scenes']:
                if row['base_scene_id'] == old:
                    row['base_scene_id'] = new
            # Episode menus are mutable catalogue references, unlike frozen
            # bindings and script text. Include both base and variant locators
            # in the same SQL evidence/CAS transaction as the source rename.
            for episode in tables['episodes']:
                menu = json.loads(episode['scene_menu_json'] or '[]')
                changed = False
                for index, item in enumerate(menu):
                    if isinstance(item, dict):
                        for field in ('scene_id', 'base_scene_id'):
                            if item.get(field) == old:
                                item[field] = new
                                changed = True
                    elif item == old:  # Legacy string-valued menu locator.
                        menu[index] = new
                        changed = True
                if changed:
                    episode['scene_menu_json'] = json.dumps(menu, ensure_ascii=False)
            for key, value in updates.items():
                column = {'aliases': 'aliases_json', 'stale_reference_kinds': 'stale_reference_kinds_json'}.get(key, key)
                if column in scene and column not in ('name', 'created_at', 'updated_at'):
                    scene[column] = json.dumps(value, ensure_ascii=False) if column.endswith('_json') and not isinstance(value, str) else value
            files.append(_copy_plan(root / 'scenes' / old, root / 'scenes' / new))
            from novelvideo.director_world.stage_manifest import stage_dir
            files.append(_copy_plan(stage_dir(Path(source.project_dir), old).parent,
                                    stage_dir(Path(source.project_dir), new).parent, manifest_name=new))
        else:
            raise ValueError('Unsupported rename kind')
        if kind in ('character', 'identity'):
            for identity in identities:
                for key in ('portrait_image', 'costume_image', 'reference_audio_path'):
                    if key in identity:
                        identity[key] = _repath(identity[key], files, source.project_dir)
                identity['reference_images'] = [_repath(value, files, source.project_dir) for value in identity.get('reference_images', [])]
            character['identities_json'] = json.dumps(identities, ensure_ascii=False)
            if kind == 'character':
                character['reference_audio_path'] = _repath(character.get('reference_audio_path', ''), files, source.project_dir)
                samples = json.loads(character.get('voice_samples_by_age_group_json') or '{}')
                for sample in samples.values():
                    if isinstance(sample, dict) and 'path' in sample:
                        sample['path'] = _repath(sample['path'], files, source.project_dir)
                character['voice_samples_by_age_group_json'] = json.dumps(samples, ensure_ascii=False)
        else:
            scene['spatial_layout_image'] = _repath(scene['spatial_layout_image'], files, source.project_dir)
        changes = []
        for table, rows in tables.items():
            key = 'number' if table == 'episodes' else 'name'
            for prior, after in zip(before[table], rows):
                if prior != after:
                    if 'updated_at' in after:
                        after['updated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    changes.append({'table': table, 'key': key, 'before': prior, 'after': after})
        workflow = _workflow_plan(source, kind, old, new,
            scene_base=next((r['base_scene_id'] for r in before['scenes'] if r['name'] == old), '') if kind == 'scene' else '')
        for plan in files:
            plan['token'] = token
        sync = SourceSync.existing(source)
        source_kind = 'scene' if kind == 'scene' else 'character'
        changed_names = {c['before']['name'] for c in changes if c['table'] == source_kind + 's'}
        mappings = sync.mappings() if sync else []
        after_mappings = copy.deepcopy(mappings)
        for mapping in after_mappings:
            if mapping['kind'] == kind and mapping['source_name'] == old and (kind == 'scene' or not mapping['parent_id']):
                mapping['source_name'] = new
        keys = [(m['kind'], m['source_name'], m['parent_id']) for m in after_mappings]
        if len(keys) != len(set(keys)):
            raise ValueError('Target semantic source locator already exists')
        sync_token = await sync.prepare(source_kind, changed_names) if sync else None
        evidence = {'changes': changes, 'files': files, 'workflow': workflow,
                    'sync_token': sync_token, 'mappings': mappings, 'after_mappings': after_mappings}
        with _connect(source) as conn:
            conn.execute('INSERT INTO asset_rename_intents VALUES (?,?,?,0)', (token, request, json.dumps(evidence)))
        try:
            _checkpoint('prepared')
            await _resume(source, {'token': token, 'evidence': json.dumps(evidence)})
        except Exception as exc:
            raise SourceSyncPending(f'Asset rename {token} requires reconciliation: {exc}') from exc
        return True


async def _resume(source, entry):
    evidence = json.loads(entry['evidence'])
    with _connect(source) as conn:
        conn.execute('BEGIN IMMEDIATE')
        for change in evidence['changes']:
            table, key = change['table'], change['key']
            before, after = change['before'], change['after']
            old = conn.execute(f'SELECT * FROM {table} WHERE {key}=?', (before[key],)).fetchone()
            new = conn.execute(f'SELECT * FROM {table} WHERE {key}=?', (after[key],)).fetchone()
            if new is not None and dict(new) == after and (before[key] == after[key] or old is None):
                continue
            if old is None or dict(old) != before or (before[key] != after[key] and new is not None):
                raise SourceSyncPending('Rename SQL source drifted; evidence retained')
            conn.execute(f'UPDATE {table} SET ' + ','.join(f'{column}=?' for column in after) + f' WHERE {key}=?',
                         [*after.values(), before[key]])
    _checkpoint('sql_committed')
    for plan in evidence['files']:
        _install_files(plan)
    _checkpoint('media_installed')
    _install_workflow(evidence['workflow'])
    sync = SourceSync.existing(source)
    if sync and evidence['sync_token']:
        entries = sync.entries(evidence['sync_token'])
        if entries:
            mappings = {m['object_id']: m for m in evidence['after_mappings']}
            actual = await sync.records([mappings[oid] for oid in entries], all_mappings=mappings)
            with _connect(source) as conn:
                for change in evidence['changes']:
                    after = change['after']
                    row = conn.execute(f"SELECT * FROM {change['table']} WHERE {change['key']}=?", (after[change['key']],)).fetchone()
                    if row is None or dict(row) != after:
                        raise SourceSyncPending('Rename source changed before semantic commit')
            sync.capture_after(evidence['sync_token'], actual, entries)
            _checkpoint('before_semantic_commit')
            sync.apply(sync.entries(evidence['sync_token']), actual, mappings_after=mappings, rename_token=entry['token'])
        else:
            with sync.objects.transaction() as conn:
                table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='asset_rename_commits'").fetchone()
                if not table or not conn.execute('SELECT 1 FROM asset_rename_commits WHERE token=?', (entry['token'],)).fetchone():
                    raise SourceSyncPending('Rename semantic completion proof is missing')
        _checkpoint('semantic_committed')
    await source.load_graph_state()
    with _connect(source) as conn:
        conn.execute('UPDATE asset_rename_intents SET complete=1 WHERE token=?', (entry['token'],))


async def reconcile_renames(source):
    async with asset_project_lock(source.state_dir):
        with _connect(source) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='asset_rename_intents'").fetchone():
                return
            entries = [dict(r) for r in conn.execute('SELECT * FROM asset_rename_intents WHERE complete=0')]
        for entry in entries:
            await _resume(source, entry)
