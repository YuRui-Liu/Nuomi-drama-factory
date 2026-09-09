// SPDX-License-Identifier: Elastic-2.0
import { AlertTriangle, Check, ImageOff } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PlannedReferenceBinding, PlannedReferenceStatus } from "@/lib/queries/narrative-groups";

export interface PlannedReferencePickerProps {
  bindings: PlannedReferenceBinding[];
  selectedIds: string[];
  maxImages: number;
  temporaryCount: number;
  onChange: (ids: string[]) => void;
  onResolvePlanning: () => void;
}

type BindingGroup = {
  key: "characters" | "scenes" | "props";
  title: string;
  includes: (binding: PlannedReferenceBinding) => boolean;
};

const bindingGroups: BindingGroup[] = [
  { key: "characters", title: "角色身份", includes: (item) => item.asset_kind === "character_identity" },
  { key: "scenes", title: "场景与变体", includes: (item) => item.asset_kind === "scene_base" || item.asset_kind === "scene_variant" },
  { key: "props", title: "道具", includes: (item) => item.asset_kind === "prop" },
];

function unavailableReason(status: PlannedReferenceStatus) {
  switch (status) {
    case "pending_confirmation": return "待确认规划引用";
    case "missing_asset": return "缺少项目资产";
    case "missing_image": return "缺少资产图片";
    case "ready": return "";
  }
}

export function isPlannedReferenceAvailable(binding: PlannedReferenceBinding) {
  return binding.status === "ready" && Boolean(binding.version_id?.trim());
}

function uniqueReadyIds(bindings: PlannedReferenceBinding[], selectedIds: string[]) {
  const ready = new Set(bindings.filter(isPlannedReferenceAvailable).map((item) => item.binding_id));
  const required = bindings.filter((item) => (
    item.required
    && item.resolution !== "explicit_fallback"
    && isPlannedReferenceAvailable(item)
  )).map((item) => item.binding_id);
  return [...new Set([...required, ...selectedIds])].filter((id) => ready.has(id));
}

function uniqueBindingsById(bindings: PlannedReferenceBinding[]) {
  const seen = new Set<string>();
  return bindings.filter((binding) => {
    if (seen.has(binding.binding_id)) return false;
    seen.add(binding.binding_id);
    return true;
  });
}

export function PlannedReferencePicker({
  bindings,
  selectedIds,
  maxImages,
  temporaryCount,
  onChange,
  onResolvePlanning,
}: PlannedReferencePickerProps) {
  const headingIdPrefix = useId();
  const visibleBindings = uniqueBindingsById(bindings);
  const selected = uniqueReadyIds(visibleBindings, selectedIds);
  const selectedSet = new Set(selected);
  const safeTemporaryCount = Math.max(0, temporaryCount);
  const totalCount = selected.length + safeTemporaryCount;
  const remainingSlots = Math.max(0, maxImages - safeTemporaryCount);
  const atLimit = totalCount >= maxImages;
  const hasRequiredUnavailable = visibleBindings.some((item) => item.required && !isPlannedReferenceAvailable(item));

  const selectGroups = (groups: BindingGroup[]) => {
    const orderedCandidates = groups.flatMap((group) => visibleBindings.filter(
      (item) => group.includes(item) && isPlannedReferenceAvailable(item),
    ));
    const ids = [...new Set([
      ...selected,
      ...orderedCandidates.map((item) => item.binding_id),
    ])].slice(0, remainingSlots);
    onChange(ids);
  };

  const clearGroups = (groups: BindingGroup[]) => {
    const removed = new Set(visibleBindings.filter((item) => (
      (!item.required || item.resolution === "explicit_fallback")
      && groups.some((group) => group.includes(item))
    ))
      .map((item) => item.binding_id));
    onChange(selected.filter((id) => !removed.has(id)));
  };

  return <section aria-label="规划参考图" className="space-y-4 rounded-xl border border-white/20 bg-black/25 p-4 text-foreground">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/15 pb-3">
      <div>
        <h2 className="font-semibold">规划参考图</h2>
        <p aria-live="polite" className="mt-1 text-sm font-medium text-white">
          已选 {totalCount} 张 / 上限 {maxImages} 张
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" aria-label="全选全部规划参考" onClick={() => selectGroups(bindingGroups)}>全选</Button>
        <Button type="button" size="sm" variant="outline" aria-label="清空全部规划参考" onClick={() => clearGroups(bindingGroups)}>清空</Button>
      </div>
    </header>

    {hasRequiredUnavailable ? <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-300/70 bg-amber-300/15 p-3 text-sm text-amber-100">
      <span className="flex items-center gap-2"><AlertTriangle className="size-4" aria-hidden="true" />必需引用尚未就绪，请先返回规划处理。</span>
      <Button type="button" size="sm" variant="outline" aria-label="返回规划处理不可用引用" onClick={onResolvePlanning}>返回规划处理</Button>
    </div> : null}

    {bindingGroups.map((group) => {
      const items = visibleBindings.filter(group.includes);
      if (items.length === 0) return null;
      const headingId = `${headingIdPrefix}-planned-reference-${group.key}`;
      return <section key={group.key} aria-labelledby={headingId} className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 id={headingId} className="text-sm font-semibold text-white">{group.title}</h3>
          <div className="flex gap-2">
            <Button type="button" size="xs" variant="ghost" aria-label={`全选${group.title}`} onClick={() => selectGroups([group])}>全选</Button>
            <Button type="button" size="xs" variant="ghost" aria-label={`清空${group.title}`} onClick={() => clearGroups([group])}>清空</Button>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((binding) => {
            const available = isPlannedReferenceAvailable(binding);
            const isSelected = available && selectedSet.has(binding.binding_id);
            const lockedRequired = available && binding.required && binding.resolution !== "explicit_fallback";
            const disabled = !available || lockedRequired || (!isSelected && atLimit);
            const warning = binding.warning || (binding.status === "ready" && !binding.version_id?.trim()
              ? "缺少有效资产版本"
              : unavailableReason(binding.status));
            return <button
              key={binding.binding_id}
              type="button"
              aria-pressed={isSelected}
              data-selection-state={isSelected ? "selected" : "unselected"}
              disabled={disabled}
              onClick={() => onChange(isSelected
                ? selected.filter((id) => id !== binding.binding_id)
                : [...selected, binding.binding_id])}
              className={cn(
                "relative min-h-32 overflow-hidden rounded-lg border-2 p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300",
                isSelected
                  ? "border-cyan-300 bg-cyan-300/20 text-white"
                  : "border-white/30 bg-white/[0.06] text-zinc-100 hover:border-white/60 hover:bg-white/10",
                disabled && "cursor-not-allowed opacity-65 hover:border-white/30 hover:bg-white/[0.06]",
              )}
            >
              {binding.thumbnail_url ? <img src={binding.thumbnail_url} alt="" className="mb-3 h-20 w-full rounded-md object-cover" /> : <span className="mb-3 flex h-20 items-center justify-center rounded-md bg-black/30 text-zinc-300"><ImageOff className="size-6" aria-hidden="true" /></span>}
              <span className="flex items-start justify-between gap-2">
                <span>
                  <span className="block font-semibold">{binding.display_label}</span>
                  {binding.variant_id ? <span className="mt-0.5 block text-xs text-zinc-300">变体 {binding.variant_id}</span> : null}
                </span>
                <span aria-hidden="true" className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded border-2",
                  isSelected ? "border-cyan-200 bg-cyan-200 text-slate-950" : "border-zinc-300 bg-black/30",
                )}>{isSelected ? <Check data-testid="selected-check" className="size-4 stroke-[3]" /> : null}</span>
              </span>
              <span className={cn("mt-2 block text-xs font-semibold", isSelected ? "text-cyan-100" : "text-zinc-200")}>
                {isSelected ? "已选择" : "未选择"}
              </span>
              {lockedRequired ? <span className="mt-1 block text-xs font-semibold text-lime-200">必选</span> : null}
              {warning ? <span className="mt-2 flex gap-1.5 text-xs text-amber-200"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />{warning}</span> : null}
              {!available && binding.required ? <span className="mt-2 block text-xs font-semibold text-amber-100">必需引用</span> : null}
            </button>;
          })}
        </div>
      </section>;
    })}
  </section>;
}
