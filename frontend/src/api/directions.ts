import { apiCall } from './client';
export type DirectionKind='style'|'technique';
export type DirectionScope={level:'project'}|{level:'canvas';canvas_id:string}|{level:'group';canvas_id:string;group_id:string}|{level:'node';canvas_id:string;node_id:string};
export interface DirectionEntry {kind:DirectionKind;id:string;version:string;title:string;available:boolean;proof:string;configuration:Record<string,unknown>;metadata:Record<string,unknown>}
export interface DirectionEffect {state:'unset'|'enabled'|'disabled';origin_scope:DirectionScope|null;snapshot_ref:Record<string,unknown>|null;configuration:Record<string,unknown>|null;title?:string;version?:string;applicability?:{status:string;compatible:boolean|null;reasons:{code:string;message:string}[]}}
export interface DirectionState {scope:DirectionScope;proof:string;chain:DirectionScope[];local:Record<DirectionKind,{revision:number;state:string;snapshot_id:string|null}>;effective:Record<DirectionKind,DirectionEffect>}
export interface DirectionTarget {scope:DirectionScope;expected_revision:number;scope_proof:string}
export interface DirectionRequest {request_id:string;kind:DirectionKind;action:'apply'|'unset'|'disable';scope?:DirectionScope;expected_revision?:number;scope_proof?:string;targets?:DirectionTarget[];selection?:{id:string;version:string;proof:string}}
export interface DirectionResult {request_id:string;operation_id:string;status:'applied';kind:DirectionKind;snapshot_ref:Record<string,unknown>|null;bindings:{scope:DirectionScope;revision:number;state:string}[];effective:DirectionState[]}
const root=(project:string)=>`projects/${encodeURIComponent(project)}/creation`;
export const listDirectionCatalog=(project:string,kind:DirectionKind)=>apiCall<DirectionEntry[]>(`${root(project)}/direction-catalog?kind=${kind}`);
export const getDirectionBindings=(project:string,scope:DirectionScope,context?:{mode:string;duration_seconds:number})=>apiCall<DirectionState>(`${root(project)}/direction-bindings?${new URLSearchParams(Object.entries({...scope,...context}).map(([key,value])=>[key,String(value)]))}`);
export const applyDirectionBindings=(project:string,request:DirectionRequest)=>apiCall<DirectionResult>(`${root(project)}/direction-bindings`,{method:'POST',json:request});
export const getDirectionRequest=(project:string,id:string)=>apiCall<DirectionResult|{request_id:string;status:'unknown'}>(`${root(project)}/direction-requests/${encodeURIComponent(id)}`);
export const getDirectionSnapshot=(project:string,id:string)=>apiCall<DirectionEntry & {snapshot_ref:Record<string,unknown>}>(`${root(project)}/direction-snapshots/${encodeURIComponent(id)}`);
