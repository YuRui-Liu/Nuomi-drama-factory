// SPDX-License-Identifier: Elastic-2.0
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface StyleChangeDecision {
  styleId: string | null;
  action: "restyle" | "redirect";
}

export function StyleChangeDialog({
  open,
  currentStyleId,
  availableStyles,
  saving = false,
  onOpenChange,
  onApply,
}: {
  open: boolean;
  currentStyleId: string | null;
  availableStyles: Array<{ id: string; label: string }>;
  saving?: boolean;
  onOpenChange: (open: boolean) => void;
  onApply: (decision: StyleChangeDecision) => void | Promise<void>;
}) {
  const [styleId, setStyleId] = useState(currentStyleId ?? availableStyles[0]?.id ?? "");
  useEffect(() => {
    if (open) setStyleId(currentStyleId ?? availableStyles[0]?.id ?? "");
  }, [availableStyles, currentStyleId, open]);

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>修改叙事组风格</DialogTitle>
        <DialogDescription>
          仅换画风保留叙事结构；重新导演会按新风格生成待审核版本。
        </DialogDescription>
      </DialogHeader>
      <label className="space-y-1 text-sm">
        <span className="block text-muted-foreground">叙事组风格</span>
        <select
          aria-label="叙事组风格"
          className="h-9 w-full rounded-md border border-input bg-background px-3"
          value={styleId}
          onChange={(event) => setStyleId(event.target.value)}
        >
          {availableStyles.map((style) => <option key={style.id} value={style.id}>{style.label}</option>)}
        </select>
      </label>
      <DialogFooter className="p-0">
        <Button type="button" variant="ghost" disabled={saving} onClick={() => void onApply({ styleId: null, action: "restyle" })}>恢复项目默认</Button>
        <Button type="button" variant="outline" disabled={saving || !styleId} onClick={() => void onApply({ styleId, action: "restyle" })}>仅换画风</Button>
        <Button type="button" disabled={saving || !styleId} onClick={() => void onApply({ styleId, action: "redirect" })}>按新风格重新导演</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
