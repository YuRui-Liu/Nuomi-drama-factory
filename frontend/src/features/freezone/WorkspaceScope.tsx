import { createContext, useContext, type ReactNode } from 'react';
import type { WorkspaceKind, WorkspaceStatus } from '@/features/creation-workspace/contracts';

export interface WorkspaceScope {
  projectId: string;
  canvasId: string;
  kind: WorkspaceKind;
  episodeId: string | null;
  episodeNumber: number | null;
  status: WorkspaceStatus;
}

const WorkspaceScopeContext = createContext<WorkspaceScope | null>(null);

export function WorkspaceScopeProvider({ value, children }: {
  value: WorkspaceScope | null;
  children: ReactNode;
}) {
  return <WorkspaceScopeContext.Provider value={value}>{children}</WorkspaceScopeContext.Provider>;
}

export function useWorkspaceScope(): WorkspaceScope | null {
  return useContext(WorkspaceScopeContext);
}

export function canEditEpisodeNode(scope: WorkspaceScope | null | undefined, nodeEpisodeNumber: unknown): boolean {
  return Boolean(scope && scope.projectId && scope.canvasId && scope.kind === 'episode'
    && scope.status === 'applied'
    && typeof scope.episodeId === 'string' && scope.episodeId
    && Number.isInteger(scope.episodeNumber) && (scope.episodeNumber ?? 0) > 0
    && Number.isInteger(nodeEpisodeNumber) && nodeEpisodeNumber === scope.episodeNumber);
}

export interface LightweightCanvasScope {
  enabled: boolean;
  projectId: string;
  canvasId: string;
}

const LightweightCanvasContext = createContext<LightweightCanvasScope | null>(null);

export function LightweightCanvasProvider({ value, children }: {
  value: LightweightCanvasScope | null;
  children: ReactNode;
}) {
  return <LightweightCanvasContext.Provider value={value}>{children}</LightweightCanvasContext.Provider>;
}

export function useLightweightCanvas(): LightweightCanvasScope | null {
  return useContext(LightweightCanvasContext);
}
