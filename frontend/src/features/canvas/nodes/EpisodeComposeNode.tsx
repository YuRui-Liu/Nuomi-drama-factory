import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { resolveMediaUrl } from '@/lib/media-url';
import { readUrl } from '@/lib/url-params';
import type { ProductionContentNodeData } from '../domain/canvasNodes';
import { ProductionGraphNodeCard } from './ProductionGraphNodeCard';
import { ProductionVideoPreview } from './ProductionVideoPreview';

export const EpisodeComposeNode = memo(function EpisodeComposeNode({ data }: NodeProps) {
  const d = data as ProductionContentNodeData;
  const project = readUrl().project ?? '';
  const episode = d.episodeNumber;
  const revisionId = typeof d.directorRevisionId === 'string' ? d.directorRevisionId : '';
  const groupIds = Array.isArray(d.productionGroupIds)
    ? d.productionGroupIds.filter((value): value is string => typeof value === 'string' && value.length > 0)
    : [];
  const validEpisode = typeof episode === 'number' && Number.isInteger(episode) && episode > 0;
  const validationError = !project || !validEpisode || !revisionId || groupIds.length === 0
    ? '此整集节点缺少项目、集数、叙事组或固定导演版本。请从已采用导演方案重新创建制作图。'
    : undefined;
  const title = typeof d.displayName === 'string' && d.displayName.trim()
    ? d.displayName.trim()
    : `E${String(validEpisode ? episode : 0).padStart(2, '0')} · 整集成片`;
  const videoUrl = typeof d.resultVideoUrl === 'string' && d.resultVideoUrl
    ? resolveMediaUrl(d.resultVideoUrl)
    : null;

  return <ProductionGraphNodeCard
    title={title}
    summary={`汇总 ${groupIds.length} 个叙事组 · 运行此节点开始整集合成`}
    validationError={validationError}
    orchestration={d.orchestration}
    result={videoUrl
      ? <ProductionVideoPreview src={videoUrl} title={title} />
      : <p className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">完整成片会在所有上游叙事组视频完成后回写到此节点。</p>}
  />;
});
