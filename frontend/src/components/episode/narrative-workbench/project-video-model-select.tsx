// SPDX-License-Identifier: Elastic-2.0
import { Loader2 } from "lucide-react";

import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import type { VideoModelCatalogItem } from "@/lib/queries/media-models";

function unavailableLabel(reason?: string | null) {
  if (reason === "hybrid_input_unverified") return "需完成 RunningHub 验证";
  if (reason === "credential_unavailable") return "RunningHub 凭据不可用";
  if (reason === "workflow_not_configured") return "未配置工作流";
  if (reason === "profile_invalid") return "工作流配置无效";
  if (reason === "provider_not_configured") return "未配置 RunningHub";
  return "暂不可用";
}

export function ProjectVideoModelSelect({ value, models, saving, disabled = false, onChange }: {
  value: string;
  models: VideoModelCatalogItem[];
  saving: boolean;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const selected = models.find((item) => item.id === value);
  if (models.length === 0) {
    return <div className="flex items-center gap-2 rounded-md border border-white/10 bg-black/20 px-3 py-2 text-xs">
      <span className="text-muted-foreground">视频模型</span>
      <span className="font-medium">暂无可用视频模型</span>
    </div>;
  }
  return <div className="flex items-center gap-2 text-xs">
    <span className="text-muted-foreground">视频模型</span>
    <Select value={value} onValueChange={(next) => next && onChange(next)} disabled={saving || disabled}>
      <SelectTrigger aria-label="视频模型" size="sm" className="min-w-48"><SelectValue>{() => selected?.label ?? value}</SelectValue></SelectTrigger>
      <SelectContent align="end">{models.map((model) => <SelectItem key={model.id} value={model.id} disabled={!model.available}>
        <span>{model.label}</span>
        {!model.available && <span className="text-[10px] text-muted-foreground">{unavailableLabel(model.unavailable_reason)}</span>}
      </SelectItem>)}</SelectContent>
    </Select>
    {saving && <Loader2 className="size-3 animate-spin" />}
  </div>;
}
