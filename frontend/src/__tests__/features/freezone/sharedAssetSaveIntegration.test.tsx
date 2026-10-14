import { act,fireEvent,render,screen,waitFor } from '@testing-library/react';
import { beforeEach,expect,it,vi } from 'vitest';
import { QueryClient,QueryClientProvider } from '@tanstack/react-query';
import { FreezoneShell } from '@/features/freezone/FreezoneShell';
import { FixedAssetLibrary } from '@/features/creation-workspace/FixedAssetLibrary';
import { useAssetDropStore } from '@/stores/assetDropStore';
import { useCanvasStore } from '@/stores/canvasStore';
import { useAuthStore } from '@/stores/auth-store';
import { canvasEventBus } from '@/features/canvas/application/canvasServices';
import * as api from '@/api/creation';
import { getFreezoneCanvas,putFreezoneCanvas } from '@/api/canvas';
import { promoteToAsset } from '@/features/freezone/commit/promoteToAsset';
vi.mock('@/api/creation',async original=>({...await original<typeof import('@/api/creation')>(),inspectSharedAssetSource:vi.fn(),listCreationAssets:vi.fn(),listCreationCanvases:vi.fn(),publishSharedAsset:vi.fn(),registerCreationAsset:vi.fn(),getSharedPublication:vi.fn()}));
vi.mock('@/api/canvas',async original=>({...await original<typeof import('@/api/canvas')>(),getFreezoneCanvas:vi.fn(),putFreezoneCanvas:vi.fn()}));
vi.mock('@xyflow/react',async original=>({...await original<typeof import('@xyflow/react')>(),useReactFlow:()=>({setViewport:vi.fn()})}));
vi.mock('@/features/canvas/Canvas',()=>({Canvas:()=> <div>测试画布</div>}));
vi.mock('@/features/canvas/ui/CanvasRunPanel',()=>({CanvasRunPanel:()=>null}));
vi.mock('@/features/freezone/AssetLibraryPanel',()=>({AssetLibraryPanel:()=>null}));
vi.mock('@/features/freezone/commit/promoteToAsset',()=>({promoteToAsset:vi.fn()}));
vi.mock('@/features/canvas/hooks/useFreezoneImageModels',()=>({prefetchFreezoneImageModels:vi.fn()}));
vi.mock('@/features/canvas/hooks/useFreezoneVideoModels',()=>({prefetchFreezoneVideoModels:vi.fn()}));
vi.mock('@/features/canvas/hooks/useFreezoneCameraOptions',()=>({prefetchFreezoneCameraOptions:vi.fn()}));
vi.mock('@/features/canvas/hooks/useFreezoneStyleTemplates',()=>({prefetchFreezoneStyleTemplates:vi.fn()}));
vi.mock('@/features/canvas/hooks/useFreezoneVideoCameraTemplates',()=>({prefetchFreezoneVideoCameraTemplates:vi.fn()}));
const target={kind:'character' as const,source_name:'Lin',parent_source_name:'',selection:{kind:'character' as const,source_name:'Lin',parent_source_name:''},proof:'frozen target',title:'Lin',asset_ref:{base_asset_id:'base',revision:1},snapshot:{},media:[]};
const node={id:'n',type:'uploadNode',position:{x:0,y:0},data:{imageUrl:'/image.png',slot_target:{kind:'portrait',character:'Lin'}}};
beforeEach(()=>{
  target.proof='frozen target';
  vi.clearAllMocks();localStorage.clear();useAuthStore.setState({username:'owner'});
  useCanvasStore.getState().setCanvasData([],[]);useAssetDropStore.setState({activeDrag:null,pendingReplace:null,hoverAssetId:null});
  vi.mocked(getFreezoneCanvas).mockResolvedValue({nodes:[structuredClone(node)],edges:[],revision:1} as never);
  vi.mocked(putFreezoneCanvas).mockResolvedValue({saved:true,revision:2});
  vi.mocked(api.inspectSharedAssetSource).mockResolvedValue({canvas_id:'c',node_id:'n',type:'uploadNode',data:{imageUrl:'/image.png'},proof:'source',files:{}});
  vi.mocked(api.listCreationAssets).mockResolvedValue([target]);vi.mocked(api.listCreationCanvases).mockResolvedValue([]);
  vi.mocked(api.publishSharedAsset).mockResolvedValue({request_id:'s',status:'applied',asset_ref:{base_asset_id:'base',revision:2}});
});
function shell(){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><FreezoneShell managedWorkspace project={{id:'p'} as never} canvasId="c"/></QueryClientProvider>);}
it('real Shell/runtime suppresses automatic shared writes and opens explicit save with flushed source',async()=>{
  shell();await waitFor(()=>expect(useCanvasStore.getState().nodes.length).toBe(1));
  act(()=>canvasEventBus.publish('freezone/commit-node',{nodeId:'n',auto:true}));
  expect(api.publishSharedAsset).not.toHaveBeenCalled();expect(promoteToAsset).not.toHaveBeenCalled();expect(screen.queryByRole('dialog')).toBeNull();
  act(()=>canvasEventBus.publish('freezone/commit-node',{nodeId:'n'}));
  await screen.findByText('Lin · 基础 · 版本 1');
  fireEvent.change(screen.getByLabelText('目标资产'),{target:{value:JSON.stringify(target.selection)}});
  fireEvent.click(screen.getByRole('button',{name:'保存共享资产'}));
  await waitFor(()=>expect(api.publishSharedAsset).toHaveBeenCalledTimes(1));
  expect(promoteToAsset).not.toHaveBeenCalled();
  expect(useCanvasStore.getState().nodes[0].data).not.toHaveProperty('assetRef');
});
it('generic selected background keeps its existing automatic path',async()=>{
  vi.mocked(getFreezoneCanvas).mockResolvedValue({nodes:[{...node,data:{imageUrl:'/image.png',slot_target:{kind:'selected_background',episode:1,beat:1}}}],edges:[],revision:1} as never);
  vi.mocked(promoteToAsset).mockResolvedValue({target_path:'background.png',target_url:'/background.png',backup:null});
  shell();await waitFor(()=>expect(useCanvasStore.getState().nodes.length).toBe(1));
  act(()=>useCanvasStore.getState().updateNodeData('n',{slot_target:{kind:'selected_background',episode:1,beat:1}}));
  act(()=>canvasEventBus.publish('freezone/commit-node',{nodeId:'n',auto:true}));
  await waitFor(()=>expect(promoteToAsset).toHaveBeenCalledTimes(1));expect(api.publishSharedAsset).not.toHaveBeenCalled();
});
it.each([false,true])('card drop freezes source scope and target proof; stale runtime=%s',async stale=>{
  useCanvasStore.getState().setCanvasData([node] as never,[]);
  const save=vi.fn();render(<FixedAssetLibrary project="p" canvasId="c" kind="character" onSaveFromNode={save}/>);
  await screen.findByText('Lin');
  act(()=>{
    useAssetDropStore.getState().beginDrag({nodeId:'n',mediaType:'image',sourceUrl:'/image.png',thumbUrl:'/image.png',label:'图像',directorControlBundle:null,
      scope:{user:'owner',project:'p',canvas:'c',isCurrent:()=>!stale,data:JSON.stringify(useCanvasStore.getState().nodes[0].data)}});
    useAssetDropStore.getState().setHoverAsset('shared:Lin',target);
    target.proof='changed after hover';
    useAssetDropStore.getState().endDrag(true);
  });
  if(stale){await screen.findByRole('alert');expect(save).not.toHaveBeenCalled();}
  else await waitFor(()=>expect(save).toHaveBeenCalledWith('n',expect.objectContaining({proof:'frozen target'})));
  expect(api.registerCreationAsset).not.toHaveBeenCalled();expect(api.publishSharedAsset).not.toHaveBeenCalled();
  target.proof='frozen target';
});
