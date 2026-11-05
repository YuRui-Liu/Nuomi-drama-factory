// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  FileText,
  Film,
  Globe,
  Image,
  LayoutGrid,
  Music,
  Orbit,
  Sparkles,
  Type,
  Upload,
  Video,
  type LucideIcon,
} from "lucide-react";

import {
  CANVAS_NODE_TYPES,
  type CanvasNodeData,
  type CanvasNodeType,
} from "@/features/canvas/domain/canvasNodes";
import { nodeCatalog } from "@/features/canvas/application/nodeCatalog";
import type { MenuIconKey } from "@/features/canvas/domain/nodeRegistry";
import { defaultSettings } from "@/features/script-creation/settings";

export const canvasMenuIconMap: Record<MenuIconKey, LucideIcon> = {
  upload: Upload,
  sparkles: Sparkles,
  layout: LayoutGrid,
  text: Type,
  video: Video,
  audio: Music,
  script: FileText,
  pano360: Globe,
  threeDWorld: Orbit,
  videoCompose: Film,
};

export const CANVAS_ADD_NODE_TYPES: readonly CanvasNodeType[] = [
  CANVAS_NODE_TYPES.screenplay,
  CANVAS_NODE_TYPES.script,
  CANVAS_NODE_TYPES.storyboardGen,
  CANVAS_NODE_TYPES.videoDirector,
  CANVAS_NODE_TYPES.videoCompose,
];

export const CANVAS_AUXILIARY_NODE_TYPES: readonly CanvasNodeType[] = [
  CANVAS_NODE_TYPES.ideation,
  CANVAS_NODE_TYPES.characterVoice,
  CANVAS_NODE_TYPES.textAnnotation,
  CANVAS_NODE_TYPES.beatContext,
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.video,
  CANVAS_NODE_TYPES.musicDesk,
  CANVAS_NODE_TYPES.audio,
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.pano360Viewer,
  CANVAS_NODE_TYPES.threeDWorld,
  CANVAS_NODE_TYPES.agent,
];

const LIGHTWEIGHT_NODE_OPTIONS: readonly { type: CanvasNodeType; label: string; artifactKind: string }[] = [
  { type: CANVAS_NODE_TYPES.ideation, label: '故事立意', artifactKind: 'idea' },
  { type: CANVAS_NODE_TYPES.screenplay, label: '剧本', artifactKind: 'screenplay' },
  { type: CANVAS_NODE_TYPES.upload, label: '资产', artifactKind: 'asset' },
  { type: CANVAS_NODE_TYPES.imageGen, label: '生成资产', artifactKind: 'asset' },
  { type: CANVAS_NODE_TYPES.script, label: '分镜表', artifactKind: 'storyboard' },
  { type: CANVAS_NODE_TYPES.storyboardGen, label: '分镜宫格', artifactKind: 'grid' },
  { type: CANVAS_NODE_TYPES.video, label: '镜头', artifactKind: 'shot' },
  { type: CANVAS_NODE_TYPES.videoDirector, label: '导演', artifactKind: 'director' },
  { type: CANVAS_NODE_TYPES.audio, label: '配音', artifactKind: 'dubbing' },
  { type: CANVAS_NODE_TYPES.musicDesk, label: '配乐', artifactKind: 'music' },
  { type: CANVAS_NODE_TYPES.videoCompose, label: '成片', artifactKind: 'composition' },
];

const AGENT_ROLE_OPTIONS = [
  {
    id: "builtin:creative-lead",
    titleKey: "node.menu.agentCreativeLead",
    descriptionKey: "node.menu.agentCreativeLeadDescription",
  },
  {
    id: "builtin:text-writer",
    titleKey: "node.menu.agentTextWriter",
    descriptionKey: "node.menu.agentTextWriterDescription",
  },
  {
    id: "builtin:ideation",
    titleKey: "node.menu.agentIdeation",
    descriptionKey: "node.menu.agentIdeationDescription",
  },
] as const;

export const CANVAS_MENU_ICON_CELL_CLASS =
  "flex min-w-[58px] max-w-[96px] flex-col items-center gap-1.5 rounded-xl px-2.5 py-2 text-center transition-colors";

export const CANVAS_MENU_ROW_CLASS =
  "flex w-full items-center gap-3 rounded-xl py-2 pl-[17px] pr-2 text-left transition-colors";

interface CanvasMenuSectionHeaderProps {
  label: string;
  className?: string;
}

export function CanvasMenuSectionHeader({
  label,
  className = "",
}: CanvasMenuSectionHeaderProps) {
  return (
    <div className={`text-[15px] font-semibold leading-none text-white/62 ${className}`}>
      {label}
    </div>
  );
}

interface CanvasAddNodeGridProps {
  onSelectNode: (
    type: CanvasNodeType,
    initialData?: Partial<CanvasNodeData>,
    clientPosition?: { x: number; y: number },
  ) => void;
  onItemPointerEnter?: () => void;
  transitionDelayForIndex?: (index: number) => string | undefined;
  lightweightMode?: boolean;
}

export function CanvasAddNodeGrid({
  onSelectNode,
  onItemPointerEnter,
  transitionDelayForIndex,
  lightweightMode = false,
}: CanvasAddNodeGridProps) {
  const { t } = useTranslation();
  const [agentOptionsOpen, setAgentOptionsOpen] = useState(false);
  const visibleOptions: readonly { type: CanvasNodeType; label?: string; artifactKind?: string }[] = lightweightMode
    ? LIGHTWEIGHT_NODE_OPTIONS
    : [...CANVAS_ADD_NODE_TYPES, ...CANVAS_AUXILIARY_NODE_TYPES].map(type => ({ type }));

  return (
    <>
      <div className="grid grid-cols-4 justify-items-center gap-x-2 gap-y-5">
        {visibleOptions.map((option, index) => {
          const type = option.type;
          const definition = nodeCatalog.getDefinition(type);
          if (!definition) return null;
          const Icon = canvasMenuIconMap[definition.menuIcon] ?? Image;
          if (type === CANVAS_NODE_TYPES.agent && !lightweightMode) {
            return (
              <button
                key={type}
                type="button"
                aria-haspopup="true"
                aria-expanded={agentOptionsOpen}
                aria-controls="canvas-agent-role-options"
                onMouseEnter={onItemPointerEnter}
                className={`${CANVAS_MENU_ICON_CELL_CLASS} hover:bg-white/[0.075]`}
                style={{ transitionDelay: transitionDelayForIndex?.(index) }}
                onClick={() => setAgentOptionsOpen((open) => !open)}
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-lime-300/[0.12]">
                  <Icon className="h-4 w-4 text-lime-200" />
                </div>
                <span className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-[13px] leading-5 text-white/82">
                  {option.label ?? t(definition.menuLabelKey)}
                </span>
              </button>
            );
          }
          return (
            <button
              key={option.artifactKind ? `${type}:${option.artifactKind}` : type}
              type="button"
              onMouseEnter={onItemPointerEnter}
              className={`${CANVAS_MENU_ICON_CELL_CLASS} hover:bg-white/[0.075]`}
              style={{ transitionDelay: transitionDelayForIndex?.(index) }}
              onClick={(event) => onSelectNode(
                type,
                option.artifactKind
                  ? { artifactKind: option.artifactKind, displayName: option.label } as Partial<CanvasNodeData>
                  : undefined,
                { x: event.clientX, y: event.clientY },
              )}
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-cyan-300/[0.12]">
                <Icon className="h-4 w-4 text-cyan-200" />
              </div>
              <span className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-[13px] leading-5 text-white/82">
                {option.label ?? t(definition.menuLabelKey)}
              </span>
            </button>
          );
        })}
      </div>
      {!lightweightMode && agentOptionsOpen && (
        <div
          id="canvas-agent-role-options"
          role="group"
          aria-label={t("node.menu.agentOptions")}
          className="mt-4 grid gap-1 rounded-xl border border-white/[0.08] bg-black/20 p-2"
        >
          {AGENT_ROLE_OPTIONS.map((role) => (
            <button
              key={role.id}
              type="button"
              className="flex items-start gap-3 rounded-lg px-3 py-2 text-left transition-colors hover:bg-white/[0.07]"
              onClick={(event) => onSelectNode(
                CANVAS_NODE_TYPES.agent,
                {
                  displayName: t(role.titleKey),
                  agentRef: { id: role.id, revision: 1 },
                  settings: defaultSettings(),
                } as Partial<CanvasNodeData>,
                { x: event.clientX, y: event.clientY },
              )}
            >
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-lime-200" />
              <span className="min-w-0">
                <span className="block text-[13px] leading-5 text-white/85">{t(role.titleKey)}</span>
                <span className="block text-[11px] leading-4 text-white/42">{t(role.descriptionKey)}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}
