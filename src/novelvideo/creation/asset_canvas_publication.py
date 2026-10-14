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


@asynccontextmanager
async def canvas_asset_publication(ctx,root,slot,source_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.utils.path_resolver import canonical_identity_path, canonical_identity_costume_path, canonical_identity_portrait_path
    from novelvideo.director_world import stage_manifest
    root=Path(root);slot=deepcopy(slot)
    requested_locator=getattr(slot,'character',None) or getattr(slot,'scene_id',None)
    source_path=controlled_path(root,source_path)
    source_proof=capture_blob(root,source_path,archive=False)
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
        target_proof=capture_blob(root,target,archive=False) if target.is_file() else None

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
            if capture_blob(root,source_path,archive=False)!=source_proof:
                raise ValueError('Canvas source changed before publication')
            current=capture_blob(root,target,archive=False) if target.is_file() else None
            if current!=target_proof:raise ValueError('Canvas target changed before publication')
            if scene is not None:
                scene_mutation_path(root,scene.name,target)
                manifest=stage_manifest.manifest_path(root,scene.name)
                scene_mutation_path(root,scene.name,manifest)
                scene_mutation_path(root,scene.name,manifest.with_suffix(manifest.suffix+'.lock'))

        async with boundary(publish=False) as frozen:
            await validate()
        async with boundary(publish=True,expected=frozen):
            await validate()
            # The synchronous copy uses an isolated, verified source, including
            # when the caller selected an immutable creation archive as input.
            with tempfile.TemporaryDirectory(prefix='canvas-push-') as directory:
                staged=Path(directory)/('source'+source_path.suffix)
                shutil.copy2(source_path,staged)
                from .asset_media import _digest
                if _digest(staged)!=(source_proof['sha256'],source_proof['size']):
                    raise ValueError('Canvas source changed during staging')
                yield CanvasPublication(staged,source_path,target,slot,store)
    finally:
        if store is not None:await store.close()
