"""Freeze stage inputs and publish isolated synchronous worker results."""
from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import shutil
from uuid import uuid4

from novelvideo.director_world import stage_manifest
from novelvideo.production_workflow.coordination import asset_project_lock
from novelvideo.sqlite_store import SQLiteStore
from .asset_canvas_publication import _unique_asset
from .asset_media import controlled_path, _digest
from .asset_media_publication import scene_media_publication, scene_mutation_path
from .asset_rename import assert_asset_locators_writable, assert_no_pending_rename


@dataclass
class StagePreparation:
    root: Path
    scene: object
    locator: str
    frozen_root: Path
    params: dict
    expected: dict
    inputs: list


async def prepare(ctx, locator, step, params, check_cancel):
    from novelvideo import stage_asset_tasks as tasks
    root=Path(ctx.output_dir)
    source=SQLiteStore(ctx.owner_project_label,output_dir=str(root),state_dir=str(ctx.state_dir))
    params=deepcopy(params)
    try:
        async with asset_project_lock(ctx.state_dir):
            await check_cancel()
            from novelvideo.utils.safe_paths import validate_path_segment
            from novelvideo.models import NovelScene
            validate_path_segment(locator,label='scene name')
            records=[]
            if (Path(ctx.state_dir)/'data.db').is_file():
                assert_no_pending_rename(source,kind='scene',names={locator})
                assert_asset_locators_writable(source,'scene',{locator})
                records=await source.list_scenes()
            matched=[record for record in records if locator in {record.name,*record.aliases}]
            unbound_candidate=not matched and not params.get('update_manifest',True)
            scene=NovelScene(name=locator) if unbound_candidate else _unique_asset(records,locator)
            @asynccontextmanager
            async def preflight():
                if unbound_candidate:
                    yield None
                else:
                    async with scene_media_publication(root,ctx.state_dir,scene,publish=False) as frozen:
                        yield frozen
            async with preflight() as expected:
                frozen=controlled_path(root,root/'stage_candidates'/uuid4().hex)
                frozen.mkdir(parents=True)
                inputs=[]
                def freeze(value, *, optional=False):
                    if not value:return None
                    path=controlled_path(root,value)
                    proof=_digest(path) if path.is_file() else None
                    inputs.append((path,proof))
                    if proof is None:
                        if optional:return None
                        raise FileNotFoundError(str(path))
                    dest=controlled_path(frozen,frozen/path.relative_to(root))
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(path,dest)
                    if _digest(dest)!=proof:raise ValueError('Stage input changed while freezing')
                    return dest
                if step=='pano_sharp':
                    path=params.get('pano_path') or stage_manifest.resolve_pano_path(root,scene.name)
                    if not path:raise FileNotFoundError('Missing panorama')
                    params['pano_path']=str(freeze(path))
                elif step=='single_face_sharp':
                    resolver=tasks.compute_scene_reverse_master_path if params.get('source_kind')=='reverse' else tasks.compute_scene_master_path
                    path=params.get('image_path') or resolver(root,scene.name)
                    if not path:raise FileNotFoundError('Missing scene image')
                    params['image_path']=str(freeze(path))
                elif step=='splat_collision':
                    path=params.get('ply_path') or stage_manifest.resolve_ply_path(root,scene.name)
                    if not path:raise FileNotFoundError('Missing scene package')
                    params['ply_path']=str(freeze(path))
                elif step=='upload_scene_package':
                    params['src_asset']=str(freeze(params['src_asset']))
                elif step=='pano_from_master':
                    for key,resolver in [('master_path',tasks.compute_scene_master_path),('reverse_master_path',tasks.compute_scene_reverse_master_path)]:
                        path=params.get(key) or resolver(root,scene.name)
                        if path:
                            params[key]=str(freeze(path))
                            for relative in ['overlap_continuation_test/overlap_continuation_analysis.json','scene_spatial_contract/scene_spatial_contract.json']:
                                freeze(Path(path).parent/relative,optional=True)
                manifest=stage_manifest.load_manifest(root,scene.name) or {}
                frozen_manifest=stage_manifest.manifest_path(frozen,scene.name)
                frozen_manifest.parent.mkdir(parents=True,exist_ok=True)
                frozen_manifest.write_text(json.dumps(manifest,ensure_ascii=False))
                params.update(artifact_dir=str(frozen/'output'),update_manifest=False)
                return StagePreparation(root,scene,locator,frozen,params,expected,inputs)
    finally:
        await source.close()


async def publish(ctx, plan, step, result, check_cancel):
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.api.deps import make_static_url_for_context
    source=SQLiteStore(ctx.owner_project_label,output_dir=str(plan.root),state_dir=str(ctx.state_dir))
    try:
        async with asset_project_lock(ctx.state_dir):
            await check_cancel()
            assert_no_pending_rename(source,kind='scene',names={plan.locator})
            assert_asset_locators_writable(source,'scene',{plan.locator})
            current=_unique_asset(await source.list_scenes(),plan.locator)
            if current.name!=plan.scene.name:raise ValueError('Stage scene locator changed')
            for path,proof in plan.inputs:
                path=controlled_path(plan.root,path)
                if (_digest(path) if path.is_file() else None)!=proof:
                    raise ValueError('Stage input changed during generation')
            async with scene_media_publication(plan.root,ctx.state_dir,plan.scene,expected=plan.expected,
                    actor=str(ctx.requester_user_id or 'stage-asset')):
                await check_cancel()
                if step=='upload_scene_package':
                    prepared=controlled_path(plan.frozen_root,result['prepared_path'])
                    target_name=plan.params.get('target_name')
                    if target_name:
                        if Path(target_name).name!=target_name:raise ValueError('Invalid scene package target name')
                        target_name=str(Path(target_name).with_suffix(prepared.suffix))
                    result=tasks.upload_scene_package(plan.root,plan.scene.name,prepared,target_name=target_name)
                    result['output_path']=result['scene_path']
                else:
                    patch=deepcopy(result['manifest_patch'])
                    directory=scene_mutation_path(plan.root,plan.scene.name,stage_manifest.stage_dir(plan.root,plan.scene.name))
                    manifest=stage_manifest.manifest_path(plan.root,plan.scene.name)
                    scene_mutation_path(plan.root,plan.scene.name,manifest)
                    scene_mutation_path(plan.root,plan.scene.name,manifest.with_suffix(manifest.suffix+'.lock'))
                    copies={}
                    for field,value in patch.items():
                        if field.endswith('_path') and value:
                            if Path(value).name!=value:raise ValueError('Invalid generated manifest filename')
                            src=controlled_path(plan.frozen_root,Path(plan.params['artifact_dir'])/value)
                            if not src.is_file():raise FileNotFoundError(str(src))
                            dest=scene_mutation_path(plan.root,plan.scene.name,directory/value)
                            copies[src]=dest
                    directory.mkdir(parents=True,exist_ok=True)
                    for src,dest in copies.items():shutil.copy2(src,dest)
                    stage_manifest.update_manifest(plan.root,plan.scene.name,**patch)
                    for key,value in list(result.items()):
                        if isinstance(value,str) and Path(value) in copies:result[key]=str(copies[Path(value)])
                    result['manifest_updated']=True
                if step in {'pano_from_master','pano_from_text'}:
                    await source.clear_scene_stale_reference_kind(plan.scene.name,'pano')
                # Cancellation after media changed retains evidence instead of
                # returning a success while leaving semantic state incomplete.
                await check_cancel()
        result.pop('manifest_patch',None)
        path_text=result.get('ply_path') or result.get('scene_path') or result.get('collision_glb_path') or result.get('pano_path') or result.get('output_path')
        if path_text:
            path=Path(path_text)
            url=make_static_url_for_context(ctx,path.relative_to(plan.root).as_posix())
            result.update(output_url=url,url=url)
            for key in ('ply_url','splat_url','image_url'):
                if key in result:result[key]=url
        return result
    finally:
        await source.close()
