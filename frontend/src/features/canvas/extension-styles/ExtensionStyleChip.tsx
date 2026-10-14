// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useRef, useState } from "react";
import { Library, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ExtensionStyleDrawer } from "@/features/canvas/extension-styles/ExtensionStyleDrawer";
import { getExtensionStyle } from "@/features/canvas/extension-styles/catalog";
import { DirectionReferenceEntry,useManagedDirectionProject } from '@/features/direction-references/DirectionReferenceEntry';
import { currentFreezoneCanvasScope } from '@/features/freezone/canvasSyncRuntime';
import { getDirectionBindings } from '@/api/directions';
import { useAuthStore } from '@/stores/auth-store';

export interface ExtensionStyleChipProps {
  readonly value: string | null | undefined;
  readonly onChange: (value: string | null) => void;
  readonly onOpenChange?: (open: boolean) => void;
  readonly ownerNodeId?: string;
}

export function ExtensionStyleChip({ value, onChange, onOpenChange,ownerNodeId }: ExtensionStyleChipProps) {
  const scope=ownerNodeId?currentFreezoneCanvasScope():null;
  const managed=useManagedDirectionProject(scope?.project);
  const username=useAuthStore(s=>s.username);
  const fixedKey=`${username}:${scope?.project}:${scope?.canvasId}:${ownerNodeId}`;
  const [fixed,setFixed]=useState<{key:string;id?:string;title?:string;version?:string}|null>(null);
  useEffect(()=>{let live=true;const load=()=>{if(managed&&scope&&ownerNodeId)void getDirectionBindings(scope.project,{level:'node',canvas_id:scope.canvasId,node_id:ownerNodeId}).then(value=>{const effect=value.effective.style;if(live)setFixed({key:fixedKey,id:typeof effect.configuration?.style_id==='string'?effect.configuration.style_id:undefined,title:effect.title,version:effect.version});}).catch(()=>{});};load();window.addEventListener('direction-config-changed',load);return()=>{live=false;window.removeEventListener('direction-config-changed',load);};},[fixedKey,managed]);
  const fixedStyle=fixed?.key===fixedKey?fixed:null;
  const [open, setOpen] = useState(false);
  const style = typeof value === "string" ? getExtensionStyle(value) : undefined;
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
      {ownerNodeId&&!scope?<span>画布同步中…</span>:managed&&scope ? <><Button type="button" variant="outline" size="sm" onClick={()=>handleOpenChange(true)}>风格库</Button><DirectionReferenceEntry project={scope.project} canvasId={scope.canvasId} nodeIds={ownerNodeId?[ownerNodeId]:[]} kind="style" label={fixedStyle?.title?`${fixedStyle.title} · v${fixedStyle.version}`:'固定风格配置（含自定义）'}/></> : scope&&managed===null ? <span>风格加载中…</span> : style ? (
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
        value={managed?fixedStyle?.id??null:style?.id??null}
        onChange={next=>{if(managed===false||!ownerNodeId)onChange(next);}}
        renderApplication={managed&&scope?id=><DirectionReferenceEntry project={scope.project} canvasId={scope.canvasId} nodeIds={ownerNodeId?[ownerNodeId]:[]} kind="style" selectedId={id} label="配置固定风格"/>:undefined}
        onOpenChange={handleOpenChange}
      />
    </>
  );
}
