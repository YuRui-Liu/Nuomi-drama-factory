import { useEffect,useRef,useState } from 'react';
import { Button } from '@/components/ui/button';
import { listCreationCanvases } from '@/api/creation';
import { getFreezoneCanvas } from '@/api/canvas';
import { listDirectionCatalog,getDirectionBindings,type DirectionKind,type DirectionEntry,type DirectionScope,type DirectionState,type DirectionRequest } from '@/api/directions';
import type { CreationCanvas } from '@/features/creation-workspace/contracts';
import { captureFreezoneCanvasRuntime,currentFreezoneCanvasScope } from '@/features/freezone/canvasSyncRuntime';
import { createDirectionOperation,readDirectionIntent,finishDirectionIntent,type DirectionOwner,type DirectionIntent } from './directionOperation';

interface NodeOption {id:string;type?:string;data?:Record<string,unknown>}
export interface DirectionPanelProps {owner:DirectionOwner;kind:DirectionKind;selectedId?:string;canvasId?:string;nodeIds?:string[];isCurrent:()=>boolean;onApplied?:()=>void}
const control='rounded border border-border bg-background px-2 py-1 text-sm';
export function DirectionReferencePanel(props:DirectionPanelProps){
 const [frozen]=useState(()=>({...props,nodeIds:props.nodeIds?[...props.nodeIds]:[]}));
 const {owner,kind}=frozen;const title=kind==='style'?'风格':'手法';
 const alive=useRef(true);useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 const current=()=>alive.current&&frozen.isCurrent();
 const [catalog,setCatalog]=useState<DirectionEntry[]>([]),[selected,setSelected]=useState(frozen.selectedId??'');
 const [canvases,setCanvases]=useState<CreationCanvas[]>([]),[canvas,setCanvas]=useState(frozen.canvasId??'');
 const [nodes,setNodes]=useState<NodeOption[]>([]),[nodeIds,setNodeIds]=useState(frozen.nodeIds),[group,setGroup]=useState('');
 const [level,setLevel]=useState<'project'|'canvas'|'group'|'nodes'>(frozen.nodeIds.length?'nodes':frozen.canvasId?'canvas':'project');
 const [states,setStates]=useState<DirectionState[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[compare,setCompare]=useState(0);
 const [intent,setIntent]=useState<DirectionIntent|null>(null),[message,setMessage]=useState('');
 const runtime=useRef<ReturnType<typeof captureFreezoneCanvasRuntime>>(null);
 const [initialRuntime]=useState(()=>frozen.canvasId?captureFreezoneCanvasRuntime(owner.project,frozen.canvasId):null);
 const scopes:DirectionScope[]=level==='project'?[{level:'project'}]:!canvas?[]:level==='canvas'?[{level:'canvas',canvas_id:canvas}]:level==='group'?(group?[{level:'group',canvas_id:canvas,group_id:group}]:[]):nodeIds.map(node_id=>({level:'node',canvas_id:canvas,node_id}));
 const scopeKey=JSON.stringify(scopes);
 const ready=states.length>0&&JSON.stringify(states.map(s=>s.scope))===scopeKey;
 const nodeName=(id:string)=>{const node=nodes.find(n=>n.id===id);return String(node?.data?.displayName??node?.data?.label??node?.data?.title??(node?.type==='groupNode'?'分组':`节点 ${nodes.findIndex(n=>n.id===id)+1}`));};
 const scopeName=(scope:DirectionScope):string=>scope.level==='project'?'整个项目':`${canvases.find(c=>c.canvas_id===scope.canvas_id)?.title??'所选画布'}${scope.level==='group'?` / 分组 ${nodeName(scope.group_id)}`:scope.level==='node'?` / ${nodeName(scope.node_id)}`:''}`;
 useEffect(()=>{try{setIntent(readDirectionIntent(owner));}catch(e){setError(String(e));}},[]);
 useEffect(()=>{let live=true;void Promise.all([listDirectionCatalog(owner.project,kind),listCreationCanvases(owner.project)]).then(([entries,items])=>{if(!live||!current())return;setCatalog(entries);setCanvases(items.filter(c=>c.status==='applied'));}).catch(e=>{if(live&&current())setError(String(e));});return()=>{live=false;};},[compare]);
 useEffect(()=>{let live=true;setNodes([]);if(!canvas)return;void getFreezoneCanvas(owner.project,canvas).then(value=>{if(live&&current())setNodes(value.nodes as NodeOption[]);}).catch(e=>{if(live&&current())setError(String(e));});return()=>{live=false;};},[canvas,compare]);
 useEffect(()=>{let live=true;setStates([]);setError('');if(intent||!scopes.length)return;
  void(async()=>{
   if(initialRuntime&&!initialRuntime.isCurrent())throw new Error('原画布已关闭，请重新打开配置');
   const active=currentFreezoneCanvasScope();runtime.current=canvas?captureFreezoneCanvasRuntime(owner.project,canvas):null;
   if(canvas&&active?.project===owner.project&&active.canvasId===canvas&&!runtime.current)throw new Error('画布同步尚未准备好');
   if(runtime.current&&await runtime.current.flush()!==true)throw new Error('画布保存未完成，未读取配置证据');
   if(!live||!current()||runtime.current&&!runtime.current.isCurrent())return;
   if(level!=='project'&&!canvases.some(c=>c.canvas_id===canvas))return;
   const result=await Promise.all(scopes.map(scope=>getDirectionBindings(owner.project,scope)));
   if(live&&current())setStates(result);
  })().catch(e=>{if(live&&current())setError(String(e));});return()=>{live=false;};
 },[scopeKey,compare,JSON.stringify(canvases.map(c=>c.canvas_id)),intent?.request.request_id]);
 const entry=catalog.find(e=>e.id===selected);
 const rules=(entry?.metadata.card as {applicability?:{modes:string[];min_duration_seconds:number;max_duration_seconds:number}}|undefined)?.applicability;
 const act=async(action:DirectionRequest['action'],restore=false)=>{
  if(busy||!current())return;setBusy(true);setMessage('');
  try{
   if(!restore){
    if(!ready)throw new Error('请先比较所选作用域');
    if(initialRuntime&&!initialRuntime.isCurrent())throw new Error('原画布实例已变化，请重新打开');
    if(runtime.current&&(!runtime.current.isCurrent()||await runtime.current.flush()!==true||!runtime.current.isCurrent()))throw new Error('画布保存未完成或原实例已关闭');
    if(!current())return;
   }
   const targets=states.map(s=>({scope:s.scope,expected_revision:s.local[kind].revision,scope_proof:s.proof}));
   const request:DirectionRequest|undefined=restore?undefined:{request_id:crypto.randomUUID(),kind,action,
    ...(level==='nodes'?{targets}:{...targets[0]}),...(action==='apply'&&entry?{selection:{id:entry.id,version:entry.version,proof:entry.proof}}:{})};
   const result=await createDirectionOperation(owner,current).run(request,`${entry?.title??title} · ${scopes.map(scopeName).join('、')}`);
   if(!current()||result.status==='detached')return;
   setIntent(readDirectionIntent(owner));
   setMessage(result.status==='applied'?'配置已保存；不会修改已有产物或启动生成':result.message??'请恢复原请求');
   if(result.status==='applied'){frozen.onApplied?.();window.dispatchEvent(new CustomEvent('direction-config-changed',{detail:{project:owner.project,user:owner.user}}));}
  }catch(e){if(current())setError(String(e));}finally{if(current())setBusy(false);}
 };
 const refresh=()=>{try{finishDirectionIntent(owner);setIntent(null);setMessage('');setCompare(n=>n+1);}catch(e){setError(String(e));}};
 return <section className="space-y-3 p-4 text-sm" aria-label={`固定${title}配置`}>
  <p className="text-muted-foreground">保存创作配置，不修改已有产物、不启动生成。每类只采用最近一层明确配置。</p>
  {intent&&<div className="rounded border border-border p-3"><p>原请求：{intent.label}</p><p>{intent.phase==='applied'?'已保存的提交结果':intent.phase==='failed'?'配置未保存，请重新比较':'结果待确认，继续保留原选择'}</p>{intent.error&&<p role="alert">{intent.error}</p>}
   {intent.phase==='pending'?<button className={control} disabled={busy} onClick={()=>void act('apply',true)}>恢复原请求</button>:<button className={control} onClick={refresh}>重新比较当前配置</button>}
   {intent.result?.effective.map((s,i)=>{const effect=s.effective[intent.request.kind];return <p key={i}>此次保存结果 · 目标 {i+1}：{effect.title??(effect.state==='disabled'?'已禁用':'继承上级')} {effect.version&&`· v${effect.version}`}；来源：{effect.origin_scope?{project:'项目',canvas:'画布',group:'分组',node:'节点'}[effect.origin_scope.level]:'无'}</p>;})}
  </div>}
  <fieldset disabled={busy||!!intent} className="space-y-3 disabled:opacity-60">
   <label className="block">影响范围 <select aria-label="影响范围" value={level} onChange={e=>setLevel(e.target.value as typeof level)} className={control}><option value="project">整个项目</option><option value="canvas">一个画布</option><option value="group">一个分组</option><option value="nodes">明确选择的节点</option></select></label>
   {level!=='project'&&<label className="block">目标画布 <select aria-label="目标画布" value={canvas} onChange={e=>{setCanvas(e.target.value);setGroup('');setNodeIds([]);}} className={control}><option value="">请选择注册画布</option>{canvases.map(c=><option key={c.canvas_id} value={c.canvas_id}>{c.title}</option>)}</select></label>}
   {level==='group'&&<label>目标分组 <select aria-label="目标分组" value={group} onChange={e=>setGroup(e.target.value)} className={control}><option value="">请选择分组</option>{nodes.filter(n=>n.type==='groupNode').map(n=><option key={n.id} value={n.id}>{nodeName(n.id)}</option>)}</select></label>}
   {level==='nodes'&&<div><p>{nodeIds.map(nodeName).join('、')||'请选择目标节点；跨组选择不会自动应用整组'}</p><div className="max-h-32 overflow-auto">{nodes.filter(n=>n.type!=='groupNode').map(n=><label key={n.id} className="mr-3 inline-flex gap-1"><input type="checkbox" checked={nodeIds.includes(n.id)} onChange={e=>setNodeIds(ids=>e.target.checked?[...ids,n.id]:ids.filter(id=>id!==n.id))}/>{nodeName(n.id)}</label>)}</div></div>}
   <label className="block">固定{title} <select aria-label={`固定${title}`} value={selected} onChange={e=>setSelected(e.target.value)} className={`${control} max-w-full`}><option value="">请选择{title}</option>{catalog.map(e=><option key={`${e.id}:${e.version}`} value={e.id}>{e.title} · v{e.version}{!e.available?'（已下架）':''}</option>)}</select></label>
   {entry&&<p>{entry.title} · v{entry.version}</p>}
   {selected&&!entry&&catalog.length>0&&<p role="alert" className="text-amber-500">所选目录项已不可用，请明确重新选择。</p>}
   {entry&&<details className="rounded border border-border p-2"><summary>查看将保存的固定内容</summary>{Object.entries(kind==='style'?(entry.configuration.projections??{}) as Record<string,unknown>:entry.configuration).filter(([key])=>!['id','version','content_hash'].includes(key)).map(([key,value])=><p key={key} className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">{{director:'导演方向',image:'画面方向',video:'视频方向',panel_tag:'标签',intent:'创作意图',action_beats:'动作节拍',performance:'表演',camera:'运镜',ending_composition:'结尾构图',avoid:'避免'}[key]??'创作内容'}：{Array.isArray(value)?value.join('；'):String(value)}</p>)}</details>}
   {!intent&&states.map(s=>{const effective=s.effective[kind];return <div key={JSON.stringify(s.scope)} className="rounded border border-border p-2"><p>{scopeName(s.scope)}：{effective.title??(effective.state==='disabled'?'已禁用':'尚未配置')}{effective.version&&` · v${effective.version}`}</p><p className="text-muted-foreground">来源：{effective.origin_scope?scopeName(effective.origin_scope):'无'}；本级 {s.local[kind].state==='inherit'?'继承':s.local[kind].state==='disabled'?'禁用':'明确覆盖'}</p>{effective.applicability?.reasons.map(r=><p key={r.code} className="text-amber-500">{r.message}</p>)}</div>;})}
   {kind==='technique'&&<p className="text-muted-foreground">适用性待具体视频模式及时长验证；不会伪造分段参数。</p>}
   {kind==='technique'&&rules&&<p className="text-muted-foreground">此版本适用：{rules.modes.map(mode=>({i2v:'首帧生成',fl2v:'首尾帧生成',ref_only:'参考图生成'}[mode]??mode)).join('、')}；{rules.min_duration_seconds}–{rules.max_duration_seconds} 秒。</p>}
   <div className="flex flex-wrap gap-2"><Button disabled={!ready||!entry?.available||busy} onClick={()=>void act('apply')}>{busy?'正在保存…':`应用固定${title}`}</Button><Button variant="outline" disabled={!ready||busy} onClick={()=>void act('unset')}>{level==='project'?'清除项目配置':'取消本级，继承上级'}</Button><Button variant="outline" disabled={!ready||busy} onClick={()=>void act('disable')}>禁用本级{title}</Button></div>
  </fieldset>
  {!intent&&<button className={`${control} text-muted-foreground`} disabled={busy} onClick={refresh}>重新比较目录与作用域</button>}
  {error&&<p role="alert" className="text-red-400">{error}</p>}{message&&<p role="status">{message}</p>}
 </section>;
}
