// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useRef, useState } from "react";
import { Library, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ExtensionStyleDrawer } from "@/features/canvas/extension-styles/ExtensionStyleDrawer";
import { getExtensionStyle } from "@/features/canvas/extension-styles/catalog";
import { DirectionReferenceEntry } from '@/features/direction-references/DirectionReferenceEntry';
import { fixedStyleVersionLabel, useFixedNodeStyle } from '@/features/direction-references/useFixedNodeStyle';

export interface ExtensionStyleChipProps {
  readonly value: string | null | undefined;
  readonly onChange: (value: string | null) => void;
  readonly onOpenChange?: (open: boolean) => void;
  readonly ownerNodeId?: string;
}

export function ExtensionStyleChip({ value, onChange, onOpenChange,ownerNodeId }: ExtensionStyleChipProps) {
  const {scope,managed,fixed:fixedStyle}=useFixedNodeStyle(ownerNodeId);
  const [open, setOpen] = useState(false);
  const style = typeof value === "string" ? getExtensionStyle(value) : undefined;
  const fixedStyleVersion = fixedStyleVersionLabel(fixedStyle?.version);
  const fixedStyleLabel = fixedStyle?.state === 'disabled'
    ? '固定风格已禁用'
    : fixedStyle?.title
    ? `固定风格：${fixedStyle.title}${fixedStyleVersion ? ` · ${fixedStyleVersion}` : ''}`
    : fixedStyle?.id
    ? '固定风格：已配置'
    : '风格配置（含自定义）';
  const onOpenChangeRef = useRef(onOpenChange);

  useEffect(() => {
    onOpenChangeRef.current = onOpenChange;
  }, [onOpenChange]);

  useEffect(
    () => () => onOpenChangeRef.current?.(false),
    [],
  );

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      setOpen(nextOpen);
      onOpenChange?.(nextOpen);
    },
    [onOpenChange],
  );

  return (
    <>
      {ownerNodeId&&!scope?<span>画布同步中…</span>:managed&&scope ? <>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={style ? `打开提示词库，当前扩展风格：${style.name}` : '打开提示词库'}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={()=>handleOpenChange(true)}
        >
          <Library aria-hidden />
          提示词库
        </Button>
        <span
          className="max-w-[180px] truncate text-xs text-muted-foreground"
          title={style ? `生成请求会追加扩展风格提示片段：${style.name}` : typeof value === 'string' ? `无法识别的扩展风格 ID：${value}` : '未设置扩展提示词风格'}
        >
          {style ? `${fixedStyle?.title ? '附加提示词风格' : '当前风格'}：${style.name}` : typeof value === 'string' ? '风格无法识别' : fixedStyle?.title ? null : '未选择风格'}
        </span>
        <span title={fixedStyleLabel}>
          <DirectionReferenceEntry
            project={scope.project}
            canvasId={scope.canvasId}
            nodeIds={ownerNodeId?[ownerNodeId]:[]}
            kind="style"
            label={fixedStyleLabel}
          />
        </span>
      </> : scope&&managed===null ? <span>风格加载中…</span> : style ? (
        <div className="inline-flex items-center rounded-md border border-border bg-muted/50 text-xs text-foreground">
          <button
            type="button"
            aria-label={`打开提示词库，当前风格：${style.name}`}
            aria-haspopup="dialog"
            aria-expanded={open}
            className="inline-flex h-7 items-center gap-1.5 rounded-l-md px-2 outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
            onClick={(event) => {
              event.stopPropagation();
              handleOpenChange(true);
            }}
          >
            <Library aria-hidden className="size-3.5" />
            <span>{style.name}</span>
          </button>
          <button
            type="button"
            aria-label={`移除扩展风格：${style.name}`}
            className="inline-flex size-7 items-center justify-center rounded-r-md border-l border-border text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            onClick={(event) => {
              event.stopPropagation();
              onChange(null);
            }}
          >
            <X aria-hidden className="size-3.5" />
          </button>
        </div>
      ) : (
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={(event) => {
            event.stopPropagation();
            handleOpenChange(true);
          }}
        >
          <Library aria-hidden />
          提示词库
        </Button>
      )}

      <ExtensionStyleDrawer
        open={open}
        value={style?.id??null}
        onChange={next=>{if(managed===false||!ownerNodeId)onChange(next);}}
        renderApplication={managed&&scope?()=><p role="note" className="text-xs leading-5 text-muted-foreground">管理画布中只读：此扩展风格仅在生成请求中追加提示片段。固定风格是独立的创作方向设置，不会由此扩展风格自动写入。</p>:undefined}
        onOpenChange={handleOpenChange}
      />
    </>
  );
}
