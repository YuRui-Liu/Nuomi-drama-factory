// SPDX-License-Identifier: Elastic-2.0
import { Loader2 } from "lucide-react";

import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import type { VideoModelCatalogItem } from "@/lib/queries/media-models";

export function ProjectVideoModelSelect({ value, models, saving, onChange }: {
  value: string;
  models: VideoModelCatalogItem[];
  saving: boolean;
  onChange: (value: string) => void;
}) {
  const selected = models.find((item) => item.id === value);
  if (models.length <= 1) {
    return <div className="flex items-center gap-2 rounded-md border border-white/10 bg-black/20 px-3 py-2 text-xs">
      <span className="text-muted-foreground">视频模型</span>
      <span className="font-medium">{models[0]?.label ?? "暂无可用视频模型"}</span>
    </div>;
  }
  return <div className="flex items-center gap-2 text-xs">
    <span className="text-muted-foreground">视频模型</span>
    <Select value={value} onValueChange={(next) => next && onChange(next)} disabled={saving}>
      <SelectTrigger aria-label="视频模型" size="sm" className="min-w-48"><SelectValue>{() => selected?.label ?? value}</SelectValue></SelectTrigger>
      <SelectContent align="end">{models.map((model) => <SelectItem key={model.id} value={model.id}>{model.label}</SelectItem>)}</SelectContent>
    </Select>
    {saving && <Loader2 className="size-3 animate-spin" />}
  </div>;
}
