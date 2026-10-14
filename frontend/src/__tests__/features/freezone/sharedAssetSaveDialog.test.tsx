import { beforeEach, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SharedAssetSaveDialog } from '@/features/freezone/commit/SharedAssetSaveDialog';
import * as api from '@/api/creation';
import { createSharedAssetPublication } from '@/features/freezone/commit/sharedAssetPublication';
vi.mock('@/api/creation',()=>({inspectSharedAssetSource:vi.fn(),listCreationAssets:vi.fn(),registerCreationAsset:vi.fn(),publishSharedAsset:vi.fn(),getSharedPublication:vi.fn()}));
const mocks=vi.hoisted(()=>({flush:vi.fn(),save:vi.fn(),current:vi.fn(()=>true)}));
vi.mock('@/features/freezone/canvasSyncRuntime',()=>({captureFreezoneCanvasRuntime:()=>({isCurrent:mocks.current,flush:mocks.flush})}));
vi.mock('@/features/canvas/domain/directorWorldSceneSaveRegistry',()=>({saveOpenDirectorWorldScene:mocks.save}));
vi.mock('@/stores/auth-store',()=>({useAuthStore:{getState:()=>({username:'owner'})}}));
const scope={user:'owner',project:'p',canvas:'c',node:'n'};
const target={kind:'character' as const,source_name:'Lin',parent_source_name:'',selection:{kind:'character' as const,source_name:'Lin',parent_source_name:''},proof:'target',title:'Lin',asset_ref:null,snapshot:{},media:[]};
beforeEach(()=>{
  localStorage.clear();vi.clearAllMocks();mocks.flush.mockResolvedValue(true);mocks.current.mockReturnValue(true);
  vi.mocked(api.inspectSharedAssetSource).mockResolvedValue({canvas_id:'c',node_id:'n',type:'uploadNode',data:{imageUrl:'/image.png'},proof:'source',files:{}});
  vi.mocked(api.listCreationAssets).mockResolvedValue([target]);
  vi.mocked(api.registerCreationAsset).mockResolvedValue({base_asset_id:'b',variant_id:null,revision:1});
  vi.mocked(api.publishSharedAsset).mockResolvedValue({request_id:'s',status:'applied',asset_ref:{base_asset_id:'b',revision:2}});
});
it('browsing is read-only; explicit save registers the selected original proof then publishes without bind',async()=>{
  const success=vi.fn();render(<SharedAssetSaveDialog scope={scope} onClose={()=>{}} onSuccess={success}/>);
  await screen.findByText('Lin · 基础 · 保存时注册');
  expect(api.registerCreationAsset).not.toHaveBeenCalled();expect(api.publishSharedAsset).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('目标资产'),{target:{value:JSON.stringify(target.selection)}});
  fireEvent.click(screen.getByRole('button',{name:'保存共享资产'}));
  await waitFor(()=>expect(success).toHaveBeenCalled());
  expect(api.registerCreationAsset).toHaveBeenCalledWith('p',{selection:target.selection,proof:'target'});
  expect(api.publishSharedAsset).toHaveBeenCalledWith('p',expect.objectContaining({mode:'existing_version',expected_ref:{base_asset_id:'b',variant_id:null,revision:1}}));
});
it.each(['new_base','new_variant'])('supports explicit %s without changing the canvas',async mode=>{
  render(<SharedAssetSaveDialog scope={scope} onClose={()=>{}} onSuccess={()=>{}}/>);
  await screen.findByText('Lin · 基础 · 保存时注册');
  fireEvent.change(screen.getByLabelText('保存方式'),{target:{value:mode}});
  if(mode==='new_variant')fireEvent.change(screen.getByLabelText('目标资产'),{target:{value:JSON.stringify(target.selection)}});
  fireEvent.change(screen.getByLabelText('新资产名称'),{target:{value:'Rain'}});
  fireEvent.click(screen.getByRole('button',{name:'保存共享资产'}));
  await waitFor(()=>expect(api.publishSharedAsset).toHaveBeenCalledWith('p',expect.objectContaining({mode,name:'Rain',role:mode==='new_base'?'portrait':'identity'})));
});
it('failed flush never inspects or publishes',async()=>{
  mocks.flush.mockResolvedValue(false);render(<SharedAssetSaveDialog scope={scope} onClose={()=>{}} onSuccess={()=>{}}/>);
  await screen.findByRole('alert');expect(api.inspectSharedAssetSource).not.toHaveBeenCalled();expect(api.publishSharedAsset).not.toHaveBeenCalled();
});
it('source changed while choosing is rejected before writing',async()=>{
  render(<SharedAssetSaveDialog scope={scope} initialTarget={target} onClose={()=>{}} onSuccess={()=>{}}/>);
  await screen.findByText('Lin · 基础 · 保存时注册');
  vi.mocked(api.inspectSharedAssetSource).mockResolvedValue({canvas_id:'c',node_id:'n',type:'uploadNode',data:{imageUrl:'/new.png'},proof:'new',files:{}});
  fireEvent.click(screen.getByRole('button',{name:'保存共享资产'}));
  await screen.findByRole('alert');expect(api.registerCreationAsset).not.toHaveBeenCalled();expect(api.publishSharedAsset).not.toHaveBeenCalled();
});
it('late preparation from a previous canvas instance cannot replace the current preview',async()=>{
  let release!:(value:api.SharedAssetSource)=>void;
  vi.mocked(api.inspectSharedAssetSource).mockImplementationOnce(()=>new Promise(resolve=>{release=resolve;}));
  const view=render(<SharedAssetSaveDialog scope={scope} onClose={()=>{}} onSuccess={()=>{}}/>);
  await waitFor(()=>expect(api.inspectSharedAssetSource).toHaveBeenCalledTimes(1));
  view.rerender(<SharedAssetSaveDialog scope={{...scope,canvas:'other'}} onClose={()=>{}} onSuccess={()=>{}}/>);
  await screen.findByText('Lin · 基础 · 保存时注册');
  await act(async()=>release({canvas_id:'c',node_id:'n',type:'uploadNode',data:{imageUrl:'/stale.png'},proof:'stale',files:{}}));
  await waitFor(()=>expect(screen.getByAltText('当前来源预览')).toHaveAttribute('src','/image.png'));
});
it('saves the entire frozen multi-source scene bundle in one request',async()=>{
  vi.mocked(api.inspectSharedAssetSource).mockResolvedValue({canvas_id:'c',node_id:'n',type:'threeDWorldNode',proof:'bundle',files:{},data:{plyUrl:'/model.sog',panoUrl:'/pano.png',sources:[{id:'one',source_type:'sog',ply_url:'/one.sog'},{id:'two',source_type:'sog',ply_url:'/model.sog'}],activeSourceId:'two',scenesBySourceId:{one:{camera:{x:1}},two:{camera:{x:2}}}}});
  render(<SharedAssetSaveDialog scope={scope} defaultKind="scene" onClose={()=>{}} onSuccess={()=>{}}/>);
  await screen.findByText('将同时保存全部场景来源、当前视角和各来源布景。');
  fireEvent.change(screen.getByLabelText('保存方式'),{target:{value:'new_base'}});
  fireEvent.change(screen.getByLabelText('新资产名称'),{target:{value:'大厅'}});
  fireEvent.click(screen.getByRole('button',{name:'保存共享资产'}));
  await waitFor(()=>expect(api.publishSharedAsset).toHaveBeenCalledWith('p',expect.objectContaining({kind:'scene',scene_bundle:true,role:'scene_3gs_custom_scene',source:{canvas_id:'c',node_id:'n',proof:'bundle'}})));
  expect(api.publishSharedAsset).toHaveBeenCalledTimes(1);
});
it('restores readable target and revision even when source node is no longer present',async()=>{
  vi.mocked(api.publishSharedAsset).mockRejectedValueOnce(new Error('unknown'));
  vi.mocked(api.getSharedPublication).mockRejectedValue(new Error('offline'));
  await createSharedAssetPublication(scope,()=>true).run({request_id:'saved',mode:'existing_version',kind:'character',role:'identity_portrait',source:{canvas_id:'c',node_id:'n',proof:'frozen'},selection:{kind:'character',source_name:'internal-uuid',parent_source_name:'Lin'},proof:'target',expected_ref:{base_asset_id:'base',variant_id:'variant',revision:2}},
    {title:'雨夜外套',parentTitle:'Lin',mode:'已有资产的新版本',role:'身份肖像'});
  vi.mocked(api.inspectSharedAssetSource).mockRejectedValue(new Error('node missing'));
  vi.mocked(api.publishSharedAsset).mockResolvedValue({request_id:'saved',status:'applied',asset_ref:{base_asset_id:'base',variant_id:'variant',revision:3}});
  render(<SharedAssetSaveDialog scope={scope} onClose={()=>{}} onSuccess={()=>{}}/>);
  await screen.findByText('保存目标：雨夜外套 · Lin的变体 · 角色');
  fireEvent.click(screen.getByRole('button',{name:'恢复原保存请求'}));
  await screen.findByText('已保存版本 3');
  expect(api.inspectSharedAssetSource).not.toHaveBeenCalled();expect(screen.queryByText(/internal-uuid/)).toBeNull();
});
it('disables save for incompatible source media and missing targets',async()=>{
  vi.mocked(api.inspectSharedAssetSource).mockResolvedValue({canvas_id:'c',node_id:'n',type:'uploadNode',proof:'missing',files:{},data:{}});
  render(<SharedAssetSaveDialog scope={scope} onClose={()=>{}} onSuccess={()=>{}}/>);
  await screen.findByText('Lin · 基础 · 保存时注册');
  expect(screen.getByRole('button',{name:'保存共享资产'})).toBeDisabled();
  expect(api.publishSharedAsset).not.toHaveBeenCalled();
});
