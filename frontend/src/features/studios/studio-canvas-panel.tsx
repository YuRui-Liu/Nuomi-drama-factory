import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { p } from '@/lib/api-path';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import type { CanvasAsset } from '@/features/canvas/domain/canvasAssets';
import { StudioShortcut } from './studio-shortcut';
import { previsCanvasAsset, type PrevisOutput } from './studio-assets';

export function StudioCanvasPanel({project, nodeId, onUseAsset}: {project: string; nodeId?: string | null; onUseAsset: (asset: CanvasAsset) => void}) {
  const [open, setOpen] = useState(false);
  const query = useQuery({queryKey:['studios',project,'previs-outputs'], enabled:open,
    queryFn: ({signal}) => api.get(p`api/v1/projects/${project}/studios/previs-tools/outputs`,{signal}).json<{data:PrevisOutput[]}>()});
  const assets = (query.data?.data ?? []).map(o => previsCanvasAsset(project,o)).filter((a): a is CanvasAsset => a !== null);
  return <><Button variant="ghost" size="sm" onClick={() => setOpen(true)}>工作室</Button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-3xl">
      <DialogTitle>工作室与预演素材</DialogTitle><DialogDescription>从当前画布进入工作室，或把已导出的预演素材添加为新节点，保留原节点。</DialogDescription>
      <div className="flex flex-wrap gap-2">{(['character','director','previs','intro'] as const).map((studio,i) => <StudioShortcut key={studio} project={project} studio={studio} node={nodeId ?? undefined} returnTo={window.location.pathname+window.location.search} label={['角色造型','导演自定义','3D 预演','片头制作'][i]} />)}</div>
      {nodeId && <p className="text-xs text-muted-foreground">来源节点：{nodeId}</p>}
      {query.isPending && <p role="status">正在读取项目预演素材…</p>}
      {query.error && <p role="alert">素材读取失败：{query.error.message}<Button onClick={()=>void query.refetch()} variant="ghost">重试</Button></p>}
      {!query.isPending && !query.error && assets.length===0 && <p className="text-sm text-muted-foreground">尚无导出的预演素材。在 3D 预演中导出参考帧或白模视频后，可从这里添加到画布。</p>}
      <div className="grid gap-3 sm:grid-cols-2">{assets.map(asset=><article key={asset.id} className="space-y-2 rounded-lg border p-3">
        {asset.kind==='image'?<img className="aspect-video w-full rounded object-contain" src={asset.url} alt={asset.label ?? '预演参考帧'}/>:<video className="aspect-video w-full rounded" src={asset.url} controls preload="metadata"/>}
        <p className="text-sm">{asset.label}</p><Button size="sm" onClick={()=>{onUseAsset(asset);setOpen(false);}}>添加到当前画布</Button>
      </article>)}</div>
    </DialogContent></Dialog></>;
}
