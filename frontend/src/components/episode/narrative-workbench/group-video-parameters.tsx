// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Button } from "@/components/ui/button";
import type { VideoWorkflowParameterDefinition } from "@/lib/queries/media-models";

const OUTPUT_SIZES = {
  "720p": { megapixels: "0.9", portrait: "736×1280", landscape: "1280×736" },
  "1080p": { megapixels: "2.0", portrait: "1088×1920", landscape: "1920×1088" },
} as const;

export interface GroupVideoParametersProps {
  parameters: VideoWorkflowParameterDefinition[];
  projectDefaults?: Record<string, string>;
  overrides?: Record<string, string>;
  aspectRatio: "9:16" | "16:9";
  busy?: boolean;
  saving?: boolean;
  onSaveOverride: (key: string, value: string) => void | Promise<void>;
  onRestoreDefault: (key: string) => void | Promise<void>;
  onPromoteDefault: (key: string, value: string) => void | Promise<void>;
}

export function GroupVideoParameters({
  parameters,
  projectDefaults = {},
  overrides = {},
  aspectRatio,
  busy = false,
  saving = false,
  onSaveOverride,
  onRestoreDefault,
  onPromoteDefault,
}: GroupVideoParametersProps) {
  const resolution = parameters.find((parameter) => parameter.key === "resolution");
  if (!resolution) return null;

  const projectValue = projectDefaults.resolution ?? resolution.default;
  const overridden = Object.prototype.hasOwnProperty.call(overrides, "resolution");
  const value = overridden ? overrides.resolution : projectValue;
  const disabled = busy || saving;
  const size = OUTPUT_SIZES[value as keyof typeof OUTPUT_SIZES];

  return (
    <div className="rounded-lg border border-white/10 bg-black/10 p-3" data-video-parameters>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <p className="text-xs font-medium text-foreground">{resolution.label}</p>
          <p className="text-[11px] text-muted-foreground">来源：{overridden ? "叙事组覆盖" : "项目默认"}</p>
        </div>
        {size && <p className="text-[11px] text-muted-foreground">{value} ≈ {size.megapixels}MP · {aspectRatio} {aspectRatio === "9:16" ? size.portrait : size.landscape}</p>}
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {resolution.options.map((option) => {
          const optionSize = OUTPUT_SIZES[option.value as keyof typeof OUTPUT_SIZES];
          return (
            <Button
              key={option.value}
              type="button"
              variant={option.value === value ? "secondary" : "outline"}
              className="h-auto justify-start px-3 py-2 text-left"
              disabled={disabled}
              aria-pressed={option.value === value}
              onClick={() => void onSaveOverride("resolution", option.value)}
            >
              <span>
                <span className="block text-xs">{option.label}</span>
                <span className="block text-[11px] font-normal text-muted-foreground">
                  {optionSize ? `≈ ${optionSize.megapixels}MP · ${aspectRatio === "9:16" ? optionSize.portrait : optionSize.landscape}` : option.description}
                </span>
                {option.relative_cost === "higher" && <span className="block text-[11px] font-normal text-amber-400">画质更高，预计耗时和额度增加</span>}
              </span>
            </Button>
          );
        })}
      </div>
      {overridden && <div className="mt-2 flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => void onRestoreDefault("resolution")}>恢复项目默认</Button>
        <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={() => void onPromoteDefault("resolution", value)}>设为项目默认</Button>
      </div>}
    </div>
  );
}
