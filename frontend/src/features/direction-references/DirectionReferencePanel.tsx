import { useEffect,useRef,useState } from 'react';
import './direction-reference.css';
import { Button } from '@/components/ui/button';
import { listCreationCanvases } from '@/api/creation';
import { getFreezoneCanvas } from '@/api/canvas';
import { listDirectionCatalog,getDirectionBindings,type DirectionKind,type DirectionEntry,type DirectionScope,type DirectionState,type DirectionRequest } from '@/api/directions';
import type { CreationCanvas } from '@/features/creation-workspace/contracts';
import { captureFreezoneCanvasRuntime,currentFreezoneCanvasScope } from '@/features/freezone/canvasSyncRuntime';
import { createDirectionOperation,readDirectionIntent,finishDirectionIntent,type DirectionOwner,type DirectionIntent } from './directionOperation';
import type { SettingsDraftGuard } from '@/features/creation-workspace/projectSettingsDraftGuard';

interface NodeOption {id:string;type?:string;data?:Record<string,unknown>}
type DirectionSelectionDraft={level:'project'|'canvas'|'group'|'nodes'|'choose';canvas:string;group:string;nodeIds:string[];selected:string;version:string};
const draftKey=(owner:DirectionOwner,kind:DirectionKind)=>`direction-settings-draft:v1:${encodeURIComponent(owner.user)}:${encodeURIComponent(owner.project)}:${kind}`;
function readDraft(owner:DirectionOwner,kind:DirectionKind):DirectionSelectionDraft|null{try{const value=localStorage.getItem(draftKey(owner,kind));if(!value)return null;const draft=JSON.parse(value) as DirectionSelectionDraft;if(!['project','canvas','group','nodes','choose'].includes(draft.level)||typeof draft.canvas!=='string'||typeof draft.group!=='string'||!Array.isArray(draft.nodeIds)||typeof draft.selected!=='string'||typeof draft.version!=='string')return null;return draft;}catch{return null;}}
export interface DirectionPanelProps {owner:DirectionOwner;kind:DirectionKind;selectedId?:string;canvasId?:string;nodeIds?:string[];selectedTargets?:{id:string;type?:string}[];isCurrent:()=>boolean;onApplied?:()=>void;defaultProjectScope?:boolean;workspaceRuntime?:{flush:()=>Promise<boolean>;isCurrent:()=>boolean}|null;draftGuardRef?:{current:SettingsDraftGuard|null}}
const control='rounded border border-border bg-background px-2 py-1 text-sm';
export function DirectionReferencePanel(props:DirectionPanelProps){
 const [frozen]=useState(()=>({...props,nodeIds:props.nodeIds?[...props.nodeIds]:[],selectedTargets:props.selectedTargets?.map(n=>({...n}))}));
 const [savedDraft]=useState(()=>frozen.defaultProjectScope?readDraft(frozen.owner,frozen.kind):null);
 const groupTargets=frozen.selectedTargets?.filter(n=>n.type==='groupNode')??[];
 const singleGroup=groupTargets.length===1&&frozen.selectedTargets?.length===1?groupTargets[0].id:'';
 const ambiguous=!!frozen.selectedTargets?.length&&!singleGroup&&(groupTargets.length>0||frozen.selectedTargets.some(n=>!n.type));
 const {owner,kind}=frozen;const title=kind==='style'?'风格':'手法';
 const alive=useRef(true);useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 const current=()=>alive.current&&frozen.isCurrent();
 const [catalog,setCatalog]=useState<DirectionEntry[]>([]),[selected,setSelected]=useState(frozen.selectedId??savedDraft?.selected??''),[selectedVersion,setSelectedVersion]=useState(savedDraft?.version??'');
 const [canvases,setCanvases]=useState<CreationCanvas[]>([]),[canvas,setCanvas]=useState(savedDraft?.canvas||frozen.canvasId||'');
 const [nodes,setNodes]=useState<NodeOption[]>([]),[nodeIds,setNodeIds]=useState(ambiguous||singleGroup?[]:savedDraft?.nodeIds??frozen.nodeIds),[group,setGroup]=useState(savedDraft?.group??singleGroup);
 const [level,setLevel]=useState<'project'|'canvas'|'group'|'nodes'|'choose'>(ambiguous?'choose':savedDraft?.level??(frozen.defaultProjectScope?'project':singleGroup?'group':frozen.nodeIds.length?'nodes':frozen.canvasId?'canvas':'project')),[draftDirty,setDraftDirty]=useState(false);
 const [states,setStates]=useState<DirectionState[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[compare,setCompare]=useState(0);
 const [intent,setIntent]=useState<DirectionIntent|null>(null),[message,setMessage]=useState('');
 const [editingNodes,setEditingNodes]=useState(false),[query,setQuery]=useState('');
 const runtime=useRef<DirectionPanelProps['workspaceRuntime']>(null);
 const [initialRuntime]=useState(()=>frozen.workspaceRuntime??(frozen.canvasId?captureFreezoneCanvasRuntime(owner.project,frozen.canvasId):null));
 const scopes:DirectionScope[]=level==='choose'?[]:level==='project'?[{level:'project'}]:!canvas?[]:level==='canvas'?[{level:'canvas',canvas_id:canvas}]:level==='group'?(group?[{level:'group',canvas_id:canvas,group_id:group}]:[]):nodeIds.map(node_id=>({level:'node',canvas_id:canvas,node_id}));
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
   const active=currentFreezoneCanvasScope();runtime.current=canvas===frozen.canvasId&&frozen.workspaceRuntime?frozen.workspaceRuntime:canvas?captureFreezoneCanvasRuntime(owner.project,canvas):null;
   if(canvas&&active?.project===owner.project&&active.canvasId===canvas&&!runtime.current)throw new Error('画布同步尚未准备好');
   if(runtime.current&&await runtime.current.flush()!==true)throw new Error('画布保存未完成，未读取配置证据');
   if(!live||!current()||runtime.current&&!runtime.current.isCurrent())return;
   if(level!=='project'&&!canvases.some(c=>c.canvas_id===canvas))return;
   const result=await Promise.all(scopes.map(scope=>getDirectionBindings(owner.project,scope)));
   if(live&&current())setStates(result);
  })().catch(e=>{if(live&&current())setError(String(e));});return()=>{live=false;};
 },[scopeKey,compare,JSON.stringify(canvases.map(c=>c.canvas_id)),intent?.request.request_id]);
 const entry=catalog.find(e=>e.id===selected&&(!selectedVersion||e.version===selectedVersion));
 const unchanged=(action:DirectionRequest['action'])=>ready&&states.length===scopes.length&&states.every(state=>{
  const local=state.local[kind];
  if(action==='unset')return local.state==='inherit';
  if(action==='disable')return local.state==='disabled';
  const effective=state.effective[kind];
  return !!entry&&local.state==='enabled'&&!!local.snapshot_id&&local.snapshot_id===effective.snapshot_ref?.snapshot_id&&
   effective.state==='enabled'&&JSON.stringify(effective.origin_scope)===JSON.stringify(state.scope)&&
   effective.version===entry.version&&effective.snapshot_ref?.content_hash===entry.proof;
 });
 const applyUnchanged=unchanged('apply'),unsetUnchanged=unchanged('unset'),disableUnchanged=unchanged('disable');
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
   if(result.status==='applied'){setDraftDirty(false);try{localStorage.removeItem(draftKey(owner,kind));}catch{}frozen.onApplied?.();window.dispatchEvent(new CustomEvent('direction-config-changed',{detail:{project:owner.project,user:owner.user}}));}
  }catch(e){if(current())setError(String(e));}finally{if(current())setBusy(false);}
 };
 const refresh=()=>{try{finishDirectionIntent(owner);setIntent(null);setMessage('');setCompare(n=>n+1);}catch(e){setError(String(e));}};
 if(frozen.draftGuardRef)frozen.draftGuardRef.current={isDirty:()=>draftDirty,save:async()=>{if(!draftDirty)return true;try{localStorage.setItem(draftKey(owner,kind),JSON.stringify({level,canvas,group,nodeIds,selected,version:entry?.version??selectedVersion} satisfies DirectionSelectionDraft));setDraftDirty(false);return true;}catch(e){if(current())setError(String(e));return false;}},discard:()=>{try{localStorage.removeItem(draftKey(owner,kind));}catch{}setSelected(frozen.selectedId??'');setSelectedVersion('');setCanvas(frozen.canvasId??'');setNodeIds(ambiguous||singleGroup?[]:frozen.nodeIds);setGroup(singleGroup);setLevel(ambiguous?'choose':frozen.defaultProjectScope?'project':singleGroup?'group':frozen.nodeIds.length?'nodes':frozen.canvasId?'canvas':'project');setDraftDirty(false);}};
 const setScope=(value:DirectionSelectionDraft['level'])=>{setLevel(value);setDraftDirty(true);if(value!=='nodes')setEditingNodes(false);};
 const chooseCanvas=(value:string)=>{setCanvas(value);setGroup('');if(level==='nodes')setNodeIds([]);setDraftDirty(true);};
 const candidates=catalog.filter(value=>value.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
 const groupNodes=nodes.filter(node=>node.type==='groupNode');
 const leafNodes=nodes.filter(node=>node.type!=='groupNode');
 const selectedNodeNames=nodeIds.map(nodeName);
 return <section className="direction-panel space-y-4 text-sm" aria-label={`固定${title}配置`}>
  <p className="text-muted-foreground">保存创作配置，不修改已有产物、不启动生成。每类只采用最近一层明确配置。</p>
  {intent&&<div className="rounded border border-border p-3"><p>原请求：{intent.label}</p><p>{intent.phase==='applied'?'已保存的提交结果':intent.phase==='failed'?'配置未保存，请重新比较':'结果待确认，继续保留原选择'}</p>{intent.error&&<p role="alert">{intent.error}</p>}
   {intent.phase==='pending'?<button className={control} disabled={busy} onClick={()=>void act('apply',true)}>恢复原请求</button>:<button className={control} onClick={refresh}>重新比较当前配置</button>}
   {intent.result?.effective.map((s,i)=>{const effect=s.effective[intent.request.kind];return <p key={i}>此次保存结果 · 目标 {i+1}：{effect.title??(effect.state==='disabled'?'已禁用':'继承上级')} {effect.version&&`· v${effect.version}`}；来源：{effect.origin_scope?{project:'项目',canvas:'画布',group:'分组',node:'节点'}[effect.origin_scope.level]:'无'}</p>;})}
  </div>}
  <fieldset disabled={busy||!!intent} className="space-y-3 disabled:opacity-60">
   {ambiguous&&<p role="alert">当前选择包含多个分组或分组与节点，请明确选择一个分组或节点集合。</p>}
   <div className="direction-field"><span>作用范围</span><div className="direction-scopes" role="group" aria-label="作用范围">{([['project','整个项目'],['canvas','一个画布'],['group','一个分组'],['nodes','明确节点']] as const).map(([value,label])=><button key={value} type="button" className={`direction-scope-option ${level===value?'is-selected':''}`} aria-pressed={level===value} onClick={()=>setScope(value)}>{label}</button>)}</div></div>
   {level!=='project'&&!canvas&&<div className="direction-field"><span>目标画布</span><div className="direction-choice-grid">{canvases.map(item=><button key={item.canvas_id} type="button" className={`direction-choice-button ${canvas===item.canvas_id?'is-selected':''}`} aria-pressed={canvas===item.canvas_id} onClick={()=>chooseCanvas(item.canvas_id)}>{item.title}</button>)}</div>{canvases.length===0&&<p className="text-muted-foreground">暂无可配置的注册画布。</p>}</div>}
   {level==='group'&&canvas&&<div className="direction-field"><span>目标分组</span><div className="direction-choice-grid">{groupNodes.map(node=><button key={node.id} type="button" className={`direction-choice-button ${group===node.id?'is-selected':''}`} aria-pressed={group===node.id} onClick={()=>{setGroup(node.id);setDraftDirty(true);}}>{nodeName(node.id)}</button>)}</div>{groupNodes.length===0&&<p className="text-muted-foreground">当前画布没有可选分组。</p>}</div>}
   {level==='nodes'&&canvas&&<div className="direction-targets"><div className="direction-targets-title"><span>目标节点</span><span>{nodeIds.length} 个已选</span></div>{nodeIds.length>0&&!editingNodes?<div className="direction-node-summary"><p>{selectedNodeNames.join('、')}</p><p>{canvases.find(item=>item.canvas_id===canvas)?.title??'当前画布'} · 已选 {nodeIds.length} 个节点</p><button type="button" onClick={()=>setEditingNodes(true)}>更换节点</button></div>:<><div className="direction-node-list">{leafNodes.map(node=><label key={node.id} className="direction-node-option"><input type="checkbox" checked={nodeIds.includes(node.id)} onChange={event=>{setNodeIds(ids=>event.target.checked?[...ids,node.id]:ids.filter(id=>id!==node.id));setDraftDirty(true);}}/>{nodeName(node.id)}</label>)}</div>{editingNodes&&<button type="button" className="direction-inline-action" onClick={()=>setEditingNodes(false)}>完成选择</button>}</>}</div>}
   <div className="direction-field"><div className="direction-picker-heading"><span>固定{title}</span><span>{catalog.length} 个候选</span></div><input aria-label={`搜索${title}`} type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder={`搜索${title}名称或关键词`}/><div className="direction-candidate-grid" role="group" aria-label={`${title}候选项`}>{candidates.map(value=><button key={`${value.id}:${value.version}`} type="button" className={`direction-candidate-card ${selected===value.id&&entry?.version===value.version?'is-selected':''}`} aria-pressed={selected===value.id&&entry?.version===value.version} disabled={!value.available} onClick={()=>{setSelected(value.id);setSelectedVersion(value.version);setDraftDirty(true);}}><span>{value.title}</span><small>v{value.version} · {selected===value.id&&entry?.version===value.version?'已选':'选择此项'}</small></button>)}</div>{candidates.length===0&&<p className="text-muted-foreground">没有匹配的{title}。</p>}</div>
   {entry&&<p>{entry.title} · v{entry.version}</p>}
   {selected&&!entry&&catalog.length>0&&<p role="alert" className="text-amber-500">所选目录项已不可用，请明确重新选择。</p>}
   {entry&&<details className="rounded border border-border p-2"><summary>查看将保存的固定内容</summary>{Object.entries(kind==='style'?(entry.configuration.projections??{}) as Record<string,unknown>:entry.configuration).filter(([key])=>!['id','version','content_hash'].includes(key)).map(([key,value])=><p key={key} className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">{{director:'导演方向',image:'画面方向',video:'视频方向',panel_tag:'标签',intent:'创作意图',action_beats:'动作节拍',performance:'表演',camera:'运镜',ending_composition:'结尾构图',avoid:'避免'}[key]??'创作内容'}：{Array.isArray(value)?value.join('；'):String(value)}</p>)}</details>}
   {!intent&&states.map(s=>{const effective=s.effective[kind];return <div key={JSON.stringify(s.scope)} className="direction-current"><p>{scopeName(s.scope)}：{effective.title??(effective.state==='disabled'?'已禁用':'尚未配置')}{effective.version&&` · v${effective.version}`}</p><p className="text-muted-foreground">来源：{effective.origin_scope?scopeName(effective.origin_scope):'无'}；本级 {s.local[kind].state==='inherit'?'继承':s.local[kind].state==='disabled'?'禁用':'明确覆盖'}</p>{effective.applicability?.reasons.map(r=><p key={r.code} className="text-amber-500">{r.message}</p>)}</div>;})}
   {kind==='technique'&&<p className="text-muted-foreground">适用性待具体视频模式及时长验证；不会伪造分段参数。</p>}
   {kind==='technique'&&rules&&<p className="text-muted-foreground">此版本适用：{rules.modes.map(mode=>({i2v:'首帧生成',fl2v:'首尾帧生成',ref_only:'参考图生成'}[mode]??mode)).join('、')}；{rules.min_duration_seconds}–{rules.max_duration_seconds} 秒。</p>}
   <div className="direction-actions"><Button aria-label={`应用固定${title}`} disabled={!ready||!entry?.available||busy||applyUnchanged} onClick={()=>void act('apply')}>{busy?'正在保存…':`应用此${title}`}</Button><Button variant="outline" disabled={!ready||busy||unsetUnchanged} onClick={()=>void act('unset')}>{level==='project'?'清除项目配置':'取消本级，继承上级'}</Button><Button variant="ghost" disabled={!ready||busy||disableUnchanged} onClick={()=>void act('disable')}>禁用本级{title}</Button></div>
  </fieldset>
  {!intent&&<button className="direction-refresh" disabled={busy} onClick={refresh}>重新比较目录与作用域</button>}
  {error&&<p role="alert" className="text-red-400">{error}</p>}{message&&<p role="status">{message}</p>}
 </section>;
}
