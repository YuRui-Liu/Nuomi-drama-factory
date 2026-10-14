import { getSharedPublication, publishSharedAsset, registerCreationAsset, type SharedAssetRequest, type SharedAssetResult } from '@/api/creation';

export interface PublicationScope { user:string; project:string; canvas:string; node:string }
export interface PublicationDisplay { title:string;parentTitle?:string;mode:string;role:string }
export interface PublicationIntent { scope:PublicationScope; request:SharedAssetRequest; display?:PublicationDisplay; phase:'registering'|'publishing'|'failed'|'applied'; result?:SharedAssetResult; error?:string }
export type PublicationOutcome = {status:'applied'|'pending'|'failed'|'busy'|'detached';message?:string;result?:SharedAssetResult};
const key = (scope:PublicationScope) => `shared-asset-publication:v1:${[scope.user,scope.project,scope.canvas].map(encodeURIComponent).join(':')}`;
export function readSharedPublication(scope:Omit<PublicationScope,'node'>):PublicationIntent|null {
  const raw=localStorage.getItem(key({...scope,node:''}));
  if (!raw) return null;
  const value=JSON.parse(raw) as PublicationIntent;
  if (value.scope?.user!==scope.user || value.scope?.project!==scope.project || value.scope?.canvas!==scope.canvas || !value.request?.request_id || value.request.source?.node_id!==value.scope.node || value.request.source?.canvas_id!==scope.canvas) throw new Error('资产保存恢复记录损坏，请先保留记录');
  return value;
}
export function clearFinishedPublication(scope:PublicationScope) {
  const pending=readSharedPublication(scope);
  if (pending && !['failed','applied'].includes(pending.phase)) throw new Error('保存结果尚未确定，请先恢复原请求');
  if (pending) localStorage.setItem(`${key(scope)}:last-result`,JSON.stringify(pending));
  localStorage.removeItem(key(scope));
}
const flights=new Set<string>();
function code(error:unknown):string|undefined {
  const value=error as {code?:string;body?:{detail?:{code?:string};code?:string}};
  return value?.code ?? value?.body?.detail?.code ?? value?.body?.code;
}
/** Registration and publication are separate server boundaries. Persist before BOTH. */
export function createSharedAssetPublication(scope:PublicationScope,isCurrent:()=>boolean) {
  const persist=(intent:PublicationIntent)=>localStorage.setItem(key(scope),JSON.stringify(intent));
  return { async run(input?:SharedAssetRequest,display?:PublicationDisplay):Promise<PublicationOutcome> {
    if (!isCurrent()) return {status:'detached'};
    if (flights.has(key(scope))) return {status:'busy',message:'此画布已有资产正在保存'};
    flights.add(key(scope));
    let intent:PublicationIntent|null=null;
    let durable=false;
    try {
      intent=readSharedPublication(scope);
      durable=Boolean(intent);
      if (intent && (intent.scope.node!==scope.node || input && (input.request_id!==intent.request.request_id || JSON.stringify(input)!==JSON.stringify(intent.request)))) return {status:'busy',message:'请先恢复此画布尚未完成的保存'};
      if (intent?.result?.status==='applied') return {status:'applied',result:intent.result};
      if (intent?.phase==='failed') return {status:'failed',message:intent.error};
      if (!intent) {
        if (!input) return {status:'failed',message:'没有可恢复的保存请求'};
        intent={scope:{...scope},request:structuredClone(input),...(display?{display:structuredClone(display)}:{}),phase:input.mode!=='new_base'&&!input.expected_ref?'registering':'publishing'};
        persist(intent);
        durable=true;
      }
      if (intent.phase==='registering') {
        // Retry only the ORIGINAL proof: never refresh a changed target silently.
        const ref=await registerCreationAsset(scope.project,{selection:intent.request.selection!,proof:intent.request.proof!});
        intent.request={...intent.request,expected_ref:ref};intent.phase='publishing';persist(intent);
      }
      if (!isCurrent()) return {status:'detached'};
      let result:SharedAssetResult;
      try { result=await publishSharedAsset(scope.project,intent.request); }
      catch (error) {
        try {result=await getSharedPublication(scope.project,intent.request.request_id);}
        catch (statusError) {
          const status=(error as {status?:number}).status;
          if (code(statusError)==='asset_publication_missing' && status && status>=400 && status<500) {
            intent.phase='failed';intent.error=error instanceof Error?error.message:String(error);persist(intent);
            return isCurrent()?{status:'failed',message:intent.error}:{status:'detached'};
          }
          return isCurrent()?{status:'pending',message:'保存结果尚未确定。恢复时会重试同一请求，不会重复创建资产。'}:{status:'detached'};
        }
      }
      if (result.status==='applied') {intent.phase='applied';intent.result=result;persist(intent);}
      return isCurrent()?{status:result.status==='applied'?'applied':'pending',result,message:result.status==='reconciliation_required'?'素材保存等待对账，请恢复原请求':'保存尚未完成'}:{status:'detached'};
    } catch (error) {
      // Registration failures remain retryable with their original proof;
      // losing the response must never create a new publication identity.
      const status=(error as {status?:number}).status;
      if (durable&&intent?.phase==='registering'&&status&&status>=400&&status<500) {
        intent.phase='failed';intent.error=error instanceof Error?error.message:String(error);persist(intent);
        return {status:'failed',message:intent.error};
      }
      return {status:durable?'pending':'failed',message:error instanceof Error?error.message:String(error)};
    } finally {flights.delete(key(scope));}
  }};
}
