// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useState } from "react";
import { FileUp, Loader2, Package, Plus, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import "@/components/assets/asset-workspace.css";
import { resolveMediaUrl } from "@/lib/media-url";
import { AssetHeaderActions } from "@/components/assets/asset-header-actions-slot";
import { AssetImportDialog } from "@/components/assets/asset-import-dialog";
import { PropExtractionDialog } from "@/features/script-creation/prop-extraction-dialog";
import { CharacterImageSourceSelect } from "@/components/assets/character-image-source-select";
import { PropAssetCard } from "@/components/assets/prop-asset-card";
import { PropReferenceVersions } from "@/components/assets/prop-reference-versions";
import { AssetBeatReferences } from "@/components/assets/asset-beat-references";
import { CreditCostInline } from "@/components/credit-cost-inline";
import {
  AssetResultCount,
  AssetSearchBox,
  AssetSortSelect,
  filterBySearch,
  sortAssets,
  type AssetSortKey,
} from "@/components/assets/asset-search-box";
import {
  useAssetReferences,
  type BeatReference,
} from "@/lib/queries/asset-references";
import { useGenerationCreditCost } from "@/lib/queries/generation-credit-cost";
import { useAssetImageSourceSelection } from "@/lib/queries/character-image-selection";
import { useAssetFocus } from "@/hooks/use-asset-focus";
import { StageProgressPanel } from "@/components/stage-progress-panel";
import { Button } from "@/components/ui/button";
import { SUBTLE_HEADER_ACTION_BUTTON_CLASS } from "@/components/ui/header-action-styles";
import { HeaderRefreshButton } from "@/components/ui/header-refresh-button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { EMPTY_STATE_ACTION_BUTTON_CLASS } from "@/components/ui/empty-state-styles";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useTaskController } from "@/hooks/use-task-controller";
import { propReferenceAssetScope } from "@/lib/task-scope";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { cn } from "@/lib/utils";
import {
  useBatchGeneratePropReferences,
  useCreateProp,
  useDeleteProp,
  useGeneratePropReferenceAsync,
  useProps,
  useUpdateProp,
  useUploadPropReference,
  type PropPayload,
} from "@/lib/queries/props";
import { openPresetProjectionInMyCanvas } from "@/features/freezone/openPresetProjection";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse } from "@/types/api";
import type { PropAsset } from "@/types/prop";

const PROP_FORM_DEFAULT: PropPayload = {
  name: "",
  prop_type: "object",
  visual_prompt: "",
  description: "",
  owner: "",
};

const PROP_TYPE_VALUES = [
  "weapon",
  "accessory",
  "artifact",
  "document",
  "furniture",
  "object",
] as const;

function isErrorResponse(value: unknown): value is ErrorResponse {
  return Boolean(value && typeof value === "object" && (value as { ok?: unknown }).ok === false);
}

function formatCreditCost(value: number) {
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.?0+$/, "");
}

async function openPropFreezoneCanvas(project: string, propName: string) {
  await openPresetProjectionInMyCanvas(project, {
    scope: "asset",
    asset_kind: "prop",
    asset_id: propName,
  });
}

function PropDialog({
  open,
  initial,
  project,
  references,
  onOpenChange,
  onSubmit,
  saving,
}: {
  open: boolean;
  initial: PropAsset | null;
  project: string;
  references: BeatReference[];
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: PropPayload) => Promise<void>;
  saving: boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<PropPayload>(PROP_FORM_DEFAULT);

  useEffect(() => {
    setDraft(
      initial
        ? {
            name: initial.name,
            aliases: initial.aliases ?? [],
            prop_type: initial.prop_type ?? "object",
            visual_prompt: initial.visual_prompt ?? "",
            description: initial.description ?? "",
            owner: initial.owner ?? "",
            notes: initial.notes ?? "",
          }
        : PROP_FORM_DEFAULT,
    );
  }, [initial, open]);

  const title = initial ? t("assets.props.editProp") : t("assets.props.newProp");
  const PROP_DIALOG_INPUT_CLASS = "h-11 rounded-[8px] border-white/12 bg-white/[0.04] px-3 text-sm placeholder:text-muted-foreground/70 focus-visible:border-white/25 focus-visible:ring-2 focus-visible:ring-white/8 dark:bg-white/[0.04]";
  const PROP_DIALOG_TEXTAREA_CLASS = "rounded-[8px] border-white/12 bg-white/[0.04] px-3 py-2 text-sm placeholder:text-muted-foreground/70 focus-visible:border-white/25 focus-visible:ring-2 focus-visible:ring-white/8 dark:bg-white/[0.04]";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-4 overflow-hidden rounded-2xl border border-white/8 bg-background/68 p-7 shadow-none backdrop-blur-3xl sm:max-w-lg">
        <DialogHeader className="gap-2">
          <DialogTitle className="flex items-center gap-2 text-lg font-medium tracking-tight">
            <span>{title}</span>
          </DialogTitle>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label className="text-sm">{t("assets.props.fields.name")}</Label>
            <Input
              value={draft.name}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, name: event.target.value }))
              }
              className={PROP_DIALOG_INPUT_CLASS}
            />
          </div>
          <div className="grid gap-2">
            <Label className="text-sm">{t("assets.props.fields.type")}</Label>
            <Select
              value={draft.prop_type || "object"}
              onValueChange={(value) =>
                setDraft((prev) => ({ ...prev, prop_type: String(value) }))
              }
            >
              <SelectTrigger className={PROP_DIALOG_INPUT_CLASS}>
                <SelectValue>
                  {t(`assets.props.types.${draft.prop_type || "object"}`, {
                    defaultValue: draft.prop_type || "object",
                  })}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {PROP_TYPE_VALUES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`assets.props.types.${value}`, { defaultValue: value })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label className="text-sm">{t("assets.props.fields.owner")}</Label>
            <Input
              value={draft.owner ?? ""}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, owner: event.target.value }))
              }
              className={PROP_DIALOG_INPUT_CLASS}
            />
          </div>
          <div className="grid gap-2">
            <Label className="text-sm">{t("assets.props.fields.visualPrompt")}</Label>
            <Textarea
              rows={4}
              value={draft.visual_prompt ?? ""}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, visual_prompt: event.target.value }))
              }
              className={PROP_DIALOG_TEXTAREA_CLASS}
            />
          </div>
          {initial && (
            <AssetBeatReferences
              project={project}
              references={references}
              className="border-t border-border/60 pt-4"
            />
          )}
        </div>
        <DialogFooter className="-mx-7 -mb-7 border-t-0 bg-transparent p-7 pt-3 sm:flex-row sm:justify-end">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            className="h-10 w-18 rounded-md border-white/18 bg-white/[0.06] px-0 text-sm font-normal text-foreground/80 hover:border-white/28 hover:bg-white/[0.1] hover:text-foreground"
          >
            {t("common.cancel")}
          </Button>
          <Button
            onClick={() => onSubmit(draft)}
            disabled={saving || !draft.name.trim()}
            className="h-10 w-18 rounded-md bg-primary px-0 text-sm font-normal text-primary-foreground shadow-lg shadow-primary/15 hover:bg-primary/90"
          >
            {saving && <Loader2 className="size-4 animate-spin" />}
            {t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PropAssetCardController({
  view = "reference",
  project,
  prop,
  imageSourceSelection,
  referenceCount,
  onEdit,
  onDelete,
}: {
  view?: string;
  project: string;
  prop: PropAsset;
  imageSourceSelection: string;
  referenceCount?: number;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const generateReference = useGeneratePropReferenceAsync(project, prop.name);
  const uploadReference = useUploadPropReference(project, prop.name);
  const referenceCost = useGenerationCreditCost("fixed_image", "prop_reference");
  const [freezonePending, setFreezonePending] = useState(false);
  const refTask = useTaskController({
    key: {
      taskType: "prop_reference_asset",
      project,
      episode: 0,
      // Must match the BE-hashed scope (see task-scope.ts), else the button
      // loses its loading state after a refresh.
      scope: propReferenceAssetScope(prop.name),
    },
    invalidateKeys: [queryKeys.props(project)],
  });

  async function handleGenerate() {
    try {
      const res = await generateReference.mutateAsync({ model: imageSourceSelection });
      if (isErrorResponse(res)) {
        toast.error(res.error);
        return;
      }
      refTask.start({ scope: res.scope });
    } catch (err) {
      toast.error(backendErrorToastMessage(err, t));
    }
  }

  async function handleOpenFreezone() {
    setFreezonePending(true);
    try {
      await openPropFreezoneCanvas(project, prop.name);
      toast.success(t("assets.props.freezoneOpened"));
    } catch {
      toast.error(t("assets.props.freezoneOpenFailed"));
    } finally {
      setFreezonePending(false);
    }
  }

  async function handleUpload(file: File) {
    const res = await uploadReference.mutateAsync(file);
    if (isErrorResponse(res)) {
      toast.error(res.error);
      return;
    }
    toast.success(t("assets.props.uploadReferenceSuccess"));
  }

  return (
    <div className="space-y-3">
      <div hidden={view !== "reference"}><PropAssetCard
        workspace
        prop={prop}
        generating={generateReference.isPending || refTask.started}
        uploading={uploadReference.isPending}
        referenceCount={referenceCount}
        referenceCost={referenceCost.data?.data.display}
        freezonePending={freezonePending}
        onEdit={onEdit}
        onDelete={onDelete}
        onGenerateReference={handleGenerate}
        onUploadReference={handleUpload}
        onOpenFreezone={handleOpenFreezone}
      /></div>
      {view === "history" && (prop.reference_path ? (
        <PropReferenceVersions
          project={project}
          propName={prop.name}
          legacyAssetPath={prop.reference_path}
        />
      ) : <p className="rounded-lg border p-6 text-sm text-muted-foreground">此道具尚无参考版本，生成或上传后可在这里检查与采用。</p>)}
    </div>
  );
}

export function PropsPanel({
  project,
  focusId,
}: {
  project: string;
  focusId?: string | null;
}) {
  const { t } = useTranslation();
  const props = useProps(project);
  const [selectedName, setSelectedName] = useState<string | null>(focusId ?? null);
  const [detailView, setDetailView] = useState("reference");
  useEffect(() => { if (focusId) setSelectedName(focusId); }, [focusId]);
  const createProp = useCreateProp(project);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [extractOpen, setExtractOpen] = useState(false);
  const [editing, setEditing] = useState<PropAsset | null>(null);
  const updateProp = useUpdateProp(project, editing?.name ?? "");
  const deleteProp = useDeleteProp(project);
  const allItems = props.data?.data ?? [];
  const propRefs = useMemo(
    () => allItems.map((prop) => ({ type: "prop" as const, id: prop.name })),
    [allItems],
  );
  const refIndex = useAssetReferences(
    project,
    propRefs,
    { enabled: allItems.length > 0 },
  );
  const referenceCost = useGenerationCreditCost("fixed_image", "prop_reference");
  const batchGenerate = useBatchGeneratePropReferences(project);
  const imageSourceQuery = useAssetImageSourceSelection(project, "prop");
  const imageSourceSelection = imageSourceQuery.data?.data.image_source_selection ?? "";
  const batchTask = useTaskController({
    key: { taskType: "batch_prop_ref", project, episode: 0 },
    invalidateKeys: [queryKeys.props(project)],
  });
  const missingReferenceCount = useMemo(
    () => allItems.filter((prop) => !prop.reference_url && !prop.reference_path).length,
    [allItems],
  );
  const batchReferenceCost = useMemo(() => {
    const unitCost = referenceCost.data?.data.cost;
    if (!unitCost || missingReferenceCount <= 0) return null;
    return formatCreditCost(unitCost * missingReferenceCount);
  }, [missingReferenceCount, referenceCost.data?.data.cost]);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortKey, setSortKey] = useState<AssetSortKey>("name");
  const items = useMemo(() => {
    const filtered = filterBySearch(allItems, searchQuery, (prop) => [
      prop.name,
      prop.prop_type,
      prop.description,
      prop.visual_prompt,
      prop.owner,
      ...(prop.aliases ?? []),
    ]);
    return sortAssets(
      filtered,
      sortKey === "usage" && refIndex.isError ? "name" : sortKey,
      (prop) => prop.name,
      (prop) => refIndex.referencesFor("prop", prop.name).length,
    );
  }, [allItems, searchQuery, sortKey, refIndex]);
  const gridRef = useAssetFocus(focusId, !props.isLoading && items.length > 0);
  const showBatchTask =
    batchTask.started || batchTask.stream.status !== "idle" || batchTask.logs.length > 0;
  const lastBatchLog = batchTask.logs[batchTask.logs.length - 1];
  const batchLogs =
    lastBatchLog === batchTask.stream.currentTask
      ? batchTask.logs.slice(0, -1)
      : batchTask.logs;

  async function handleSave(data: PropPayload) {
    const payload = { ...data, name: data.name.trim() };
    const res = editing
      ? await updateProp.mutateAsync(payload)
      : await createProp.mutateAsync(payload);
    if (isErrorResponse(res)) {
      toast.error(res.error);
      return;
    }
    setDialogOpen(false);
    setEditing(null);
  }

  async function handleBatchGenerate() {
    const res = await batchGenerate.mutateAsync({ model: imageSourceSelection });
    if (isErrorResponse(res)) {
      toast.error(res.error);
      return;
    }
    if (res.scope) {
      batchTask.start({ scope: res.scope });
    } else {
      batchTask.start();
    }
    toast.success(res.message);
  }

  async function handleDelete(prop: PropAsset) {
    if (!window.confirm(t("assets.props.confirmDelete", { name: prop.name }))) return;
    const res = await deleteProp.mutateAsync(prop.name);
    if (isErrorResponse(res)) {
      toast.error(res.error);
      return;
    }
    toast.success(t("assets.props.deleted"));
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
      <AssetHeaderActions>
        <Button variant="outline" size="sm" onClick={() => setExtractOpen(true)} className={SUBTLE_HEADER_ACTION_BUTTON_CLASS}>从创作道具表提取</Button>
        <CharacterImageSourceSelect project={project} kind="prop" />
        <HeaderRefreshButton
          label={t("common.refresh")}
          onRefresh={async () => {
            const result = await props.refetch();
            if (result.isError) {
              toast.error(t("common.error"));
              return false;
            }
            return true;
          }}
          refreshing={props.isRefetching}
          data-props-refresh
        />
        <Button variant="outline" size="sm" onClick={() => setImportOpen(true)} className={SUBTLE_HEADER_ACTION_BUTTON_CLASS}>
          <FileUp className="size-3.5" />
          {t("assets.import.button.prop")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleBatchGenerate}
          disabled={batchGenerate.isPending}
          className={cn(SUBTLE_HEADER_ACTION_BUTTON_CLASS, "relative")}
        >
          {batchGenerate.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Sparkles className="size-3.5" />
          )}
          {t("assets.props.batchGenerate")}
          <CreditCostInline display={batchReferenceCost} />
        </Button>
        <TooltipProvider delay={80}>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  size="sm"
                  onClick={() => {
                    setEditing(null);
                    setDialogOpen(true);
                  }}
                  className="h-8 gap-1.5 rounded-[8px] bg-primary px-3 text-xs font-normal text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75"
                />
              }
            >
              <Plus className="size-3.5" />
              {t("assets.props.newProp")}
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {t("assets.props.newPropHint")}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </AssetHeaderActions>
      {importOpen ? (
        <AssetImportDialog project={project} assetType="prop" open onOpenChange={setImportOpen} />
      ) : null}
      {extractOpen && <PropExtractionDialog project={project} onClose={() => setExtractOpen(false)} onImported={() => { void props.refetch(); }} />}
      {refIndex.isError ? (
        <p role="alert" className="px-6 pt-3 text-xs text-destructive">
          {t("assets.common.referenceLoadFailed", { defaultValue: "引用加载失败" })}
        </p>
      ) : null}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-4">
        {showBatchTask && (
          <div className="mb-4 overflow-hidden rounded-lg border border-border/70">
            <StageProgressPanel
              title={t("assets.props.batchStatusTitle")}
              currentTask={batchTask.stream.currentTask}
              progress={batchTask.stream.progress}
              logs={batchLogs}
              onStop={batchTask.stop}
              stopping={batchTask.stopping}
            />
          </div>
        )}
        {!props.isLoading && allItems.length > 0 && (
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
              <AssetSearchBox
                value={searchQuery}
                onValueChange={setSearchQuery}
                placeholder={t("assets.common.searchProps")}
                ariaLabel={t("assets.common.searchProps")}
              />
              <AssetSortSelect value={sortKey} onValueChange={setSortKey} />
            </div>
            <AssetResultCount
              resultCount={items.length}
              totalCount={allItems.length}
            />
          </div>
        )}
        {props.isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            {t("common.loading")}
          </div>
        ) : allItems.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <div className="mb-3 flex size-12 items-center justify-center rounded-full border border-border bg-card">
              <Package className="size-5 text-muted-foreground" />
            </div>
            <div>
              <h3 className="mb-1.5 text-sm font-semibold text-foreground">
                {t("assets.props.emptyTitle")}
              </h3>
              <p className="max-w-[15rem] text-xs leading-5 text-muted-foreground">
                {t("assets.props.emptyDescription")}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setEditing(null);
                setDialogOpen(true);
              }}
              className={EMPTY_STATE_ACTION_BUTTON_CLASS}
            >
              <Plus className="size-3.5" />
              {t("assets.props.newProp")}
            </Button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {t("assets.common.noMatch")}
          </div>
        ) : (
          <div ref={gridRef} className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)] gap-5 max-md:grid-cols-[160px_minmax(0,1fr)]">
            <aside aria-label="道具列表" className="space-y-2 overflow-y-auto border-r pr-4">
              {items.map(prop => <button key={prop.name} type="button" aria-label={`选择道具 ${prop.name}`} aria-pressed={(items.some(item => item.name === selectedName) ? selectedName : items[0]?.name) === prop.name} onClick={() => { setSelectedName(prop.name); setDetailView("reference"); }} className="flex w-full items-center gap-3 rounded-lg border border-transparent p-3 text-left aria-pressed:border-primary/60 aria-pressed:bg-primary/5">
                {resolveMediaUrl(prop.reference_url) ? <img src={resolveMediaUrl(prop.reference_url)!} alt="" className="size-12 rounded object-cover" /> : <Package className="size-12 rounded bg-muted p-3 text-muted-foreground" />}
                <span className="min-w-0"><strong className="block truncate text-sm">{prop.name}</strong><small className="text-muted-foreground">{prop.owner || "未绑定角色"} · {prop.reference_url ? "已有参考" : "缺少参考"}</small></span>
              </button>)}
            </aside>
            <main className="min-w-0 overflow-y-auto pr-1">
              <div role="tablist" aria-label="道具工作区" className="asset-workspace-tabs mb-5">
                {[["reference","参考与属性"],["references","引用关系"],["history","历史"]].map(([value,label]) => <button type="button" role="tab" aria-selected={detailView === value} key={value} onClick={() => setDetailView(value)}>{label}</button>)}
              </div>
              {items.filter(prop => prop.name === (items.some(item => item.name === selectedName) ? selectedName : items[0]?.name)).map(prop => <div key={prop.name} data-asset-id={prop.name}>
                <PropAssetCardController project={project} prop={prop} view={detailView} imageSourceSelection={imageSourceSelection} referenceCount={refIndex.isError ? undefined : refIndex.referencesFor("prop", prop.name).length} onEdit={() => { setEditing(prop); setDialogOpen(true); }} onDelete={() => handleDelete(prop)} />
                {detailView === "references" && <section className="rounded-lg border bg-card p-5"><h3 className="mb-4 font-semibold">{prop.name} · 剧情引用</h3><AssetBeatReferences project={project} references={refIndex.referencesFor("prop", prop.name)} /></section>}
              </div>)}
            </main>
          </div>
        )}
      </div>
      <PropDialog
        open={dialogOpen}
        initial={editing}
        project={project}
        references={editing ? refIndex.referencesFor("prop", editing.name) : []}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) setEditing(null);
        }}
        onSubmit={handleSave}
        saving={createProp.isPending || updateProp.isPending}
      />
    </div>
  );
}
