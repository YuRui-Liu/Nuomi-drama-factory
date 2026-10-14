import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { inspectSharedAssetSource, listCreationAssets, type SharedAssetRequest, type SharedAssetRole, type SharedAssetSource } from '@/api/creation';
import type { CreationAsset } from '@/features/creation-workspace/contracts';
import { captureFreezoneCanvasRuntime } from '../canvasSyncRuntime';
import { saveOpenDirectorWorldScene } from '@/features/canvas/domain/directorWorldSceneSaveRegistry';
import { useAuthStore } from '@/stores/auth-store';
import { clearFinishedPublication, createSharedAssetPublication, readSharedPublication, type PublicationIntent, type PublicationScope } from './sharedAssetPublication';

const labels:Record<SharedAssetRole,string>={portrait:'角色肖像',identity:'身份形象',identity_portrait:'身份肖像',identity_costume:'身份服装',scene_master:'场景主视角',scene_reverse_master:'反打视角',scene_360:'场景全景',scene_director_pano_360:'导演台全景',scene_3gs_custom_scene:'完整 3D 场景',scene_3gs_master_ply:'主视角 3D',scene_3gs_reverse_ply:'反打 3D',scene_3gs_pano_ply:'全景 3D',scene_3gs_active_ply:'当前 3D',scene_spatial_layout:'空间布局'};
export function isSharedAssetTarget(target?:{kind?:string}|null) {
  return Boolean(target?.kind && (target.kind in labels || target.kind.startsWith('scene_')));
}
function availableRoles(source:SharedAssetSource|null,kind:'character'|'scene',variant:boolean):SharedAssetRole[] {
  if (!source) return [];
  if (kind==='character') return source.data.imageUrl ? variant?['identity','identity_portrait','identity_costume']:['portrait'] : [];
  const roles:SharedAssetRole[]=[];
  if(source.data.imageUrl)roles.push('scene_master','scene_reverse_master','scene_spatial_layout');
  if(source.data.panoUrl)roles.push('scene_director_pano_360','scene_360');
  if(source.data.plyUrl)roles.push('scene_3gs_custom_scene','scene_3gs_master_ply','scene_3gs_reverse_ply','scene_3gs_pano_ply');
  return roles;
}
export function SharedAssetSaveDialog({scope,initialTarget,defaultKind,defaultRole,onClose,onSuccess}: {
  scope:PublicationScope; initialTarget?:CreationAsset; defaultKind?:'character'|'scene'; defaultRole?:string;
  onClose:()=>void;onSuccess:()=>void;
}) {
  const [source,setSource]=useState<SharedAssetSource|null>(null);
  const [assets,setAssets]=useState<CreationAsset[]>([]);
  const [kind,setKind]=useState<'character'|'scene'>(initialTarget?.kind??defaultKind??'character');
  const [mode,setMode]=useState<SharedAssetRequest['mode']>('existing_version');
  const [selection,setSelection]=useState(initialTarget?JSON.stringify(initialTarget.selection):'');
  const [name,setName]=useState(''); const [role,setRole]=useState(defaultRole??'');
  const [busy,setBusy]=useState(true);const [error,setError]=useState('');const [message,setMessage]=useState('');
  const [pending,setPending]=useState<PublicationIntent|null>(null);
  const session=useMemo(()=>({live:false,runtime:null as ReturnType<typeof captureFreezoneCanvasRuntime>}),[scope.user,scope.project,scope.canvas,scope.node]);
  const flight=useRef(false);
  const current=()=>session.live&&Boolean(session.runtime?.isCurrent())&&useAuthStore.getState().username===scope.user;
  const controller=useRef<ReturnType<typeof createSharedAssetPublication>|null>(null);
  useEffect(()=>{
    let active=true;
    const mounted=()=>active&&current();
    session.live=true;session.runtime=captureFreezoneCanvasRuntime(scope.project,scope.canvas);
    setSource(null);setPending(null);setBusy(true);setError('');setMessage('');
    controller.current=createSharedAssetPublication(scope,mounted);
    void (async()=>{
      try {
        const stored=readSharedPublication(scope);
        if(stored) {setPending(stored);setMessage(stored.phase==='applied'?'此保存已完成，可关闭窗口':'检测到未完成的资产保存，请恢复原请求');return;}
        if(!mounted())throw new Error('画布或用户已变化，请返回原画布');
        await saveOpenDirectorWorldScene(scope.node);
        if(!mounted())return;
        if(await session.runtime!.flush()!==true)throw new Error('当前画布未保存成功，请先处理同步问题');
        if(!mounted())return;
        const [evidence,catalog]=await Promise.all([inspectSharedAssetSource(scope.project,scope.canvas,scope.node),listCreationAssets(scope.project)]);
        if(!mounted())return;
        setSource(evidence);
        // A drag carries the target as it was selected. Do not refresh its proof.
        setAssets(initialTarget?[initialTarget,...catalog.filter(a=>JSON.stringify(a.selection)!==JSON.stringify(initialTarget.selection))]:catalog);
        if(!initialTarget&&!defaultKind&&evidence.type==='threeDWorldNode')setKind('scene');
      } catch(e) {if(active&&session.live)setError(e instanceof Error?e.message:String(e));}
      finally {if(active&&session.live)setBusy(false);}
    })();
    return ()=>{active=false;session.live=false;session.runtime=null;};
  },[scope.user,scope.project,scope.canvas,scope.node]);
  const target=assets.find(a=>JSON.stringify(a.selection)===selection&&a.kind===kind);
  const variant=mode==='new_variant'||Boolean(target?.parent_source_name)&&mode==='existing_version';
  const roles=availableRoles(source,kind,variant);
  const chosen=roles.includes(role as SharedAssetRole)?role as SharedAssetRole:roles[0];
  const bundle=kind==='scene'&&source?.type==='threeDWorldNode'&&Array.isArray(source.data.sources)&&source.data.sources.length>0;
  const active=Array.isArray(source?.data.sources)?source.data.sources.find((s:Record<string,unknown>)=>s.id===source.data.activeSourceId):null;
  const bundleRoles=bundle?roles.filter(r=>active?.source_type==='pano360'?['scene_360','scene_director_pano_360'].includes(r):r.includes('3gs')):roles;
  const actualRole=bundleRoles.includes(chosen)?chosen:bundleRoles[0];
  const valid=Boolean(source&&actualRole&&(mode==='new_base'||target)&&(mode==='existing_version'||name.trim()));
  async function save(resume=false) {
    if(flight.current)return;flight.current=true;setBusy(true);setError('');
    try {
      let request:SharedAssetRequest|undefined;
      if(!resume) {
        if(!valid||!source)throw new Error('请完整选择来源、目标和保存方式');
        if(!current()||await session.runtime!.flush()!==true||!current())throw new Error('当前画布未保存成功或已切换');
        const latest=await inspectSharedAssetSource(scope.project,scope.canvas,scope.node);
        if(!current())return;
        if(latest.proof!==source.proof)throw new Error('来源在选择期间已变化，请关闭窗口后重新选择');
        request={request_id:crypto.randomUUID(),mode,kind,source:{canvas_id:scope.canvas,node_id:scope.node,proof:source.proof},role:actualRole,
          ...(mode!=='existing_version'?{name:name.trim()}:{}),...(mode!=='new_base'&&target?{selection:target.selection,proof:target.proof,...(target.asset_ref?{expected_ref:target.asset_ref}:{})}:{}),...(bundle?{scene_bundle:true}:{})};
      }
      const outcome=await controller.current!.run(request,request?{
        title:mode==='existing_version'?target!.title:name.trim(),
        parentTitle:mode==='new_variant'?target?.title:target?.parent_source_name??undefined,
        mode:mode==='new_base'?'新基础资产':mode==='new_variant'?'新变体':'已有资产的新版本',role:labels[actualRole],
      }:undefined);
      if(!current())return;
      setPending(readSharedPublication(scope));
      if(outcome.status==='applied') {setMessage('已保存共享资产。当前画布及其他引用保持原版本。');onSuccess();}
      else if(outcome.status==='failed')setError(outcome.message??'保存未完成');
      else setMessage(outcome.message??'保存尚未完成，请恢复原请求');
    } catch(e) {if(current())setError(e instanceof Error?e.message:String(e));}
    finally {flight.current=false;if(session.live)setBusy(false);}
  }
  const preview=source?.data.imageUrl??source?.data.panoUrl??source?.data.previewImageUrl;
  return createPortal(<div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4"><section role="dialog" aria-label="保存为共享资产" className="max-h-[90vh] w-full max-w-xl space-y-4 overflow-y-auto rounded-xl border border-white/10 bg-surface p-5 text-sm text-text shadow-2xl">
    <div className="flex items-center justify-between"><h2 className="font-semibold">保存为共享资产</h2><button aria-label="关闭保存窗口" onClick={onClose}>关闭</button></div>
    <p className="text-xs text-text-muted">保存到项目资产库；不会自动应用到画布、升级其他引用或开始生成。</p>
    {typeof preview==='string'&&<img src={preview} alt="当前来源预览" className="max-h-40 w-full rounded object-contain"/>}
    {error&&<p role="alert" className="text-red-400">{error}</p>}{message&&<p role="status">{message}</p>}
    {pending?<div className="space-y-3"><p>保存目标：{pending.display?.title??pending.request.name??'所选资产'}{pending.display?.parentTitle?` · ${pending.display.parentTitle}的变体`:''} · {pending.request.kind==='character'?'角色':'场景'}</p>
      {pending.display&&<p>{pending.display.mode} · {pending.display.role}</p>}
      {pending.result?.asset_ref&&<p>已保存版本 {pending.result.asset_ref.revision}</p>}
      {pending.phase!=='applied'&&pending.phase!=='failed'&&<button disabled={busy} onClick={()=>void save(true)} className="rounded border border-white/20 px-3 py-2">恢复原保存请求</button>}
      {['failed','applied'].includes(pending.phase)&&<button onClick={()=>{try{clearFinishedPublication(scope);onClose();}catch(e){setError(String(e));}}} className="rounded border border-white/20 px-3 py-2">{pending.phase==='failed'?'保留记录并重新选择':'完成'}</button>}
    </div>:<>
      <label className="block">资产类型<select aria-label="资产类型" value={kind} disabled={busy} onChange={e=>{setKind(e.target.value as 'character'|'scene');setSelection('');setRole('');}} className="ml-3 rounded bg-surface-raised p-2"><option value="character">角色</option><option value="scene">场景</option></select></label>
      <label className="block">保存方式<select aria-label="保存方式" value={mode} disabled={busy} onChange={e=>{setMode(e.target.value as SharedAssetRequest['mode']);setSelection('');setRole('');}} className="ml-3 rounded bg-surface-raised p-2"><option value="existing_version">已有资产的新版本</option><option value="new_base">新基础资产</option><option value="new_variant">新变体</option></select></label>
      {mode!=='new_base'&&<label className="block">{mode==='new_variant'?'所属基础资产':'目标资产'}<select aria-label="目标资产" value={selection} disabled={busy} onChange={e=>setSelection(e.target.value)} className="mt-1 w-full rounded border border-white/15 bg-surface p-2"><option value="">请选择资产</option>{assets.filter(a=>a.kind===kind&&(mode!=='new_variant'||!a.parent_source_name)).map(a=><option key={JSON.stringify(a.selection)} value={JSON.stringify(a.selection)}>{a.title} · {a.parent_source_name?`${a.parent_source_name}的变体`:'基础'} · {a.asset_ref?`版本 ${a.asset_ref.revision}`:'保存时注册'}</option>)}</select></label>}
      {mode!=='existing_version'&&<label className="block">新资产名称<input aria-label="新资产名称" value={name} onChange={e=>setName(e.target.value)} disabled={busy} className="mt-1 w-full rounded border border-white/15 bg-transparent p-2"/></label>}
      <label className="block">素材用途<select aria-label="素材用途" value={actualRole??''} disabled={busy} onChange={e=>setRole(e.target.value)} className="mt-1 w-full rounded border border-white/15 bg-surface p-2">{!bundleRoles.length&&<option value="">当前节点没有此类型的可保存素材</option>}{bundleRoles.map(r=><option key={r} value={r}>{labels[r]}</option>)}</select></label>
      {bundle&&<p className="text-xs text-text-muted">将同时保存全部场景来源、当前视角和各来源布景。</p>}
      <button disabled={busy||!valid} onClick={()=>void save()} className="w-full rounded bg-primary px-3 py-2 text-primary-foreground disabled:opacity-40">{busy?'正在准备…':'保存共享资产'}</button>
    </>}
  </section></div>,document.body);
}
