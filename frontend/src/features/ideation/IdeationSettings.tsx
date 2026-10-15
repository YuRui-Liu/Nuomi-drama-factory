// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { ScriptSetter } from "@/features/script-creation/script-setter";
import type { ScriptSettings } from "@/features/script-creation/types";
import {
  captureFreezoneCanvasRuntime,
  currentFreezoneCanvasScope,
  flushFreezoneCanvasRuntime,
} from "@/features/freezone/canvasSyncRuntime";
import { CANVAS_NODE_TYPES, type AgentNodeData } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";

export interface AgentCanvasSaveSession {
  nodeId: string;
  agentRef: AgentNodeData["agentRef"];
  project: string;
  canvasId: string;
  isCurrent: () => boolean;
  runtime: NonNullable<ReturnType<typeof captureFreezoneCanvasRuntime>>;
}

export function captureAgentCanvasSaveSession(
  nodeId: string,
  agentRef: AgentNodeData["agentRef"],
): AgentCanvasSaveSession | null {
  const scope = currentFreezoneCanvasScope();
  if (!scope) return null;
  const runtime = captureFreezoneCanvasRuntime(scope.project, scope.canvasId);
  if (!runtime) return null;
  const hydrationEpoch = useCanvasStore.getState().canvasHydrationEpoch;
  return {
    nodeId,
    agentRef: { ...agentRef },
    project: scope.project,
    canvasId: scope.canvasId,
    isCurrent: () => scope.isCurrent()
      && runtime.isCurrent()
      && useCanvasStore.getState().canvasHydrationEpoch === hydrationEpoch,
    runtime,
  };
}

export async function persistAgentSettings(
  session: AgentCanvasSaveSession | null,
  settings: ScriptSettings,
): Promise<void> {
  if (!session || !session.isCurrent()) {
    throw new Error("当前画布已切换或尚未就绪，请重新打开设定");
  }

  const findNode = () => useCanvasStore.getState().nodes.find((node) => node.id === session.nodeId);
  const before = findNode();
  const beforeData = before?.data as AgentNodeData | undefined;
  if (
    !before
    || before.type !== CANVAS_NODE_TYPES.agent
    || !beforeData?.agentRef
    || beforeData.agentRef.id !== session.agentRef.id
    || beforeData.agentRef.revision !== session.agentRef.revision
  ) {
    throw new Error("Agent 节点已更改，请重新打开设定");
  }

  const originalSettings = structuredClone(beforeData.settings);
  const attemptedSettings = structuredClone(settings);
  const hasExpectedNode = (node: typeof before | undefined) => {
    const nodeData = node?.data as AgentNodeData | undefined;
    return Boolean(
      node
      && node.type === CANVAS_NODE_TYPES.agent
      && nodeData?.agentRef?.id === session.agentRef.id
      && nodeData.agentRef.revision === session.agentRef.revision,
    );
  };

  const rollbackIfStillOptimistic = () => {
    if (!session.isCurrent()) return;
    useCanvasStore.setState((state) => {
      if (!session.isCurrent()) return state;
      const current = state.nodes.find((node) => node.id === session.nodeId);
      if (!current || !hasExpectedNode(current)) return state;
      const currentData = current.data as AgentNodeData;
      if (JSON.stringify(currentData.settings) !== JSON.stringify(attemptedSettings)) return state;
      return {
        nodes: state.nodes.map((node) => node.id === session.nodeId
          ? { ...node, data: { ...node.data, settings: structuredClone(originalSettings) } }
          : node),
      };
    });
  };

  useCanvasStore.getState().updateNodeData(session.nodeId, { settings: attemptedSettings });

  try {
    const flushed = await flushFreezoneCanvasRuntime(session.project, session.canvasId);
    if (!session.isCurrent()) {
      throw new Error("当前画布已切换，设定尚未确认保存");
    }
    const after = findNode();
    if (!hasExpectedNode(after)) {
      throw new Error("Agent 节点已更改，设定尚未确认保存");
    }
    if (flushed !== true) {
      throw new Error("画布设置保存失败，请检查连接后重试");
    }
  } catch (error) {
    rollbackIfStillOptimistic();
    throw error;
  }
}

interface IdeationSettingsProps {
  nodeId: string;
  agentRef: AgentNodeData["agentRef"];
  initial: ScriptSettings;
  onSave: (settings: ScriptSettings) => Promise<boolean>;
  onClose: () => void;
}

export function IdeationSettings({
  nodeId,
  agentRef,
  initial,
  onSave,
  onClose,
}: IdeationSettingsProps) {
  const save = async (settings: ScriptSettings) => {
    if (await onSave(settings)) onClose();
  };

  return (
    <ScriptSetter
      key={`${nodeId}:${agentRef.id}:${agentRef.revision}`}
      initial={initial}
      onSave={save}
      onClose={onClose}
    />
  );
}
