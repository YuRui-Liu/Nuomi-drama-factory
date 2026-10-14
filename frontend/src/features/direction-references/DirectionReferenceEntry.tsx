import { useEffect,useRef,useState } from 'react';
import { useRouter,useRouterState } from '@tanstack/react-router';
import { Dialog,DialogContent,DialogDescription,DialogTitle } from '@/components/ui/dialog';
import { getSupertaleProject } from '@/api/projects';
import { useAuthStore } from '@/stores/auth-store';
import { useCanvasStore } from '@/stores/canvasStore';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import type { DirectionKind } from '@/api/directions';
import { DirectionReferencePanel } from './DirectionReferencePanel';
export function useManagedDirectionProject(project?:string){
 const user=useAuthStore(s=>s.username);const [result,setResult]=useState<{key:string;enabled:boolean}|null>(null);const key=`${user}:${project}`;
 useEffect(()=>{let live=true;if(project)void getSupertaleProject(project).then(p=>{if(live)setResult({key,enabled:p.creation_mode==='agent_canvas_v1'});}).catch(()=>{if(live)setResult(null);});return()=>{live=false;};},[key]);
 return !project?false:result?.key===key?result.enabled:null;
}
export function DirectionReferenceEntry(props:Parameters<typeof ManagedDirectionEntry>[0]){
 const enabled=useManagedDirectionProject(props.project);
 return enabled?<ManagedDirectionEntry {...props}/>:null;
}
function ManagedDirectionEntry({project,canvasId,nodeIds,selectedNodes=false,kind='style',selectedId,label='风格与手法配置',onApplied}:{project:string;canvasId?:string;nodeIds?:string[];selectedNodes?:boolean;kind?:DirectionKind;selectedId?:string;label?:string;onApplied?:()=>void}){
 const router=useRouter();const location=useRouterState({select:s=>s.location});const user=useAuthStore(s=>s.username);
 const latest=useRef({project,user,canvasId});latest.current={project,user,canvasId};
 const [opened,setOpened]=useState<{user:string;project:string;canvasId?:string;origin:typeof location;runtime:ReturnType<typeof captureFreezoneCanvasRuntime>;nodeIds:string[];kind:DirectionKind;id?:string}|null>(null);
 const [tab,setTab]=useState<DirectionKind>(kind);
 useEffect(()=>{setOpened(null);},[location,project,user]);
 if(!user)return null;
 const isCurrent=()=>!!opened&&latest.current.project===opened.project&&latest.current.canvasId===opened.canvasId&&router.state.location===opened.origin&&useAuthStore.getState().username===opened.user&&(!opened.runtime||opened.runtime.isCurrent());
 return <><button type="button" className="rounded border border-border bg-background px-3 py-1.5 text-xs text-foreground" onClick={e=>{e.stopPropagation();setTab(kind);const ids=nodeIds??(selectedNodes?useCanvasStore.getState().nodes.filter(n=>n.selected).map(n=>n.id):[]);setOpened({user,project,canvasId,origin:router.state.location,runtime:canvasId?captureFreezoneCanvasRuntime(project,canvasId):null,nodeIds:[...ids],kind,id:selectedId});}}>{label}</button>
 <Dialog open={!!opened} onOpenChange={open=>{if(!open)setOpened(null);}}><DialogContent className="max-h-[85dvh] overflow-auto sm:max-w-2xl" overlayClassName="z-[11000]" style={{zIndex:11001}}><DialogTitle>固定风格与手法</DialogTitle><DialogDescription>为选定作用域保存可信固定版本。</DialogDescription>
 {opened&&<><div className="flex w-fit gap-1 rounded-lg bg-muted p-1">{(['style','technique'] as const).map(value=><button key={value} className={`rounded-md px-5 py-2 text-sm transition-colors ${tab===value?'bg-background text-foreground shadow-sm':'text-muted-foreground hover:text-foreground'}`} aria-pressed={tab===value} onClick={()=>setTab(value)}>{value==='style'?'风格':'手法'}</button>)}</div><DirectionReferencePanel key={`${opened.user}:${tab}`} owner={{user:opened.user,project:opened.project}} kind={tab} selectedId={tab===opened.kind?opened.id:undefined} canvasId={opened.canvasId} nodeIds={opened.nodeIds} isCurrent={isCurrent} onApplied={onApplied}/></>}
 </DialogContent></Dialog></>;
}
