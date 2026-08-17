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
  return <div className="flex items-center gap-2 text-xs">
    <span className="text-muted-foreground">项目默认视频模型</span>
    <Select value={value} onValueChange={(next) => next && onChange(next)} disabled={saving}>
      <SelectTrigger size="sm" className="min-w-48"><SelectValue>{() => selected?.label ?? value}</SelectValue></SelectTrigger>
      <SelectContent align="end">{models.map((model) => <SelectItem key={model.id} value={model.id} disabled={!model.available}>{model.label}{!model.available ? ` · ${model.unavailable_reason || "未配置"}` : ""}</SelectItem>)}</SelectContent>
    </Select>
    {saving && <Loader2 className="size-3 animate-spin" />}
  </div>;
}
