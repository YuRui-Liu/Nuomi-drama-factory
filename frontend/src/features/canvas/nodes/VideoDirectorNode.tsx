// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useEffect } from 'react';
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/react';
import { Clapperboard } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { CANVAS_NODE_TYPES, type VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import { CANVAS_NODE_INPUT_SURFACE_CLASS, canvasNodeFrameClass } from '@/features/canvas/ui/nodeFrameStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { resolveMediaUrl } from '@/lib/media-url';

export const VIDEO_DIRECTOR_NODE_SIZE = { width: 320, height: 250 } as const;

type VideoDirectorNodeProps = NodeProps & {
  data: VideoDirectorNodeData;
  /** Task 8 supplies the editor controller; a read-only card has no action. */
  onOpenEditor?: (nodeId: string) => void;
};

export const VideoDirectorNode = memo(function VideoDirectorNode({ id, data, selected, onOpenEditor }: VideoDirectorNodeProps) {
  const { t } = useTranslation();
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const segments = data.draft?.segments ?? [];
  const durationSeconds = segments.reduce((total, segment) => total + segment.durationSeconds, 0);
  const videoUrl = resolveMediaUrl(data.videoUrl);

  useEffect(() => updateNodeInternals(id), [id, updateNodeInternals]);

  return (
    <div className="group relative h-full w-full overflow-visible" style={VIDEO_DIRECTOR_NODE_SIZE} onClick={() => setSelectedNode(id)}>
      <Handle type="target" position={Position.Left} id="target" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />
      <Handle type="source" position={Position.Right} id="source" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />
      <NodeHeader className={NODE_HEADER_FLOATING_POSITION_CLASS} icon={<Clapperboard className="h-4 w-4" />}
        titleText={resolveNodeDisplayName(CANVAS_NODE_TYPES.videoDirector, data)} editable
        onTitleChange={(displayName) => updateNodeData(id, { displayName })} />
      <div className={`flex h-full w-full flex-col overflow-hidden rounded-[var(--node-radius)] border ${CANVAS_NODE_INPUT_SURFACE_CLASS} ${canvasNodeFrameClass({ selected })}`}>
        <div className="relative min-h-0 flex-1 bg-black/45">
          {videoUrl ? <video className="nodrag h-full w-full object-contain" src={videoUrl} controls playsInline preload="metadata" />
            : <div className="flex h-full items-center justify-center text-sm text-text-muted">{t('videoDirector.node.empty')}</div>}
        </div>
        <div className="flex items-center justify-between gap-2 px-3 py-2 text-xs text-text-muted">
          <span>{t('videoDirector.node.segments', { count: segments.length })}</span>
          <span>{t('videoDirector.node.duration', { seconds: durationSeconds.toFixed(1) })}</span>
          {onOpenEditor && <button type="button" className="nodrag text-cyan-300" onClick={(event) => {
            event.stopPropagation();
            onOpenEditor(id);
          }}>{t('videoDirector.node.edit')}</button>}
        </div>
      </div>
    </div>
  );
});
