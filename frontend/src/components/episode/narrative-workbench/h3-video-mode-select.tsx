// SPDX-License-Identifier: Elastic-2.0
import type {
  H3ModeAvailability,
  H3ResolvedVideoMode,
  H3VideoMode,
} from "@/lib/queries/media-models";

const MODE_LABELS: Record<H3ResolvedVideoMode, string> = {
  t2va: "T2V（文本）",
  i2va: "I2V（首帧）",
  fl2va: "FL2V（首尾帧）",
  l2va: "L2V（尾帧）",
  ref2va: "Ref2V（参考图）",
};

const REASON_LABELS: Record<NonNullable<H3ModeAvailability["reason"]>, string> = {
  missing_input: "缺输入",
  workflow_unverified: "工作流未验证",
  model_unsupported: "模型不支持",
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
        const reason = item.reason ? ` · ${REASON_LABELS[item.reason]}` : "";
        return <option key={item.mode} value={item.mode} disabled={!item.available}>
          {label}{reason}
        </option>;
      })}
    </select>
    {selected?.reason ? <span className="text-muted-foreground">
      {REASON_LABELS[selected.reason]}
    </span> : null}
  </label>;
}
