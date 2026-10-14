import { applyDirectionBindings,getDirectionRequest,type DirectionRequest,type DirectionResult } from '@/api/directions';
export interface DirectionOwner {user:string;project:string}
export interface DirectionIntent {owner:DirectionOwner;request:DirectionRequest;label:string;phase:'pending'|'failed'|'applied';result?:DirectionResult;error?:string}
export type DirectionOutcome={status:'applied'|'pending'|'failed'|'busy'|'detached';result?:DirectionResult;message?:string};
const key=(owner:DirectionOwner)=>`direction-operation:v1:${encodeURIComponent(owner.user)}:${encodeURIComponent(owner.project)}`;
export function readDirectionIntent(owner:DirectionOwner):DirectionIntent|null {
 const raw=localStorage.getItem(key(owner));if(!raw)return null;
 const value=JSON.parse(raw) as DirectionIntent;
 if(value.owner?.user!==owner.user||value.owner?.project!==owner.project||!value.request?.request_id||!['pending','applied','failed'].includes(value.phase))throw new Error('配置恢复记录损坏，请先保留记录');
 return value;
}
export function finishDirectionIntent(owner:DirectionOwner){const value=readDirectionIntent(owner);if(value?.phase==='pending')throw new Error('结果尚未确定，请恢复原请求');if(value)localStorage.setItem(`${key(owner)}:last`,JSON.stringify(value));localStorage.removeItem(key(owner));}
const flights=new Set<string>();
const code=(error:unknown)=>{const e=error as {code?:string;body?:{detail?:{code?:string}}};return e?.code??e?.body?.detail?.code;};
export function createDirectionOperation(owner:DirectionOwner,isCurrent:()=>boolean){return {async run(input?:DirectionRequest,label=''):Promise<DirectionOutcome>{
 if(!isCurrent())return {status:'detached'};
 if(flights.has(key(owner)))return {status:'busy',message:'此项目已有配置操作，请先恢复'};
 flights.add(key(owner));let intent:DirectionIntent|null=null;let durable=false;
 const persist=()=>localStorage.setItem(key(owner),JSON.stringify(intent));
 const outcome=(value:DirectionOutcome):DirectionOutcome=>isCurrent()?value:{status:'detached'};
 try{
  intent=readDirectionIntent(owner);durable=!!intent;
  if(intent&&input&&JSON.stringify(input)!==JSON.stringify(intent.request))return outcome({status:'busy',message:'请先处理原作用域的请求'});
  if(intent?.phase==='applied')return outcome({status:'applied',result:intent.result});
  if(intent?.phase==='failed')return outcome({status:'failed',message:intent.error});
  if(!intent){if(!input)return outcome({status:'failed',message:'没有可恢复请求'});intent={owner:{...owner},request:structuredClone(input),label,phase:'pending'};persist();durable=true;}
  // A receipt can be restored even if the old node/runtime no longer exists.
  if(!input){const receipt=await getDirectionRequest(owner.project,intent.request.request_id);if(receipt.status==='applied'){intent.phase='applied';intent.result=receipt;persist();return outcome({status:'applied',result:receipt});}}
  if(!isCurrent())return {status:'detached'};
  let result:DirectionResult;
  try{result=await applyDirectionBindings(owner.project,intent.request);}
  catch(error){
   if(code(error)==='idempotency_conflict'){intent.phase='failed';intent.error='请求编号与服务器已有操作不符，请重新比较';persist();return outcome({status:'failed',message:intent.error});}
   const terminal=['catalog_stale','direction_conflict','scope_violation','invalid_contract','legacy_project_readonly','idempotency_conflict'].includes(code(error)??'');
   // The backend returns these only before mutation (or after rollback), and
   // checks a committed receipt first. No second endpoint is needed to prove it.
   if(terminal){intent.phase='failed';intent.error=error instanceof Error?error.message:String(error);persist();return outcome({status:'failed',message:intent.error});}
   try{const receipt=await getDirectionRequest(owner.project,intent.request.request_id);if(receipt.status==='applied')result=receipt;else return outcome({status:'pending',message:'配置结果尚未确定，请恢复原请求'});}
   catch{return outcome({status:'pending',message:'无法确认配置结果，请恢复原请求'});}
  }
  intent.phase='applied';intent.result=result;persist();return outcome({status:'applied',result});
 }catch(error){return outcome({status:durable?'pending':'failed',message:error instanceof Error?error.message:String(error)});}
 finally{flights.delete(key(owner));}
 }};}
