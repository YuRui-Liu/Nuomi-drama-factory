// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useRef, useState } from "react";
import { Library, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ExtensionStyleDrawer } from "@/features/canvas/extension-styles/ExtensionStyleDrawer";
import { getExtensionStyle } from "@/features/canvas/extension-styles/catalog";

export interface ExtensionStyleChipProps {
  readonly value: string | null | undefined;
  readonly onChange: (value: string | null) => void;
  readonly onOpenChange?: (open: boolean) => void;
}

export function ExtensionStyleChip({ value, onChange, onOpenChange }: ExtensionStyleChipProps) {
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
      {style ? (
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
        value={style?.id ?? null}
        onChange={onChange}
        onOpenChange={handleOpenChange}
      />
    </>
  );
}
