import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createSharedAssetPublication, readSharedPublication } from '@/features/freezone/commit/sharedAssetPublication';
import * as api from '@/api/creation';
import { ApiError } from '@/api/client';
vi.mock('@/api/creation', () => ({ publishSharedAsset:vi.fn(), getSharedPublication:vi.fn(), registerCreationAsset:vi.fn() }));
const scope = { user:'owner',project:'project',canvas:'canvas',node:'node' };
const ref = {base_asset_id:'base',variant_id:null,revision:1};
const body = {request_id:'save',mode:'existing_version' as const,kind:'character' as const,role:'portrait' as const,
  source:{canvas_id:'canvas',node_id:'node',proof:'source'},selection:{kind:'character' as const,source_name:'Lin',parent_source_name:''},proof:'target'};
const applied = {request_id:'save',status:'applied' as const,asset_ref:ref,media:[],media_selection:{slot_id:'portrait',role:'portrait'}};
beforeEach(() => {localStorage.clear();vi.resetAllMocks();});
describe('shared publication durable scope', () => {
  it('persists the original selection before registration and the exact body before POST', async () => {
    vi.mocked(api.registerCreationAsset).mockImplementation(async () => {
      expect(readSharedPublication(scope)?.request).toEqual(body); return ref;
    });
    vi.mocked(api.publishSharedAsset).mockImplementation(async (_,request) => {
      expect(readSharedPublication(scope)?.request).toEqual(request); return applied;
    });
    const controller = createSharedAssetPublication(scope, () => true);
    expect((await controller.run(body)).status).toBe('applied');
    expect(api.publishSharedAsset).toHaveBeenCalledWith('project',{...body,expected_ref:ref});
  });
  it('unknown response survives reopening and retries the exact request', async () => {
    vi.mocked(api.publishSharedAsset).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(applied);
    vi.mocked(api.getSharedPublication).mockRejectedValue(new Error('offline'));
    const request = {...body,expected_ref:ref};
    expect((await createSharedAssetPublication(scope,()=>true).run(request)).status).toBe('pending');
    expect((await createSharedAssetPublication(scope,()=>true).run()).status).toBe('applied');
    expect(vi.mocked(api.publishSharedAsset).mock.calls.map(call => call[1])).toEqual([request,request]);
  });
  it('does not reinterpret a different request as the pending request success', async () => {
    vi.mocked(api.publishSharedAsset).mockRejectedValue(new Error('offline'));
    vi.mocked(api.getSharedPublication).mockRejectedValue(new Error('offline'));
    const controller = createSharedAssetPublication(scope,()=>true);
    await controller.run({...body,expected_ref:ref});
    expect((await controller.run({...body,request_id:'other',expected_ref:ref})).status).toBe('busy');
    expect(api.publishSharedAsset).toHaveBeenCalledTimes(1);
  });
  it('late result is stored for its scope without applying to a new runtime', async () => {
    let current=true;
    vi.mocked(api.publishSharedAsset).mockImplementation(async () => {current=false;return applied;});
    expect((await createSharedAssetPublication(scope,()=>current).run({...body,expected_ref:ref})).status).toBe('detached');
    expect(readSharedPublication(scope)?.result).toEqual(applied);
    expect(readSharedPublication({...scope,canvas:'other'})).toBeNull();
  });
  it('storage failure prevents registration and publication', async () => {
    const spy=vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('full');});
    expect((await createSharedAssetPublication(scope,()=>true).run(body)).status).toBe('failed');
    expect(api.registerCreationAsset).not.toHaveBeenCalled();expect(api.publishSharedAsset).not.toHaveBeenCalled();spy.mockRestore();
  });
  it('lost registration response reopens with the original proof and request identity',async()=>{
    vi.mocked(api.registerCreationAsset).mockRejectedValueOnce(new Error('lost register response')).mockResolvedValue(ref);
    vi.mocked(api.publishSharedAsset).mockResolvedValue(applied);
    const display={title:'雨夜外套',parentTitle:'Lin',mode:'新变体',role:'身份形象'};
    expect((await createSharedAssetPublication(scope,()=>true).run(body,display)).status).toBe('pending');
    expect(readSharedPublication(scope)?.display).toEqual(display);
    expect((await createSharedAssetPublication(scope,()=>true).run()).status).toBe('applied');
    expect(vi.mocked(api.registerCreationAsset).mock.calls.map(c=>c[1])).toEqual([{selection:body.selection,proof:'target'},{selection:body.selection,proof:'target'}]);
    expect(api.publishSharedAsset).toHaveBeenCalledWith('project',{...body,expected_ref:ref});
  });
  it('a definite target conflict is reselectable only when server confirms no publication journal',async()=>{
    vi.mocked(api.publishSharedAsset).mockRejectedValue(new ApiError('目标已变化',409));
    vi.mocked(api.getSharedPublication).mockRejectedValue(new ApiError('missing',409,{detail:{code:'asset_publication_missing'}}));
    expect((await createSharedAssetPublication(scope,()=>true).run({...body,expected_ref:ref})).status).toBe('failed');
    expect(readSharedPublication(scope)?.phase).toBe('failed');
  });
  it('a 409 with an existing journal stays pending instead of allowing a new identity',async()=>{
    vi.mocked(api.publishSharedAsset).mockRejectedValue(new ApiError('媒体等待对账',409));
    vi.mocked(api.getSharedPublication).mockResolvedValue({request_id:'save',status:'reconciliation_required'});
    expect((await createSharedAssetPublication(scope,()=>true).run({...body,expected_ref:ref})).status).toBe('pending');
    expect(readSharedPublication(scope)?.phase).toBe('publishing');
  });
  it('serializes duplicate clicks and leaves a closed registration ready for original-scope recovery',async()=>{
    let resolve!:(value:typeof ref)=>void;let current=true;
    vi.mocked(api.registerCreationAsset).mockImplementation(()=>new Promise(done=>{resolve=done;}));
    const controller=createSharedAssetPublication(scope,()=>current);
    const running=controller.run(body);
    expect((await createSharedAssetPublication(scope,()=>true).run(body)).status).toBe('busy');
    current=false;resolve(ref);
    expect((await running).status).toBe('detached');expect(api.publishSharedAsset).not.toHaveBeenCalled();
    expect(readSharedPublication(scope)?.request.expected_ref).toEqual(ref);
  });
});
