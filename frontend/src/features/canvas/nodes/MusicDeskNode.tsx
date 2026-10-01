import {memo,useCallback,useState} from 'react';
import {Handle,Position,type NodeProps} from '@xyflow/react';
import {Music2} from 'lucide-react';
import {useCanvasStore} from '@/stores/canvasStore';
import {useUpstreamNodes} from '@/features/canvas/application/useUpstreamGraph';
import {CANVAS_NODE_TYPES,type MusicDeskNodeData} from '../domain/canvasNodes';
import {NodeHeader,NODE_HEADER_FLOATING_POSITION_CLASS} from '../ui/NodeHeader';
import {readUrl} from '@/lib/url-params';
import {MusicDeskModal} from '../music/MusicDeskModal';

export const MusicDeskNode=memo(({id,data}:NodeProps)=>{
 const d=data as MusicDeskNodeData;const upstream=useUpstreamNodes(id);const [open,setOpen]=useState(false);
 const update=useCanvasStore(s=>s.updateNodeData);const {project,canvas}=readUrl();
 const videos=upstream.filter(n=>n.type===CANVAS_NODE_TYPES.video||n.type===CANVAS_NODE_TYPES.videoCompose);
 const urls=videos.map(n=>String(n.data.videoUrl||n.data.resultVideoUrl||'')).filter(Boolean);
 const source=videos.length===1&&urls.length===1?urls[0]:'';
 const exported=useCallback((url:string)=>{update(id,{resultVideoUrl:url});},[id,update]);
 const saved=useCallback((sourceUrl:string)=>update(id,{sourceUrl}),[id,update]);
 return <div className="relative h-full min-h-[160px] w-full min-w-[260px] rounded-xl border border-lime-200/25 bg-[#192018] p-5 text-white">
  <Handle type="target" position={Position.Left} id="target"/><Handle type="source" position={Position.Right} id="source"/>
  <NodeHeader className={NODE_HEADER_FLOATING_POSITION_CLASS} icon={<Music2 className="h-4 w-4"/>} titleText={String(d.displayName||'配乐台')} editable onTitleChange={displayName=>update(id,{displayName})}/>
  <div className="flex flex-col gap-3"><span className="text-xs text-lime-100/60">成片后期 · 多轨配乐</span><button className="nodrag rounded-lg border border-lime-200/25 bg-lime-100/10 px-4 py-3 text-sm disabled:opacity-40" disabled={!source||!project} onClick={()=>setOpen(true)}>打开配乐台</button><span className="text-xs text-white/50">{videos.length>1?'请只连接一个成片':source?'全局铺乐 / 自由选段 / 多轨叠加':'从成片视频连接到此节点'}</span>{d.sourceUrl&&source&&d.sourceUrl!==source&&<span className="text-xs text-amber-200">源连接已变化，已保存计划仍使用原成片版本</span>}{d.resultVideoUrl&&<a className="nodrag text-xs text-lime-200" href={d.resultVideoUrl} target="_blank" rel="noreferrer">查看配乐版成片 ↗</a>}</div>
  {open&&project&&<MusicDeskModal project={project} canvas={canvas||'default'} node={id} sourceUrl={source} onClose={()=>setOpen(false)} onSaved={saved} onExport={exported}/>}
 </div>;
});
