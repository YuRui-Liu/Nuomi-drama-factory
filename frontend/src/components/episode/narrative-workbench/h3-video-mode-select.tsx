// SPDX-License-Identifier: Elastic-2.0
import type {
  H3ModeAvailability,
  H3ResolvedVideoMode,
  H3VideoMode,
} from "@/lib/queries/media-models";
import { h3ModeReasonLabel } from "@/lib/queries/media-models";

const MODE_LABELS: Record<H3ResolvedVideoMode, string> = {
  t2va: "T2VA",
  i2va: "I2VA",
  fl2va: "FL2VA",
  l2va: "L2VA",
  ref2va: "Ref2VA",
};

export function h3ModeLabel(mode: H3ResolvedVideoMode) {
  return MODE_LABELS[mode];
}

export function H3VideoModeSelect({ value, availability, disabled = false, onChange }: {
  value: H3VideoMode;
  availability: H3ModeAvailability[];
  disabled?: boolean;
  onChange: (mode: H3VideoMode) => void;
}) {
  const selected = availability.find((item) => item.mode === value);
  return <label className="flex items-center gap-2 text-xs">
    <span className="text-muted-foreground">H3 模式</span>
    <select
      aria-label="H3 视频模式"
      className="h-8 rounded-md border border-input bg-background px-2 text-xs"
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value as H3VideoMode)}
    >
      {availability.map((item) => {
        const label = item.mode === "auto"
          ? `自动（解析为 ${MODE_LABELS[item.resolvedMode]}）`
          : MODE_LABELS[item.resolvedMode];
        const reason = item.reason ? ` · ${h3ModeReasonLabel(item.reason)}` : "";
        return <option key={item.mode} value={item.mode} disabled={!item.available}>
          {label}{reason}
        </option>;
      })}
    </select>
    {selected?.reason ? <span className="text-muted-foreground">
      {h3ModeReasonLabel(selected.reason)}
    </span> : null}
  </label>;
}
