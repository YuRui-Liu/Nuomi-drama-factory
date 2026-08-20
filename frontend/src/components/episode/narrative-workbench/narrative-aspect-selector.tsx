// SPDX-License-Identifier: Elastic-2.0
import { Button } from "@/components/ui/button";
import type { Orientation } from "@/lib/aspect-ratio";

export function NarrativeAspectSelector({
  orientation,
  saving,
  onChange,
}: {
  orientation: Orientation;
  saving: boolean;
  onChange: (orientation: Orientation) => void;
}) {
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          目标画幅
        </span>
        <div
          role="group"
          aria-label="目标画幅"
          className="inline-flex rounded-md border border-white/10 bg-black/20 p-0.5"
        >
          {(
            [
              ["portrait", "9:16"],
              ["landscape", "16:9"],
            ] as const
          ).map(([value, label]) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={orientation === value ? "secondary" : "ghost"}
              className="h-7 px-3 text-xs"
              aria-pressed={orientation === value}
              disabled={saving}
              onClick={() => onChange(value)}
            >
              {label}
            </Button>
          ))}
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground">
        仅影响后续生成；现有素材需重新生成或重新切分。
      </p>
    </div>
  );
}
