"""Durable, explicitly scoped character repair; never submits model work."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.models import NovelCharacter, CharacterIdentity
from novelvideo.production_workflow import ProductionWorkflowStore
from novelvideo.production_workflow.character_portraits import validate_character_name
from novelvideo.production_workflow.slot_ids import character_identity_portrait_slot_id, character_portrait_slot_id, character_state_slot_id
from .planned_binding_service import resolve_planned_reference_preview, required_binding_keys_for_director_group

_locks: dict[str, asyncio.Lock] = {}


class RepairStore:
    def __init__(self, state_dir, project_id):
        self.path = Path(state_dir) / 'data.db'
        self.project_id = str(project_id)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS character_reference_repairs (project_id TEXT NOT NULL, request_id TEXT NOT NULL, repair_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id, request_id), UNIQUE(project_id, repair_id))')

    def all(self):
        with sqlite3.connect(self.path) as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM character_reference_repairs WHERE project_id=?', (self.project_id,))]

    def get(self, repair_id):
        return next((r for r in self.all() if r['repair_id'] == repair_id), None)

    def save(self, item):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO character_reference_repairs VALUES (?,?,?,?) ON CONFLICT(project_id,request_id) DO UPDATE SET payload=excluded.payload', (self.project_id, item['request_id'], item['repair_id'], json.dumps(item, ensure_ascii=False)))
        return item

    def insert(self, item):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR IGNORE INTO character_reference_repairs VALUES (?,?,?,?)', (self.project_id, item['request_id'], item['repair_id'], json.dumps(item, ensure_ascii=False)))
        return next(r for r in self.all() if r['request_id'] == item['request_id'])


@asynccontextmanager
async def repair_lock(ctx):
    from novelvideo.production_workflow import production_workflow_project_lock
    async with _locks.setdefault(str(Path(ctx.state_dir).resolve()), asyncio.Lock()):
        with production_workflow_project_lock(ctx.state_dir):
            yield


def assert_source(ctx, item, expected=None):
    if expected is not None and expected != item['source_reference_revision']:
        raise ValueError('REFERENCE_REVISION_STALE')
    plan = DirectorPlanStore(ctx.output_dir).load_active(item['episode_number'])
    if plan is None or plan.revision_id != item['source_plan_revision_id']:
        raise ValueError('SOURCE_CHANGED')
    if item.get('source_persisted'):
        with sqlite3.connect(Path(ctx.state_dir) / 'data.db') as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM planned_reference_bindings WHERE project_id=? AND episode_number=? AND source_plan_revision_id=? AND asset_kind=?', (str(ctx.project_id), item['episode_number'], item['source_plan_revision_id'], 'character_identity')).fetchall()
        source = item['source_binding']
        def matches(row):
            if set(json.loads(row['group_ids_json'])) != set(source['group_ids']) or set(json.loads(row['shot_ids_json'])) != set(source['shot_ids']):
                return False
            if row['entity_id'] == item['entity_id']:
                return row['asset_slot_id'] == source['asset_slot_id']
            # Our own character creation can make a reference refresh resolve
            # the exact original requirement to its chosen identity.
            return item.get('owned_created_identity') == [item['character_name'], item['identity_id']] and row['entity_id'] == item['identity_id'] and row['asset_slot_id'] in {
                character_state_slot_id(item['character_name'], item['identity_id']),
                character_portrait_slot_id(item['character_name']),
                character_identity_portrait_slot_id(item['character_name'], item['identity_id']),
            }
        if not any(matches(row) for row in rows):
            raise ValueError('SOURCE_CHANGED')
    return plan


def choices(sql):
    return [dict(character_name=c.name, identity_id=i.identity_id, label=f'{c.name} · {i.identity_name}', has_image=bool(i.reference_images or getattr(i, 'portrait_image', '')))
            for c in sql.get_all_characters() for i in c.identities]


async def create_repair(ctx, sql, episode, revision, body):
    journal = RepairStore(ctx.state_dir, ctx.project_id)
    old = next((r for r in journal.all() if r['request_id'] == body.request_id), None)
    if old:
        if (old['episode_number'], old['source_plan_revision_id'], old['binding_id'], old['source_reference_revision']) != (episode, revision, body.binding_id, body.expected_reference_revision):
            raise ValueError('REQUEST_ID_CONFLICT')
        return old
    plan = DirectorPlanStore(ctx.output_dir).load_active(episode)
    if plan is None or plan.revision_id != revision:
        raise ValueError('SOURCE_CHANGED')
    bindings = await sql.list_planned_reference_bindings(episode)
    source = next((b for b in bindings if b.binding_id == body.binding_id), None)
    group_id = body.group_id or (source.group_ids[0] if source and source.group_ids else '')
    preview = await resolve_planned_reference_preview(sql, ProductionWorkflowStore(Path(ctx.state_dir) / 'production_workflow.json'), project_id=str(ctx.project_id), episode_number=episode, group_id=group_id, project_dir=Path(ctx.output_dir), active_plan_revision_id=revision, required_binding_keys=required_binding_keys_for_director_group(plan, group_id))
    if preview.reference_revision != body.expected_reference_revision:
        raise ValueError('REFERENCE_REVISION_STALE')
    binding = next((b for b in preview.bindings if b.binding_id == body.binding_id), None)
    if binding is None or binding.asset_kind != 'character_identity' or binding.status == 'ready':
        raise ValueError('INVALID_CHARACTER_REPAIR')
    evidence = []
    for group in plan.groups:
        if group.id not in binding.group_ids:
            continue
        for shot in group.shots:
            if binding.shot_ids and shot.id not in binding.shot_ids:
                continue
            evidence.append(f'{group.id} / {shot.id}：{shot.subject}；{shot.action}')
    exact = [(c, i) for c in sql.get_all_characters() for i in c.identities if i.identity_id == binding.entity_id]
    character, identity = exact[0] if len(exact) == 1 else (None, None)
    item = dict(repair_id='repair_' + uuid.uuid4().hex, request_id=body.request_id, project_id=str(ctx.project_id), episode_number=episode, source_plan_revision_id=revision, source_reference_revision=body.expected_reference_revision, binding_id=body.binding_id, group_id=group_id, entity_id=binding.entity_id, display_label=binding.display_label, source_binding=binding.model_dump(mode='json'), evidence=evidence, appearance=identity.face_prompt if identity else '', costume=identity.appearance_details if identity else '', character_name=character.name if character else '', identity_id=identity.identity_id if identity else '', candidate_id='', status='selected' if identity else 'draft', error='', reuse_identity=bool(identity))
    item['source_persisted'] = source is not None
    return journal.insert(item)


async def select_repair(ctx, sql, item, body):
    assert_source(ctx, item, body.expected_reference_revision)
    if item['status'] == 'bound':
        raise ValueError('REPAIR_ALREADY_BOUND')
    name = validate_character_name(body.character_name.strip())
    identity_id = body.identity_id or f'{name}_repair_{item["repair_id"][7:19]}'
    character = sql.get_character(name)
    if item['reuse_identity'] and (name, identity_id) != (item['character_name'], item['identity_id']):
        raise ValueError('MISSING_IMAGE_REUSES_IDENTITY')
    if item['reuse_identity'] and body.mode == 'create':
        raise ValueError('MISSING_IMAGE_REUSES_IDENTITY')
    if body.mode == 'existing':
        if character is None or not any(i.identity_id == identity_id for i in character.identities):
            raise ValueError('PROJECT_IDENTITY_NOT_FOUND')
    else:
        if character is not None and item.get('owned_created_identity') != [name, identity_id]:
            raise ValueError('CHARACTER_EXISTS_SELECT_EXPLICITLY')
        if character is None:
            # Persist ownership before the asset write so a crashed retry can
            # recognize its own newly created character without merging names.
            item.update(character_name=name, identity_id=identity_id, status='creating', owned_created_identity=[name, identity_id])
            RepairStore(ctx.state_dir, ctx.project_id).save(item)
            character = NovelCharacter(name=name, face_prompt=body.appearance, description='\n'.join(item['evidence']))
            character.identities = [CharacterIdentity(identity_id=identity_id, character_name=name, identity_name='剧情造型', face_prompt=body.appearance, appearance_details=body.costume, source='api')]
            await sql.add_character(character)
    if body.mode == 'create' and (body.appearance or body.costume):
        await sql.update_character_identity(name, identity_id, face_prompt=body.appearance, appearance_details=body.costume)
    item.update(character_name=name, identity_id=identity_id, appearance=body.appearance, costume=body.costume, status='selected', error='')
    if body.mode == 'existing':
        from novelvideo.api.routes.character_casting import current_version
        current = current_version(ctx, name, identity_id)
        if not current:
            workflow = ProductionWorkflowStore(Path(ctx.state_dir) / 'production_workflow.json')
            slot_id = character_state_slot_id(name, identity_id)
            try:
                slot, versions = workflow.get_slot(slot_id)
                version = versions.get(slot.current_version_id)
                if slot.asset_kind == 'character_state' and version and version.adoption_status.value == 'adopted':
                    current = {'adoption_status': 'adopted', '_path': Path(ctx.output_dir) / version.asset_path}
                    item['target_slot_id'] = slot_id
            except KeyError:
                pass
        if current and current['adoption_status'] == 'adopted':
            # An existing adopted image is enough; do not force paid regeneration.
            from .reference_uploads import validate_reference_image
            validate_reference_image(current['_path'], allowed_roots=(Path(ctx.output_dir) / 'assets' / 'characters' / name,))
            episode = sql.get_episode(item['episode_number'])
            if episode is None:
                raise ValueError('EPISODE_NOT_FOUND')
            await sql.update_episode(item['episode_number'], identity_ids=list(dict.fromkeys([*episode.identity_ids, identity_id])))
            item.update(status='bound', candidate_id=current.get('candidate_id') or '')
    return RepairStore(ctx.state_dir, ctx.project_id).save(item)


async def bind_repair(ctx, sql, item, candidate_id, expected=None):
    from novelvideo.api.routes.character_casting import candidate_store, current_version
    journal = RepairStore(ctx.state_dir, ctx.project_id)
    try:
        assert_source(ctx, item, expected)
        candidate = candidate_store(ctx).get(candidate_id)
        if candidate is None or (candidate.character_id, candidate.identity_id) != (item['character_name'], item['identity_id']):
            raise ValueError('CANDIDATE_SCOPE_MISMATCH')
        current = current_version(ctx, item['character_name'], item['identity_id'])
        if not current or current['candidate_id'] != candidate_id or current['adoption_status'] != 'adopted':
            raise ValueError('CANDIDATE_NOT_ADOPTED')
        episode = sql.get_episode(item['episode_number'])
        if episode is None:
            raise ValueError('EPISODE_NOT_FOUND')
        await sql.update_episode(item['episode_number'], identity_ids=list(dict.fromkeys([*episode.identity_ids, item['identity_id']])))
        item.update(candidate_id=candidate_id, status='bound', error='')
        journal.save(item)
        return item
    except Exception as exc:
        if item['status'] == 'bound':
            return {**item, 'error': str(exc)}
        item.update(candidate_id=candidate_id, status='source_changed' if str(exc) == 'SOURCE_CHANGED' else 'binding_failed', error=str(exc))
        journal.save(item)
        return item


async def recover_adopted_repair(ctx, sql, item, *, can_bind):
    """Recover a committed adoption whose post-adoption callback was interrupted."""
    if item['status'] not in ('selected', 'binding_failed') or not item.get('identity_id') or not item.get('character_name'):
        return item
    assert_source(ctx, item)
    from novelvideo.api.routes.character_casting import candidate_store, current_version
    current = current_version(ctx, item['character_name'], item['identity_id'])
    if not current or current['adoption_status'] != 'adopted' or not current.get('candidate_id'):
        return item
    candidate_id = current['candidate_id']
    candidate = candidate_store(ctx).get(candidate_id)
    if candidate is None or (candidate.character_id, candidate.identity_id) != (item['character_name'], item['identity_id']):
        return item
    if can_bind:
        return await bind_repair(ctx, sql, item, candidate_id, item['source_reference_revision'])
    # Viewer reads expose an actionable retry without changing episode bindings.
    return {**item, 'candidate_id': candidate_id, 'status': 'binding_failed', 'error': 'ADOPTED_CANDIDATE_BINDING_PENDING'}


def apply_repairs(database_path, bindings, *, project_id=None, episode_number=None, group_id=None, active_plan_revision_id=None):
    """Overlay exact frozen requirements, preserving the plan's entity keys."""
    project_id = project_id or (bindings[0].project_id if bindings else None)
    if not project_id:
        return bindings
    try:
        with sqlite3.connect(f'{Path(database_path).resolve().as_uri()}?mode=ro', uri=True) as db:
            rows = db.execute('SELECT payload FROM character_reference_repairs WHERE project_id=?', (project_id,)).fetchall()
    except sqlite3.OperationalError:
        return bindings
    repairs = [json.loads(row[0]) for row in rows]
    from .planned_bindings import PlannedReferenceBinding
    bindings = list(bindings)
    for repair in repairs:
        source = repair.get('source_binding', {})
        if (repair['status'] == 'bound' and repair.get('source_persisted') is False
            and repair['episode_number'] == episode_number
            and repair['source_plan_revision_id'] == active_plan_revision_id
            and group_id in source.get('group_ids', [])
            and not any(b.asset_kind == 'character_identity' and b.project_id == project_id
                and b.episode_number == episode_number and b.source_plan_revision_id == active_plan_revision_id
                and b.entity_id == repair['entity_id'] and group_id in b.group_ids for b in bindings)):
            bindings.append(PlannedReferenceBinding.create(project_id=project_id, episode_number=episode_number,
                source_plan_revision_id=active_plan_revision_id, asset_kind='character_identity',
                entity_id=repair['entity_id'], asset_slot_id='', status='missing_asset', resolution='auto_matched',
                display_label=repair['display_label'], group_ids=tuple(source['group_ids']),
                shot_ids=tuple(source.get('shot_ids', [])), beat_ids=tuple(source.get('beat_ids', []))))
    result = []
    for binding in bindings:
        match = next((r for r in reversed(repairs) if r['status'] == 'bound' and r['episode_number'] == binding.episode_number and r['source_plan_revision_id'] == binding.source_plan_revision_id and r['entity_id'] == binding.entity_id and binding.asset_kind == 'character_identity' and (r['binding_id'] == binding.binding_id or (binding.asset_slot_id == r.get('source_binding', {}).get('asset_slot_id', '') and set(binding.group_ids) == set(r.get('source_binding', {}).get('group_ids', [])) and set(binding.shot_ids) == set(r.get('source_binding', {}).get('shot_ids', []))))), None)
        if match:
            slot = character_identity_portrait_slot_id(match['character_name'], match['identity_id']) if match['identity_id'] else character_portrait_slot_id(match['character_name'])
            values = binding.model_dump(exclude={'binding_id'})
            values.update(asset_slot_id=slot, status='ready', resolution='explicit_fallback')
            if match.get('target_slot_id'):
                values.update(asset_slot_id=match['target_slot_id'], entity_id=match['identity_id'], base_entity_id=binding.entity_id, resolution='manually_confirmed')
            binding = type(binding).create(**values)
        result.append(binding)
    return tuple(result)
