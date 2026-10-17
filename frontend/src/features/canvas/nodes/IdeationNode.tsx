import { memo, useCallback, useRef } from 'react';
import type { NodeProps } from '@xyflow/react';
import { readUrl } from '@/lib/url-params';
import type { ProductionContentNodeData } from '../domain/canvasNodes';
import { IdeationWorkspace } from '../production/ideation/IdeationWorkspace';
import { ProductionNodeShell } from '../production/ProductionNodeShell';

export const IdeationNode = memo(function IdeationNode({ id, data }: NodeProps) {
  const closeGuard = useRef<() => boolean>(() => true);
  const registerCloseGuard = useCallback((guard: () => boolean) => { closeGuard.current = guard; }, []);
  const project = readUrl().project ?? '';

  return <ProductionNodeShell title="故事创作" summary="从题材创意出发，找到立意方向，再明确采用后启动 E01 规划。"
    workspace beforeClose={() => closeGuard.current()}>
    {controls => <IdeationWorkspace project={project} nodeId={id} data={data as ProductionContentNodeData}
      registerCloseGuard={registerCloseGuard} controls={controls} />}
  </ProductionNodeShell>;
});
