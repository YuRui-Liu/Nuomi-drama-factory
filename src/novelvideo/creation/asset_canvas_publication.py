"""Trusted canvas-to-asset publication, sharing the existing media journal."""
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from novelvideo.production_workflow.coordination import asset_project_lock
from novelvideo.freezone.slots import slot_target_path, SCENE_ASSET_KINDS, SCENE_3GS_PLY_TARGETS
from .asset_media import capture_blob, controlled_path
from .asset_media_publication import character_media_publication, scene_media_publication, scene_mutation_path


def writable_path(root,value):
    path=controlled_path(root,value)
    if path.is_relative_to(Path(root).absolute()/'assets/.creation-media'):
        raise ValueError('Creation media history is read-only')
    return path


def backup_target(root,target):
    target=writable_path(root,target)
    if not target.exists():return None
    backup=writable_path(root,target.parent/'_history'/f'{target.name}.{uuid4().hex}.bak')
    backup.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(target,backup)
    return backup


def _unique_asset(records, locator):
    matches=[record for record in records if locator in {record.name, *record.aliases}]
    if len(matches)!=1:
        raise ValueError('Asset locator is missing or ambiguous')
    return deepcopy(matches[0])


@dataclass
class CanvasPublication:
    source: Path
    original_source: Path
    target: Path
    slot: object
    store: object = None


async def copy_canvas_publication(ctx, root, publication, *, adapt=None):
    """Execute an already validated target, inside the caller's publication.

    Keep synchronous workflow/casting guards on the owning Task's thread.
    The callback is only the legacy frame image-size adaptation.
    """
    from novelvideo.production_workflow import production_workflow_project_lock
    from novelvideo.character_visual.casting_recovery import (
        assert_casting_path_mutation_allowed, assert_legacy_portrait_mutation_allowed,
        update_identity_portrait_reference,
    )
    from novelvideo.freezone.slots import sync_slot_after_write
    from .asset_adapter import registration_io
    from novelvideo.production_workflow.coordination import synchronous_lock_is_owned
    if not synchronous_lock_is_owned(ctx.state_dir):
        raise RuntimeError('Canvas media copy requires the owning Task project lock')
    target, slot = publication.target, publication.slot
    with production_workflow_project_lock(ctx.state_dir):
        writable_path(root, target)
        writable_path(root, target.parent/'_history')
        assert_casting_path_mutation_allowed(root, ctx.state_dir, target)
        if slot.kind == 'identity_portrait':
            assert_legacy_portrait_mutation_allowed(root, ctx.state_dir, slot.character, slot.identity_id)
    def copy_files():
        writable_path(root, target)
        writable_path(root, target.parent/'_history')
        same_file = publication.original_source == target
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = None if same_file else backup_target(root, target)
        if same_file:
            adaptation = {'adapted': False, 'same_file': True}
        elif adapt:
            adaptation = adapt(publication.source, target)
        else:
            shutil.copy2(publication.source, target)
            adaptation = {'adapted': False}
        return backup, adaptation
    backup, adaptation = await registration_io(copy_files)
    with production_workflow_project_lock(ctx.state_dir):
        sync_slot_after_write(root, slot, target)
        if slot.kind == 'identity_portrait':
            update_identity_portrait_reference(root, ctx.state_dir, slot.character, slot.identity_id, str(target))
    return backup, adaptation


async def update_canvas_publication_source(publication, root):
    """Source SQL remains in the same outer Task-owned media publication."""
    slot, target = publication.slot, publication.target
    if slot.kind in {'identity', 'identity_costume'}:
        changes = {'costume_image': str(target)} if slot.kind == 'identity_costume' else {}
        await publication.store.update_character_identity(slot.character, slot.identity_id, **changes)
    if slot.kind == 'scene_spatial_layout':
        await publication.store.update_scene(slot.scene_id, spatial_layout_image=target.relative_to(root).as_posix())


@asynccontextmanager
async def canvas_asset_publication(ctx,root,slot,source_path, *, publish=True):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.utils.path_resolver import canonical_identity_path, canonical_identity_costume_path, canonical_identity_portrait_path
    from novelvideo.director_world import stage_manifest
    from .asset_adapter import registration_io
    root=Path(root);slot=deepcopy(slot)
    requested_locator=getattr(slot,'character',None) or getattr(slot,'scene_id',None)
    source_path=controlled_path(root,source_path)
    source_proof=await registration_io(capture_blob,root,source_path,archive=False)
    store=None
    character=scene=None
    try:
        if slot.kind in {'portrait','identity','identity_costume','identity_portrait'} or slot.kind in SCENE_ASSET_KINDS:
            if not (Path(ctx.state_dir)/'data.db').is_file():
                raise ValueError('Asset source does not exist')
            store=SQLiteStore('canvas-push',output_dir=str(root),state_dir=str(ctx.state_dir))
            await store.load_graph_state()
            if slot.kind in SCENE_ASSET_KINDS:
                scene=_unique_asset(await store.list_scenes(),requested_locator)
                slot=slot.model_copy(update={'scene_id':scene.name})
                if scene.base_scene_id and (scene.base_scene_id==scene.name or await store.get_scene(scene.base_scene_id) is None):
                    raise ValueError('Scene variant parent is unavailable')
            else:
                character=_unique_asset(await store.list_characters(),requested_locator)
                slot=slot.model_copy(update={'character':character.name})
                if slot.kind!='portrait':
                    matches=[i for i in character.identities if slot.identity_id in {i.identity_id,i.identity_name}]
                    if len(matches)!=1:raise ValueError('Identity locator is missing or ambiguous')
                    identity=matches[0]
                    slot=slot.model_copy(update={'identity_id':identity.identity_id})
        if character is not None and slot.kind!='portrait':
            resolver={'identity':canonical_identity_path,'identity_costume':canonical_identity_costume_path,
                      'identity_portrait':canonical_identity_portrait_path}[slot.kind]
            target=resolver(root,character.name,identity.identity_name)
        else:
            target=slot_target_path(root,slot)
        if slot.kind in SCENE_3GS_PLY_TARGETS:
            suffix=source_path.suffix.lower()
            # The product resolver prefers same-stem SOG sidecars. A format
            # switch must not silently select an older model or delete it.
            if target.suffix.lower()!=suffix or (suffix!='.sog' and target.with_suffix('.sog').exists()):
                target=target.with_name(f'{target.stem}.push-{source_proof["sha256"]}{suffix}')
            else:
                target=target.with_suffix(suffix)
        target=writable_path(root,target)
        target_proof=await registration_io(capture_blob,root,target,archive=False) if target.is_file() else None

        def boundary(*,publish,expected=None):
            if character is not None:
                return character_media_publication(root,ctx.state_dir,character.name,
                    getattr(slot,'identity_id',None),actor=str(getattr(ctx,'requester_user_id',None) or 'canvas-push'),
                    publish=publish,expected=expected,input_character=character)
            if scene is not None:
                return scene_media_publication(root,ctx.state_dir,scene,
                    actor=str(getattr(ctx,'requester_user_id',None) or 'canvas-push'),publish=publish,expected=expected)
            return asset_project_lock(ctx.state_dir)

        async def validate():
            if character is not None or scene is not None:
                from .asset_rename import assert_asset_locators_writable, assert_no_pending_rename
                kind='character' if character is not None else 'scene'
                # Validate the original request too: resolving an old name as
                # an alias must never bypass a retired locator or pending rename.
                assert_no_pending_rename(store,kind=kind,names={requested_locator})
                assert_asset_locators_writable(store,kind,{requested_locator})
                records=await (store.list_characters() if character is not None else store.list_scenes())
                current_asset=_unique_asset(records,requested_locator)
                if current_asset.name!=(character.name if character is not None else scene.name):
                    raise ValueError('Asset alias changed before publication')
            writable_path(root,target)
            writable_path(root,target.parent/'_history')
            if await registration_io(capture_blob,root,source_path,archive=False)!=source_proof:
                raise ValueError('Canvas source changed before publication')
            current=await registration_io(capture_blob,root,target,archive=False) if target.is_file() else None
            if current!=target_proof:raise ValueError('Canvas target changed before publication')
            if scene is not None:
                scene_mutation_path(root,scene.name,target)
                manifest=stage_manifest.manifest_path(root,scene.name)
                scene_mutation_path(root,scene.name,manifest)
                scene_mutation_path(root,scene.name,manifest.with_suffix(manifest.suffix+'.lock'))

        async with boundary(publish=False) as frozen:
            await validate()
        async with boundary(publish=publish,expected=frozen):
            await validate()
            # The synchronous copy uses an isolated, verified source, including
            # when the caller selected an immutable creation archive as input.
            with tempfile.TemporaryDirectory(prefix='canvas-push-') as directory:
                staged=Path(directory)/('source'+source_path.suffix)
                await registration_io(shutil.copy2,source_path,staged)
                from .asset_media import _digest
                if await registration_io(_digest,staged)!=(source_proof['sha256'],source_proof['size']):
                    raise ValueError('Canvas source changed during staging')
                yield CanvasPublication(staged,source_path,target,slot,store)
    finally:
        if store is not None:await store.close()
