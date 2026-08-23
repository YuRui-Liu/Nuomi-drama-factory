// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useState } from "react";
import { ImageOff, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  EXTENSION_STYLES,
  getExtensionStyle,
  type ExtensionStyle,
  type ExtensionStyleCategory,
} from "@/features/canvas/extension-styles/catalog";
import { cn } from "@/lib/utils";

type CategoryFilter = "all" | ExtensionStyleCategory;

const CATEGORY_FILTERS: ReadonlyArray<{ value: CategoryFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "2d", label: "2D" },
  { value: "3d", label: "3D" },
  { value: "realistic", label: "写实" },
  { value: "chinese", label: "国风" },
  { value: "experimental", label: "实验风格" },
];

const CATEGORY_LABELS: Readonly<Record<ExtensionStyleCategory, string>> = {
  "2d": "2D",
  "3d": "3D",
  realistic: "写实",
  chinese: "国风",
  experimental: "实验风格",
};

export interface ExtensionStyleDrawerProps {
  readonly open: boolean;
  readonly value: string | null | undefined;
  readonly onChange: (value: string | null) => void;
  readonly onOpenChange: (open: boolean) => void;
}

function PreviewFallback({ label }: { readonly label: string }) {
  return (
    <div className="flex h-full min-h-24 items-center justify-center gap-2 bg-muted/60 px-3 text-center text-xs text-muted-foreground">
      <ImageOff aria-hidden className="size-4" />
      <span>{label}</span>
    </div>
  );
}

function StyleCard({
  style,
  selected,
  applied,
  onSelect,
}: {
  readonly style: ExtensionStyle;
  readonly selected: boolean;
  readonly applied: boolean;
  readonly onSelect: () => void;
}) {
  const [previewFailed, setPreviewFailed] = useState(false);
  const accessibleState = [applied ? "已应用" : null, selected ? "已选中" : null]
    .filter(Boolean)
    .join("，");

  return (
    <button
      type="button"
      aria-label={`查看风格：${style.name}${accessibleState ? `，${accessibleState}` : ""}`}
      aria-pressed={selected}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
      className={cn(
        "overflow-hidden rounded-lg border bg-card text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected ? "border-primary ring-1 ring-primary/40" : "border-border hover:border-foreground/30",
      )}
    >
      <div className="aspect-[4/3] overflow-hidden bg-muted">
        {previewFailed ? (
          <PreviewFallback label={`${style.name}预览暂不可用`} />
        ) : (
          <img
            src={style.preview_asset}
            alt={`${style.name}预览`}
            loading="lazy"
            className="h-full w-full object-cover"
            onError={() => setPreviewFailed(true)}
          />
        )}
      </div>
      <div className="space-y-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <span className="font-medium text-foreground">{style.name}</span>
          {applied ? (
            <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
              已应用
            </span>
          ) : null}
        </div>
        <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">{style.summary}</p>
        <div className="flex flex-wrap gap-1.5 text-[10px] text-muted-foreground">
          <span className="rounded bg-muted px-1.5 py-0.5">{CATEGORY_LABELS[style.category]}</span>
          <span className="rounded bg-muted px-1.5 py-0.5">漫剧扩展</span>
        </div>
      </div>
    </button>
  );
}

function StyleDetails({
  style,
  applied,
  onApply,
  detailId,
}: {
  readonly style: ExtensionStyle;
  readonly applied: boolean;
  readonly onApply: () => void;
  readonly detailId: string;
}) {
  const [previewFailed, setPreviewFailed] = useState(false);

  useEffect(() => {
    setPreviewFailed(false);
  }, [style.id]);

  return (
    <section aria-labelledby={detailId} className="space-y-4 p-4">
      <div className="overflow-hidden rounded-lg border border-border bg-muted">
        {previewFailed ? (
          <div className="aspect-[16/9]">
            <PreviewFallback label="详情预览暂不可用" />
          </div>
        ) : (
          <img
            src={style.preview_asset}
            alt={`${style.name}详情预览`}
            loading="lazy"
            className="aspect-[16/9] w-full object-cover"
            onError={() => setPreviewFailed(true)}
          />
        )}
      </div>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id={detailId} className="text-base font-semibold text-foreground">
            {style.name}
          </h2>
          <span className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">
            {CATEGORY_LABELS[style.category]}
          </span>
          <span className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground">漫剧扩展</span>
        </div>
        <p className="text-sm leading-6 text-muted-foreground">{style.summary}</p>
      </div>
      <div>
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">适用场景</h3>
        <div className="flex flex-wrap gap-2">
          {style.use_cases.map((useCase) => (
            <span key={useCase} className="rounded-full border border-border px-2 py-1 text-xs text-foreground">
              {useCase}
            </span>
          ))}
        </div>
      </div>
      <div>
        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">风格片段</h3>
        <p className="rounded-md bg-muted/60 p-3 text-xs leading-5 text-muted-foreground">
          {Object.values(style.prompt_fragment).flat().join(", ")}
        </p>
      </div>
      <p className="text-xs text-muted-foreground">
        来源经离线审核：{style.source.repository}。应用后不会改写输入框中的原提示词。
      </p>
      <div className="sticky bottom-0 bg-popover pt-2">
        <Button type="button" className="w-full" disabled={applied} onClick={onApply}>
          {applied ? "已应用" : "应用风格"}
        </Button>
      </div>
    </section>
  );
}

export function ExtensionStyleDrawer({
  open,
  value,
  onChange,
  onOpenChange,
}: ExtensionStyleDrawerProps) {
  const appliedStyle = typeof value === "string" ? getExtensionStyle(value) : undefined;
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<CategoryFilter>("all");
  const [pendingStyleId, setPendingStyleId] = useState<string | null>(appliedStyle?.id ?? null);
  const [mobileDetailsOpen, setMobileDetailsOpen] = useState(false);

  useEffect(() => {
    if (open) setPendingStyleId(appliedStyle?.id ?? null);
  }, [appliedStyle?.id, open]);

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleStyles = useMemo(
    () =>
      EXTENSION_STYLES.filter(
        (style) =>
          (category === "all" || style.category === category) &&
          `${style.name} ${style.summary} ${style.use_cases.join(" ")}`
            .toLocaleLowerCase()
            .includes(normalizedQuery),
      ),
    [category, normalizedQuery],
  );
  // Pending selection is intentionally resolved only from the filtered result.
  // A hidden card therefore cannot leave behind an actionable stale detail view.
  const pendingStyle = pendingStyleId
    ? visibleStyles.find((style) => style.id === pendingStyleId)
    : undefined;

  useEffect(() => {
    setMobileDetailsOpen(false);
  }, [category, normalizedQuery, open, pendingStyle?.id]);

  return (
    <Sheet open={open} onOpenChange={(nextOpen) => onOpenChange(nextOpen)}>
      <SheetContent
        side="right"
        className="z-[70] flex w-full gap-0 p-0 sm:!max-w-[760px]"
        onClick={(event) => event.stopPropagation()}
        onPointerDown={(event) => event.stopPropagation()}
      >
        {/*
          The popup is a z-70 stacking context. This negative child stays inside
          that context (therefore above the z-60 operations panel) while painting
          behind the drawer surface as its interactive backdrop.
        */}
        <div
          aria-hidden
          data-testid="extension-style-drawer-backdrop"
          className="fixed inset-0 -z-10 bg-black/20 backdrop-blur-xs"
          onPointerDown={(event) => {
            event.stopPropagation();
            onOpenChange(false);
          }}
        />
        <SheetHeader className="shrink-0 border-b border-border pr-12">
          <SheetTitle>漫剧提示词库</SheetTitle>
          <SheetDescription>选择一个扩展风格，仅在生成请求中追加风格片段。</SheetDescription>
        </SheetHeader>

        <div className="shrink-0 space-y-3 border-b border-border p-4">
          <label className="relative block">
            <span className="sr-only">搜索扩展风格</span>
            <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="search"
              aria-label="搜索扩展风格"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索名称、描述或适用场景"
              className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-2 focus:ring-ring/30"
            />
          </label>
          <div role="group" aria-label="扩展风格分类" className="flex flex-wrap gap-2">
            {CATEGORY_FILTERS.map((filter) => (
              <Button
                key={filter.value}
                type="button"
                variant={category === filter.value ? "secondary" : "outline"}
                size="sm"
                aria-label={`筛选分类：${filter.label}`}
                aria-pressed={category === filter.value}
                onClick={() => setCategory(filter.value)}
              >
                {filter.label}
              </Button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="md:grid md:grid-cols-[minmax(0,3fr)_minmax(280px,2fr)]">
            <div>
              <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 md:grid-cols-1 lg:grid-cols-2">
                {visibleStyles.map((style) => (
                  <StyleCard
                    key={style.id}
                    style={style}
                    selected={pendingStyle?.id === style.id}
                    applied={appliedStyle?.id === style.id}
                    onSelect={() => setPendingStyleId(style.id)}
                  />
                ))}
              </div>
              {visibleStyles.length === 0 ? (
                <p className="px-4 py-12 text-center text-sm text-muted-foreground">
                  没有找到匹配的扩展风格
                </p>
              ) : null}
            </div>

            <aside
              data-testid="extension-style-desktop-details"
              className="sticky top-0 hidden max-h-[calc(100dvh-188px)] self-start overflow-y-auto border-l border-border bg-popover md:block"
            >
              {pendingStyle ? (
                <StyleDetails
                  key={pendingStyle.id}
                  detailId="extension-style-desktop-detail-title"
                  style={pendingStyle}
                  applied={appliedStyle?.id === pendingStyle.id}
                  onApply={() => onChange(pendingStyle.id)}
                />
              ) : (
                <p className="p-6 text-sm text-muted-foreground">选择一个可见风格查看详情</p>
              )}
            </aside>
          </div>
        </div>

        {pendingStyle ? (
          <div
            data-testid="extension-style-mobile-actions"
            className="sticky bottom-0 z-10 flex shrink-0 items-center gap-2 border-t border-border bg-popover/95 p-3 shadow-[0_-8px_24px_rgba(0,0,0,0.18)] backdrop-blur md:hidden"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
              {pendingStyle.name}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-expanded={mobileDetailsOpen}
              onClick={() => setMobileDetailsOpen(true)}
            >
              查看详情
            </Button>
            <Button
              type="button"
              size="sm"
              aria-label={appliedStyle?.id === pendingStyle.id ? "移动端已应用" : "立即应用风格"}
              disabled={appliedStyle?.id === pendingStyle.id}
              onClick={() => onChange(pendingStyle.id)}
            >
              {appliedStyle?.id === pendingStyle.id ? "已应用" : "应用风格"}
            </Button>
          </div>
        ) : null}

        {mobileDetailsOpen && pendingStyle ? (
          <div
            data-testid="extension-style-mobile-details"
            className="absolute inset-0 z-20 flex flex-col bg-popover md:hidden"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border p-3">
              <span className="text-sm font-medium text-foreground">风格详情</span>
              <Button type="button" variant="ghost" size="sm" onClick={() => setMobileDetailsOpen(false)}>
                关闭详情
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <StyleDetails
                key={pendingStyle.id}
                detailId="extension-style-mobile-detail-title"
                style={pendingStyle}
                applied={appliedStyle?.id === pendingStyle.id}
                onApply={() => onChange(pendingStyle.id)}
              />
            </div>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
