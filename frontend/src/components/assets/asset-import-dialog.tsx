import { useEffect, useState } from "react";
import { AlertTriangle, FileUp, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { assetImportDispositionKey, assetImportResultToastValues, useConfirmAssetImport, usePreviewAssetImport } from "@/lib/queries/asset-imports";
import type { AssetImportPreview, AssetImportType } from "@/types/asset-import";

export function renderAssetImportValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) {
    return value.length ? value.map(renderAssetImportValue).join("、") : "—";
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function AssetImportDialog({ project, assetType, open, onOpenChange }: {
  project: string;
  assetType: AssetImportType;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const previewMutation = usePreviewAssetImport(project, assetType);
  const confirmMutation = useConfirmAssetImport(project, assetType);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<AssetImportPreview | null>(null);

  useEffect(() => {
    if (!open) {
      setFile(null);
      setPreview(null);
      previewMutation.reset();
      confirmMutation.reset();
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadPreview() {
    if (!file) return;
    if (!/\.(md|txt)$/i.test(file.name)) {
      toast.error(t("assets.import.invalidFileType"));
      return;
    }
    try {
      setPreview(await previewMutation.mutateAsync(file));
    } catch (error) {
      toast.error(backendErrorToastMessage(error, t));
    }
  }

  async function confirm() {
    if (!preview) return;
    try {
      const result = await confirmMutation.mutateAsync(preview.import_id);
      toast.success(t("assets.import.actualResult", assetImportResultToastValues(result)));
      onOpenChange(false);
    } catch (error) {
      toast.error(backendErrorToastMessage(error, t));
    }
  }

  const busy = previewMutation.isPending || confirmMutation.isPending;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader><DialogTitle>{t(`assets.import.title.${assetType}`)}</DialogTitle></DialogHeader>
        {!preview ? (
          <div className="grid gap-4 py-2">
            <p className="text-sm text-muted-foreground">{t("assets.import.help")}</p>
            <label htmlFor="asset-import-file" className="text-sm font-medium">
              {t("assets.import.fileLabel")}
            </label>
            <Input id="asset-import-file" type="file" accept=".md,.txt,text/markdown,text/plain" disabled={busy}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </div>
        ) : (
          <div className="grid gap-3 py-2">
            <div className="grid grid-cols-3 gap-2 text-center text-sm">
              <div className="rounded-md border p-2"><strong>{preview.created_count ?? 0}</strong><br />{t("assets.import.created")}</div>
              <div className="rounded-md border p-2"><strong>{preview.supplemented_count ?? 0}</strong><br />{t("assets.import.supplemented")}</div>
              <div className="rounded-md border p-2"><strong>{preview.skipped_count ?? 0}</strong><br />{t("assets.import.skipped")}</div>
            </div>
            {preview.diffs.map((diff) => (
              <section key={diff.name} className="rounded-md border p-3">
                <div className="flex items-center justify-between gap-2"><strong>{diff.name}</strong><span className="text-xs text-muted-foreground">{t(`assets.import.disposition.${assetImportDispositionKey(diff.disposition)}`)}</span></div>
                <div className="mt-2 grid gap-1 text-xs">
                  {diff.changes.map((field) => (
                    <div key={field.field} className="grid grid-cols-[8rem_1fr] gap-2 border-t pt-1">
                      <span className="text-muted-foreground">{field.field}</span>
                      <span className="flex flex-wrap items-center gap-1">
                        <span>{renderAssetImportValue(field.current)}</span>
                        <span aria-hidden="true">→</span>
                        <span>{renderAssetImportValue(field.proposed)}</span>
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          {t(`assets.import.fieldDisposition.${field.disposition}`)}
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
                {!!diff.warnings?.length && (
                  <div className="mt-2 grid gap-1 text-xs text-amber-600 dark:text-amber-400">
                    {diff.warnings.map((warning) => (
                      <p key={warning} className="flex items-start gap-1.5">
                        <AlertTriangle className="mt-0.5 size-3 shrink-0" />
                        <span>{warning}</span>
                      </p>
                    ))}
                  </div>
                )}
              </section>
            ))}
            {!!preview.warnings?.length && <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm"><div className="mb-1 flex items-center gap-2"><AlertTriangle className="size-4" />{t("assets.import.warnings")}</div>{preview.warnings.map((warning) => <p key={warning} className="text-muted-foreground">{warning}</p>)}</div>}
            {!!preview.ignored_sections?.length && <p className="text-xs text-muted-foreground">{t("assets.import.ignored")}: {preview.ignored_sections.join("、")}</p>}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
          {!preview ? <Button disabled={!file || busy} onClick={loadPreview}>{previewMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <FileUp className="size-4" />}{t("assets.import.preview")}</Button>
            : <Button disabled={busy} onClick={confirm}>{confirmMutation.isPending && <Loader2 className="size-4 animate-spin" />}{t("assets.import.confirm")}</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
