import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act,fireEvent, render, screen, waitFor,within } from '@testing-library/react';
import { createDirectionOperation, readDirectionIntent } from '@/features/direction-references/directionOperation';
import { DirectionReferencePanel } from '@/features/direction-references/DirectionReferencePanel';
import * as api from '@/api/directions';
import { listCreationCanvases } from '@/api/creation';
import { getFreezoneCanvas } from '@/api/canvas';
import { captureFreezoneCanvasRuntime,currentFreezoneCanvasScope } from '@/features/freezone/canvasSyncRuntime';
import { DirectionReferenceEntry } from '@/features/direction-references/DirectionReferenceEntry';
import { ExtensionStyleDrawer } from '@/features/canvas/extension-styles/ExtensionStyleDrawer';
import { useAuthStore } from '@/stores/auth-store';
import { getSupertaleProject } from '@/api/projects';
const navigation=vi.hoisted(()=>({state:{location:{pathname:'/projects/p',search:{}}}}));
vi.mock('@tanstack/react-router',()=>({useRouter:()=>navigation,useRouterState:({select}:{select:(state:typeof navigation.state)=>unknown})=>select(navigation.state)}));
vi.mock('@/api/projects',async importOriginal=>({...await importOriginal<object>(),getSupertaleProject:vi.fn()}));
vi.mock('@/api/directions', () => ({listDirectionCatalog:vi.fn(),getDirectionBindings:vi.fn(),applyDirectionBindings:vi.fn(),getDirectionRequest:vi.fn()}));
vi.mock('@/api/creation', () => ({listCreationCanvases:vi.fn()}));
vi.mock('@/api/canvas', () => ({getFreezoneCanvas:vi.fn()}));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({captureFreezoneCanvasRuntime:vi.fn(()=>null),currentFreezoneCanvasScope:vi.fn(()=>null)}));
const owner={user:'alice',project:'p'};
const entry={kind:'style' as const,id:'custom',version:'1',title:'自定义水墨',available:true,proof:'catalog',configuration:{},metadata:{}};
const inspect=(scope:api.DirectionScope):api.DirectionState=>({scope,proof:'scope',chain:[scope],local:{style:{revision:0,state:'inherit',snapshot_id:null},technique:{revision:0,state:'inherit',snapshot_id:null}},effective:{style:{state:'unset',origin_scope:null,snapshot_ref:null,configuration:null},technique:{state:'unset',origin_scope:null,snapshot_ref:null,configuration:null}}});
const body:api.DirectionRequest={request_id:'one',kind:'style',action:'apply',scope:{level:'project'},expected_revision:0,scope_proof:'scope',selection:{id:'custom',version:'1',proof:'catalog'}};
const applied:api.DirectionResult={request_id:'one',operation_id:'op',status:'applied',kind:'style',snapshot_ref:null,bindings:[],effective:[]};
describe('fixed direction operations',()=>{
 beforeEach(()=>{vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue(null);vi.mocked(currentFreezoneCanvasScope).mockReturnValue(null);useAuthStore.setState({username:'alice'});vi.mocked(getSupertaleProject).mockResolvedValue({creation_mode:'agent_canvas_v1'} as never);navigation.state.location={pathname:'/projects/p',search:{}};});
 beforeEach(()=>{vi.clearAllMocks();localStorage.clear();vi.mocked(api.listDirectionCatalog).mockResolvedValue([entry]);vi.mocked(api.getDirectionBindings).mockImplementation(async(_,scope)=>inspect(scope));vi.mocked(listCreationCanvases).mockResolvedValue([{canvas_id:'c',title:'第一集',status:'applied',kind:'free',episode_id:null}]);vi.mocked(getFreezoneCanvas).mockResolvedValue({nodes:[{id:'a',type:'imageGenNode',data:{displayName:'小林'}},{id:'b',type:'imageGenNode',data:{displayName:'小雨'}},{id:'g',type:'groupNode',data:{label:'开场'}}]} as never);vi.mocked(api.applyDirectionBindings).mockResolvedValue(applied);vi.mocked(api.getDirectionRequest).mockResolvedValue({request_id:'one',status:'unknown'});});
 it('persists before write and restores the exact request after unknown result',async()=>{
  vi.mocked(api.applyDirectionBindings).mockImplementation(async()=>{expect(readDirectionIntent(owner)?.request).toEqual(body);throw new Error('network');});
  expect((await createDirectionOperation(owner,()=>true).run(body,'自定义水墨')).status).toBe('pending');
  const saved=readDirectionIntent(owner);expect(saved?.request.request_id).toBe('one');
  vi.mocked(api.applyDirectionBindings).mockResolvedValue(applied);
  expect((await createDirectionOperation(owner,()=>true).run()).status).toBe('applied');
  expect(api.applyDirectionBindings).toHaveBeenLastCalledWith('p',body);
 });
 it('does not treat an old pending request as a new selection',async()=>{
  vi.mocked(api.applyDirectionBindings).mockRejectedValue(new Error('network'));
  const op=createDirectionOperation(owner,()=>true);await op.run(body,'水墨');
  expect((await op.run({...body,request_id:'two'},'其他')).status).toBe('busy');
  expect(api.applyDirectionBindings).toHaveBeenCalledTimes(1);
 });
 it('never writes without durable storage',async()=>{
  const spy=vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('full');});
  expect((await createDirectionOperation(owner,()=>true).run(body,'水墨')).status).toBe('failed');
  expect(api.applyDirectionBindings).not.toHaveBeenCalled();spy.mockRestore();
 });
 it('browses read-only then explicitly applies with the selected catalog proof',async()=>{
  render(<DirectionReferencePanel owner={owner} kind="style" selectedId="custom" isCurrent={()=>true}/>);
  await screen.findByRole('option',{name:'自定义水墨 · v1'});expect(api.applyDirectionBindings).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'应用固定风格'}));
  await waitFor(()=>expect(api.applyDirectionBindings).toHaveBeenCalled());
  expect(vi.mocked(api.applyDirectionBindings).mock.calls[0][1]).toMatchObject({scope:{level:'project'},selection:{id:'custom',proof:'catalog'}});
 });
 it('uses one atomic request for explicit nodes and separates inherit from disable',async()=>{
  render(<DirectionReferencePanel owner={owner} kind="style" canvasId="c" nodeIds={['a','b']} isCurrent={()=>true}/>);
  await screen.findByText('小林、小雨');
  await waitFor(()=>expect(screen.getByRole('button',{name:'禁用本级风格'})).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button',{name:'禁用本级风格'}));
  await waitFor(()=>expect(api.applyDirectionBindings).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.applyDirectionBindings).mock.calls[0][1]).toMatchObject({action:'disable',targets:[{scope:{level:'node',canvas_id:'c',node_id:'a'}},{scope:{level:'node',canvas_id:'c',node_id:'b'}}]});
 });
 it.each(['canvas','group','nodes'] as const)('explicitly selects %s without guessing a workspace',async level=>{
  render(<DirectionReferencePanel owner={owner} kind="style" selectedId="custom" isCurrent={()=>true}/>);
  await screen.findByRole('option',{name:'自定义水墨 · v1'});
  fireEvent.change(screen.getByLabelText('影响范围'),{target:{value:level}});
  expect(screen.getByRole('button',{name:'应用固定风格'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('目标画布'),{target:{value:'c'}});
  if(level==='group'){await screen.findByRole('option',{name:'开场'});fireEvent.change(screen.getByLabelText('目标分组'),{target:{value:'g'}});}
  if(level==='nodes')fireEvent.click(await screen.findByLabelText('小林'));
  await waitFor(()=>expect(screen.getByRole('button',{name:'应用固定风格'})).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button',{name:'应用固定风格'}));
  await waitFor(()=>expect(api.applyDirectionBindings).toHaveBeenCalledTimes(1));
  const request=vi.mocked(api.applyDirectionBindings).mock.calls[0][1];
  expect(request.targets?.[0].scope??request.scope).toEqual(level==='canvas'?{level:'canvas',canvas_id:'c'}:level==='group'?{level:'group',canvas_id:'c',group_id:'g'}:{level:'node',canvas_id:'c',node_id:'a'});
 });
 it('unsets the project separately from disabling it',async()=>{
  render(<DirectionReferencePanel owner={owner} kind="style" isCurrent={()=>true}/>);
  await waitFor(()=>expect(screen.getByRole('button',{name:'清除项目配置'})).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button',{name:'清除项目配置'}));
  await waitFor(()=>expect(api.applyDirectionBindings).toHaveBeenCalled());
  expect(vi.mocked(api.applyDirectionBindings).mock.calls[0][1]).toMatchObject({action:'unset'});
  expect(vi.mocked(api.applyDirectionBindings).mock.calls[0][1].selection).toBeUndefined();
 });
 it.each(['catalog_stale','direction_conflict'])('keeps the selection on %s and only refreshes proof after explicit comparison',async code=>{
  vi.mocked(api.applyDirectionBindings).mockRejectedValue(Object.assign(new Error('请重新比较'),{body:{detail:{code}}}));
  render(<DirectionReferencePanel owner={owner} kind="style" selectedId="custom" isCurrent={()=>true}/>);
  await waitFor(()=>expect(screen.getByRole('button',{name:'应用固定风格'})).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button',{name:'应用固定风格'}));
  await screen.findByText('配置未保存，请重新比较');
  expect(screen.getByLabelText('固定风格')).toHaveValue('custom');
  const reads=vi.mocked(api.listDirectionCatalog).mock.calls.length;
  expect(api.applyDirectionBindings).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button',{name:'重新比较当前配置'}));
  await waitFor(()=>expect(api.listDirectionCatalog).toHaveBeenCalledTimes(reads+1));
 });
 it('drains active canvas before preview and flushes changes without replacing the original scope proof',async()=>{
  const flush=vi.fn().mockResolvedValue(true);
  vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({flush,isCurrent:()=>true,run:vi.fn()} as never);
  render(<DirectionReferencePanel owner={owner} kind="style" canvasId="c" selectedId="custom" isCurrent={()=>true}/>);
  await waitFor(()=>expect(screen.getByRole('button',{name:'应用固定风格'})).not.toBeDisabled());
  expect(flush).toHaveBeenCalled();
  const reads=vi.mocked(api.getDirectionBindings).mock.calls.length;
  fireEvent.click(screen.getByRole('button',{name:'应用固定风格'}));
  await waitFor(()=>expect(api.applyDirectionBindings).toHaveBeenCalled());
  expect(flush.mock.calls.length).toBeGreaterThan(1);
  expect(api.getDirectionBindings).toHaveBeenCalledTimes(reads);
  expect(vi.mocked(api.applyDirectionBindings).mock.calls[0][1].scope_proof).toBe('scope');
 });
 it('refuses a null or unsuccessful flush instead of submitting',async()=>{
  vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({flush:vi.fn().mockResolvedValue(null),isCurrent:()=>true,run:vi.fn()} as never);
  render(<DirectionReferencePanel owner={owner} kind="style" canvasId="c" isCurrent={()=>true}/>);
  await screen.findByRole('alert');expect(api.getDirectionBindings).not.toHaveBeenCalled();expect(api.applyDirectionBindings).not.toHaveBeenCalled();
 });
 it('does not publish after an A to B to A runtime replacement',async()=>{
  let current=true;
  vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({flush:vi.fn().mockResolvedValue(true),isCurrent:()=>current,run:vi.fn()} as never);
  render(<DirectionReferencePanel owner={owner} kind="style" canvasId="c" selectedId="custom" isCurrent={()=>true}/>);
  await waitFor(()=>expect(screen.getByRole('button',{name:'应用固定风格'})).not.toBeDisabled());current=false;
  fireEvent.click(screen.getByRole('button',{name:'应用固定风格'}));
  await screen.findByRole('alert');expect(api.applyDirectionBindings).not.toHaveBeenCalled();
 });
 it('late completion persists the original scope but cannot notify the new screen',async()=>{
  let finish!:(value:api.DirectionResult)=>void,current=true;const notify=vi.fn();
  vi.mocked(api.applyDirectionBindings).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  const ui=render(<DirectionReferencePanel owner={owner} kind="style" selectedId="custom" isCurrent={()=>current} onApplied={notify}/>);
  await waitFor(()=>expect(screen.getByRole('button',{name:'应用固定风格'})).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button',{name:'应用固定风格'}));await waitFor(()=>expect(api.applyDirectionBindings).toHaveBeenCalled());
  current=false;ui.unmount();finish(applied);await waitFor(()=>expect(readDirectionIntent(owner)?.phase).toBe('applied'));
  expect(notify).not.toHaveBeenCalled();expect(readDirectionIntent({user:'bob',project:'p'})).toBeNull();
 });
 it('reopens an unknown request and restores without needing the old node',async()=>{
  vi.mocked(api.applyDirectionBindings).mockRejectedValue(new Error('network'));
  await createDirectionOperation(owner,()=>true).run({...body,scope:{level:'node',canvas_id:'deleted',node_id:'gone'}},'小林 · 旧画布');
  vi.mocked(api.getDirectionRequest).mockResolvedValue(applied);vi.mocked(api.applyDirectionBindings).mockClear();
  render(<DirectionReferencePanel owner={owner} kind="style" isCurrent={()=>true}/>);
  fireEvent.click(await screen.findByRole('button',{name:'恢复原请求'}));
  await screen.findByText('已保存的提交结果');expect(api.applyDirectionBindings).not.toHaveBeenCalled();
 });
 it('canvas_busy stays retryable with the same identity',async()=>{
  vi.mocked(api.applyDirectionBindings).mockRejectedValue(Object.assign(new Error('busy'),{body:{detail:{code:'canvas_busy'}}}));
  expect((await createDirectionOperation(owner,()=>true).run(body,'水墨')).status).toBe('pending');
  expect(readDirectionIntent(owner)?.request).toEqual(body);
  vi.mocked(api.applyDirectionBindings).mockResolvedValue(applied);
  expect((await createDirectionOperation(owner,()=>true).run()).status).toBe('applied');
  expect(api.applyDirectionBindings).toHaveBeenLastCalledWith('p',body);
 });
 it('does not accept a conflicting server request receipt as this application',async()=>{
  vi.mocked(api.applyDirectionBindings).mockRejectedValue(Object.assign(new Error('collision'),{body:{detail:{code:'idempotency_conflict'}}}));
  vi.mocked(api.getDirectionRequest).mockResolvedValue(applied);
  expect((await createDirectionOperation(owner,()=>true).run(body,'水墨')).status).toBe('failed');
  expect(api.getDirectionRequest).not.toHaveBeenCalled();
 });
 it('a typed precommit catalog conflict permits explicit comparison even when the receipt endpoint is unavailable',async()=>{
  vi.mocked(api.applyDirectionBindings).mockRejectedValue(Object.assign(new Error('目录已变化'),{body:{detail:{code:'catalog_stale'}}}));
  vi.mocked(api.getDirectionRequest).mockRejectedValue(new Error('offline'));
  expect((await createDirectionOperation(owner,()=>true).run(body,'水墨')).status).toBe('failed');
  expect(readDirectionIntent(owner)?.phase).toBe('failed');
 });
 it('keeps the full extension drawer and delegates every apply entry to the same controlled footer',async()=>{
  const apply=vi.fn(),legacy=vi.fn();
  render(<ExtensionStyleDrawer open value={null} onChange={legacy} onOpenChange={()=>{}} renderApplication={id=><button onClick={()=>apply(id)}>配置固定风格</button>}/>);
  fireEvent.click(screen.getAllByRole('button',{name:/查看风格：/})[0]);
  const desktop=screen.getByTestId('extension-style-desktop-details');
  fireEvent.click(within(desktop).getByRole('button',{name:'配置固定风格'}));
  const mobile=screen.getByTestId('extension-style-mobile-actions');
  fireEvent.click(within(mobile).getByRole('button',{name:'配置固定风格'}));
  fireEvent.click(within(mobile).getByRole('button',{name:'查看详情'}));
  fireEvent.click(within(screen.getByTestId('extension-style-mobile-details')).getByRole('button',{name:'配置固定风格'}));
  expect(apply).toHaveBeenCalledTimes(3);expect(new Set(apply.mock.calls.map(c=>c[0])).size).toBe(1);expect(legacy).not.toHaveBeenCalled();
 });
 it('the real entry is absent in legacy mode',async()=>{
  vi.mocked(getSupertaleProject).mockResolvedValue({creation_mode:undefined} as never);
  render(<DirectionReferenceEntry project="p"/>);
  await waitFor(()=>expect(getSupertaleProject).toHaveBeenCalled());
  expect(screen.queryByRole('button',{name:'风格与手法配置'})).not.toBeInTheDocument();
  expect(api.listDirectionCatalog).not.toHaveBeenCalled();
 });
 it.each(['navigation','user','runtime'] as const)('real entry isolates a late applied response after %s change',async change=>{
  let finish!:(value:api.DirectionResult)=>void;let active=true;const onApplied=vi.fn();
  vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({flush:vi.fn().mockResolvedValue(true),isCurrent:()=>active,run:vi.fn()} as never);
  vi.mocked(api.applyDirectionBindings).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  render(<DirectionReferenceEntry project="p" canvasId="c" selectedId="custom" onApplied={onApplied}/>);
  fireEvent.click(await screen.findByRole('button',{name:'风格与手法配置'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'应用固定风格'})).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button',{name:'应用固定风格'}));await waitFor(()=>expect(api.applyDirectionBindings).toHaveBeenCalled());
  await act(async()=>{if(change==='user')useAuthStore.setState({username:'bob'});else if(change==='runtime')active=false;else navigation.state.location={pathname:'/projects/p',search:{}};finish(applied);});
  expect(onApplied).not.toHaveBeenCalled();expect(readDirectionIntent(owner)?.phase).toBe('applied');
  expect(readDirectionIntent({user:'bob',project:'p'})).toBeNull();
 });
 it('only shows the receipt effective state after saving rather than contradictory old inheritance',async()=>{
  const old=inspect({level:'project'});old.effective.style={state:'enabled',origin_scope:{level:'project'},title:'旧风格',version:'1',configuration:{},snapshot_ref:null};
  vi.mocked(api.getDirectionBindings).mockResolvedValue(old);
  const latest=inspect({level:'project'});latest.effective.style={...old.effective.style,title:'新风格'};
  vi.mocked(api.applyDirectionBindings).mockResolvedValue({...applied,effective:[latest]});
  render(<DirectionReferencePanel owner={owner} kind="style" selectedId="custom" isCurrent={()=>true}/>);
  await screen.findByText(/整个项目：旧风格/);fireEvent.click(screen.getByRole('button',{name:'应用固定风格'}));
  await screen.findByText(/此次保存结果.*新风格/);expect(screen.queryByText(/整个项目：旧风格/)).not.toBeInTheDocument();
 });
});
