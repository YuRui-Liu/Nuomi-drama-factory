// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Bot, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  CANVAS_NODE_TYPES,
  type AgentNodeData,
  type CanvasNode,
} from "@/features/canvas/domain/canvasNodes";
import { defaultSettings } from "@/features/script-creation/settings";
import { useCanvasStore } from "@/stores/canvasStore";
import { NodeHeader } from "@/features/canvas/ui/NodeHeader";
import { NodeResizeHandle } from "@/features/canvas/ui/NodeResizeHandle";
import { genres } from "@/features/script-creation/script-setter-data";
import {
  captureAgentCanvasSaveSession,
  IdeationSettings,
  persistAgentSettings,
  type AgentCanvasSaveSession,
} from "@/features/ideation/IdeationSettings";
import "@/features/ideation/ideation-node.css";

type AgentNodeProps = NodeProps<CanvasNode> & {
  id: string;
  data: AgentNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 420;
const MIN_WIDTH = 360;

const ROLE_TRANSLATION_KEYS: Record<string, { name: string; purpose: string }> = {
  "builtin:creative-lead": {
    name: "node.menu.agentCreativeLead",
    purpose: "node.menu.agentCreativeLeadDescription",
  },
  "builtin:text-writer": {
    name: "node.menu.agentTextWriter",
    purpose: "node.menu.agentTextWriterDescription",
  },
  "builtin:ideation": {
    name: "node.menu.agentIdeation",
    purpose: "node.menu.agentIdeationDescription",
  },
};

function getMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export const AgentNode = memo(function AgentNode({
  id,
  data,
  selected = false,
  width,
}: AgentNodeProps) {
  const { t } = useTranslation();
  const storedData = useCanvasStore((state) => {
    const node = state.nodes.find((item) => item.id === id && item.type === CANVAS_NODE_TYPES.agent);
    return node?.data as AgentNodeData | undefined;
  });
  const currentData = storedData ?? data;
  const emptySettings = useMemo(defaultSettings, []);
  const committedSettings = currentData.settings ?? emptySettings;
  const [draftSettings, setDraftSettings] = useState(() => structuredClone(committedSettings));
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSession, setSettingsSession] = useState<AgentCanvasSaveSession | null>(null);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    setDraftSettings(structuredClone(committedSettings));
  }, [committedSettings]);

  const agentRef = currentData.agentRef ?? { id: "builtin:ideation", revision: 1 };
  const role = ROLE_TRANSLATION_KEYS[agentRef.id] ?? ROLE_TRANSLATION_KEYS["builtin:ideation"];
  const title = t(role.name);
  const purpose = t(role.purpose);
  const nodeWidth = Math.max(MIN_WIDTH, Math.round(width ?? DEFAULT_WIDTH));

  const updateDraft = useCallback((key: "genrePrimary" | "genreSecondary" | "idea", value: string) => {
    setDraftSettings((current) => ({ ...current, [key]: value }));
    setSaveError("");
  }, []);

  const saveInlineSettings = useCallback(async () => {
    if (JSON.stringify(draftSettings) === JSON.stringify(committedSettings)) return;
    const session = captureAgentCanvasSaveSession(id, agentRef);
    try {
      await persistAgentSettings(session, draftSettings);
      setSaveError("");
    } catch (error) {
      setSaveError(getMessage(error, t("canvas.agentNode.saveFailure")));
    }
  }, [agentRef, committedSettings, draftSettings, id, t]);

  const openSettings = () => {
    setSettingsSession(captureAgentCanvasSaveSession(id, agentRef));
    setSaveError("");
    setSettingsOpen(true);
  };

  const closeSettings = () => {
    setSettingsOpen(false);
    setSettingsSession(null);
  };

  const secondarySummary = committedSettings.genreSecondary === "不融合"
    ? t("canvas.agentNode.noFusion")
    : committedSettings.genreSecondary || t("canvas.agentNode.unspecified");

  return (
    <div
      className="agent-node-shell"
      data-selected={selected ? "true" : "false"}
      style={{ width: nodeWidth }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-2 !border-[#14161A] !bg-[#E5FF5C]"
      />
      <NodeHeader
        icon={<Bot className="h-4 w-4" />}
        titleText={title}
        metaText={purpose}
        className="agent-node-header"
        iconClassName="agent-node-header-icon"
        titleClassName="agent-node-header-title"
        metaClassName="agent-node-header-meta"
      />

      <div className="agent-node-content nodrag nowheel">
        <div className="agent-node-role">
          <span className="agent-node-role-icon"><Sparkles aria-hidden="true" size={14} /></span>
          <span>{title}</span>
          <span className="agent-node-purpose">{purpose}</span>
        </div>

        <div className="agent-node-fields">
          <label>
            <span>{t("canvas.agentNode.primaryGenre")}</span>
            <select
              className="nodrag nowheel"
              aria-label={t("canvas.agentNode.primaryGenre")}
              value={draftSettings.genrePrimary}
              onChange={(event) => updateDraft("genrePrimary", event.target.value)}
              onBlur={() => void saveInlineSettings()}
            >
              {[...new Set([...genres, draftSettings.genrePrimary])].map((genre) => (
                <option key={genre} value={genre}>{genre}</option>
              ))}
            </select>
          </label>
          <label>
            <span>{t("canvas.agentNode.fusionGenre")}</span>
            <select
              className="nodrag nowheel"
              aria-label={t("canvas.agentNode.fusionGenre")}
              value={draftSettings.genreSecondary}
              onChange={(event) => updateDraft("genreSecondary", event.target.value)}
              onBlur={() => void saveInlineSettings()}
            >
              {[...new Set(["不融合", ...genres, draftSettings.genreSecondary])].map((genre) => (
                <option key={genre} value={genre}>{genre === "不融合" ? t("canvas.agentNode.noFusion") : genre}</option>
              ))}
            </select>
          </label>
          <label className="agent-node-seed">
            <span>{t("canvas.agentNode.storySeed")}</span>
            <textarea
              className="nodrag nowheel"
              aria-label={t("canvas.agentNode.storySeed")}
              value={draftSettings.idea}
              rows={3}
              placeholder={t("canvas.agentNode.storySeedPlaceholder")}
              onChange={(event) => updateDraft("idea", event.target.value)}
              onBlur={() => void saveInlineSettings()}
            />
          </label>
        </div>

        <div className="agent-node-summary" data-testid="agent-settings-summary">
          <span className="agent-node-summary-label">{t("canvas.agentNode.summary")}</span>
          <div className="agent-node-summary-genres">
            <strong>{committedSettings.genrePrimary || t("canvas.agentNode.unspecified")}</strong>
            <span>{secondarySummary}</span>
          </div>
          <p>{committedSettings.idea || t("canvas.agentNode.storySeedPlaceholder")}</p>
        </div>

        {saveError && <p className="agent-node-error" role="alert">{saveError}</p>}
        <button
          type="button"
          className="agent-node-settings-button nodrag"
          onClick={openSettings}
        >
          {t("canvas.agentNode.editSettings")}
        </button>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="!h-2.5 !w-2.5 !border-2 !border-[#14161A] !bg-[#E5FF5C]"
      />
      <NodeResizeHandle visible={selected} minWidth={MIN_WIDTH} minHeight={300} />

      {settingsOpen && (
        <IdeationSettings
          nodeId={id}
          agentRef={agentRef}
          initial={structuredClone(draftSettings)}
          session={settingsSession}
          onClose={closeSettings}
        />
      )}
    </div>
  );
});
