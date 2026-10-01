// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useReducer, useState } from "react";
import type { KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { BackendStatusError } from "@/lib/api-errors";
import {
  useCommitEpisodeImport,
  usePreviewEpisodeImports,
} from "@/lib/queries/ingest";
import type {
  EpisodeImportPreview,
  EpisodeImportPreviewItem,
  EpisodeImportResolutionAction,
} from "@/types/episode-import";
import type { TaskResponse } from "@/types/api";

type EditableItem = EpisodeImportPreviewItem & {
  action: EpisodeImportResolutionAction | null;
  editedEpisodeNumber: string;
  editableEpisodeNumber: boolean;
  batchDuplicate: boolean;
};

type State = { preview: EpisodeImportPreview | null; items: EditableItem[]; result: TaskResponse | null };
type Action =
  | { type: "preview"; preview: EpisodeImportPreview }
  | { type: "episode"; fileId: string; value: string; conflictKind: "existing" | "batch" | null }
  | { type: "action"; fileId: string; value: "overwrite" | "skip" }
  | { type: "batch"; value: "overwrite" | "skip" }
  | { type: "result"; result: TaskResponse }
  | { type: "reset" };

const initialState: State = { preview: null, items: [], result: null };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "preview":
      return {
        preview: action.preview,
        result: null,
        items: action.preview.files
          .map((file) => ({
            ...file,
            action: file.status === "new" ? "import" as const : null,
            editedEpisodeNumber: file.episode_number?.toString() ?? "",
            editableEpisodeNumber: file.status !== "invalid",
            batchDuplicate: file.warnings?.includes("批次内部集号重复") === true,
          }))
          .sort((a, b) =>
            (a.episode_number ?? Number.MAX_SAFE_INTEGER) -
              (b.episode_number ?? Number.MAX_SAFE_INTEGER) ||
            itemLabel(a).localeCompare(itemLabel(b)),
          ),
      };
    case "episode":
      return {
        ...state,
        items: state.items.map((item) =>
          item.file_id === action.fileId
            ? {
                ...item,
                editedEpisodeNumber: action.value,
                status: action.conflictKind ? "conflict" : /^\d+$/.test(action.value) ? "new" : "needs_episode_number",
                action: action.conflictKind ? null : "import",
                batchDuplicate: action.conflictKind === "batch",
              }
            : item,
        ),
      };
    case "action":
      return {
        ...state,
        items: state.items.map((item) =>
          item.file_id === action.fileId ? { ...item, action: action.value } : item,
        ),
      };
    case "batch":
      return {
        ...state,
        items: state.items.map((item) =>
          item.status === "conflict" && (action.value === "skip" || !item.batchDuplicate) ? { ...item, action: action.value } : item,
        ),
      };
    case "result":
      return { ...state, result: action.result };
    case "reset":
      return initialState;
  }
}

function parsedEpisode(item: EditableItem): number | null {
  if (!/^\d+$/.test(item.editedEpisodeNumber)) return null;
  const episode = Number(item.editedEpisodeNumber);
  return Number.isSafeInteger(episode) && episode > 0 ? episode : null;
}

function itemLabel(item: EpisodeImportPreviewItem): string {
  return item.display_name?.trim() || item.filename;
}

export function EpisodeImportDialog({
  project,
  open,
  onOpenChange,
  onCommitted,
  existingEpisodeNumbers = [],
}: {
  project: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCommitted?: (result: TaskResponse) => void;
  existingEpisodeNumbers?: number[];
}) {
  const previewMutation = usePreviewEpisodeImports(project);
  const commitMutation = useCommitEpisodeImport(project);
  const [state, dispatch] = useReducer(reducer, initialState);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [sourceMode, setSourceMode] = useState<"files" | "text">("files");
  const [text, setText] = useState("");
  const [leavePrompt, setLeavePrompt] = useState(false);
  const { t } = useTranslation();
  const existingEpisodes = useMemo(
    () => new Set(existingEpisodeNumbers),
    [existingEpisodeNumbers],
  );

  useEffect(() => {
    if (!open) {
      dispatch({ type: "reset" });
      setError(null);
      setStep(1);
      setText("");
      setLeavePrompt(false);
    }
  }, [open]);

  const validItems = state.items.filter((item) => item.status !== "invalid");
  const episodes = validItems.filter((item) => item.action !== "skip").map(parsedEpisode).filter((value): value is number => value !== null);
  const hasDuplicate = new Set(episodes).size !== episodes.length;
  const canSubmit =
    validItems.length > 0 &&
    !hasDuplicate &&
    validItems.every((item) => parsedEpisode(item) !== null && item.action !== null);

  const conflicts = useMemo(
    () => state.items.filter((item) => item.status === "conflict"),
    [state.items],
  );

  async function handleFiles(files: FileList | File[] | null) {
    if (!files?.length) return;
    setError(null);
    try {
      const response = await previewMutation.mutateAsync(Array.from(files));
      dispatch({ type: "preview", preview: response.data });
      setStep(2);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("ingest.episodeImport.errors.previewFailed"));
    }
  }

  function requestClose(next: boolean) {
    if (next) return onOpenChange(true);
    if (commitMutation.isPending || previewMutation.isPending) return;
    if (state.preview || text.trim()) setLeavePrompt(true);
    else onOpenChange(false);
  }

  async function handleSubmit() {
    if (!canSubmit || !state.preview) return;
    const resolutions = validItems.map((item) => ({
        file_id: item.file_id,
        episode_number: parsedEpisode(item)!,
        action: item.action!,
      }));
    if (resolutions.every((item) => item.action === "skip")) {
      onOpenChange(false);
      return;
    }
    setError(null);
    try {
      const result = await commitMutation.mutateAsync({
        preview_id: state.preview.preview_id,
        expected_revision: state.preview.base_revision,
        resolutions,
      });
      dispatch({ type: "result", result });
      onCommitted?.(result);
      onOpenChange(false);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : t("ingest.episodeImport.errors.commitFailed");
      const body = cause instanceof BackendStatusError ? cause.body as { detail?: { code?: string }; code?: string } : undefined;
      const code = body?.detail?.code ?? body?.code;
      const key = code === "EPISODE_IMPORT_PREVIEW_STALE" || code === "EPISODE_IMPORT_REVISION_CONFLICT"
        ? "ingest.episodeImport.errors.stale"
        : code === "EPISODE_IMPORT_CONFLICT_UNRESOLVED"
          ? "ingest.episodeImport.errors.unresolved"
          : code === "EPISODE_IMPORT_INVALID_RESOLUTION"
            ? "ingest.episodeImport.errors.invalidResolution"
            : null;
      setError(key ? t(key) : message);
    }
  }

  function chooseConflictAction(
    event: KeyboardEvent<HTMLButtonElement>,
    item: EditableItem,
    current: "overwrite" | "skip",
  ) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const next = current === "overwrite" ? "skip" : "overwrite";
    dispatch({ type: "action", fileId: item.file_id, value: next });
    const group = event.currentTarget.parentElement;
    const targetLabel = t(
      next === "overwrite"
        ? "ingest.episodeImport.overwriteFile"
        : "ingest.episodeImport.skipFile",
      { filename: itemLabel(item) },
    );
    Array.from(group?.querySelectorAll<HTMLElement>(`[role="radio"]`) ?? [])
      .find((button) => button.getAttribute("aria-label") === targetLabel)
      ?.focus();
  }

  return (
    <Dialog open={open} onOpenChange={requestClose}>
      <DialogContent className="max-h-[90dvh] max-w-4xl overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{t("ingest.episodeImport.title")}</DialogTitle>
        </DialogHeader>
        <ol className="flex gap-4 border-b pb-4 text-sm" aria-label="导入步骤">
          {["选择内容", "检查分集与冲突", "确认导入"].map((label, index) => <li key={label} aria-current={step === index + 1 ? "step" : undefined} className={step === index + 1 ? "font-semibold text-primary" : "text-muted-foreground"}>{index + 1}. {label}</li>)}
        </ol>

        <p className="text-sm text-muted-foreground">
          {t("ingest.episodeImport.intentHint")}
        </p>

        {step === 1 && <>
        <div className="flex gap-2"><Button variant={sourceMode === "files" ? "default" : "outline"} onClick={() => setSourceMode("files")}>选择文件</Button><Button variant={sourceMode === "text" ? "default" : "outline"} onClick={() => setSourceMode("text")}>粘贴正文</Button></div>
        {sourceMode === "files" ? <label className="block text-sm font-medium">
          {t("ingest.episodeImport.selectFiles")}
          <Input
            className="mt-2"
            type="file"
            multiple
            accept=".md,.txt,.docx"
            disabled={previewMutation.isPending || commitMutation.isPending}
            onChange={(event) => void handleFiles(event.currentTarget.files)}
          />
        </label> : <label className="space-y-2 text-sm">分集剧本正文<Textarea value={text} onChange={(event) => setText(event.target.value)} className="min-h-48" placeholder="请保留第 1 集、第 2 集等分集标题" /><Button disabled={!text.trim() || previewMutation.isPending} onClick={() => void handleFiles([new File([text], "粘贴剧本.txt", { type: "text/plain" })])}>检查导入内容</Button></label>}
        {previewMutation.isPending && <p role="status" className="text-sm text-muted-foreground">正在检查文件…</p>}
        {state.preview && <Button variant="outline" onClick={() => setStep(2)}>继续检查已有结果</Button>}
        </>}

        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

        {step === 2 && conflicts.length > 1 && (
          <div className="flex gap-2">
            {conflicts.some((item) => !item.batchDuplicate) && <Button type="button" variant="outline" onClick={() => dispatch({ type: "batch", value: "overwrite" })}>{t("ingest.episodeImport.overwriteAll")}</Button>}
            <Button type="button" variant="outline" onClick={() => dispatch({ type: "batch", value: "skip" })}>{t("ingest.episodeImport.skipAll")}</Button>
          </div>
        )}

        {step === 2 && state.items.length > 0 && (
          <div className="max-h-[50vh] space-y-2 overflow-y-auto">
            {state.items.map((item) => (
              <div key={item.file_id} data-testid="episode-import-row" className="grid grid-cols-[minmax(0,1fr)_7rem_minmax(0,1fr)_auto] items-center gap-3 rounded-md border p-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{itemLabel(item)}</p>
                  <p className="truncate text-xs text-muted-foreground">{item.title ?? "—"}</p>
                </div>

                {item.editableEpisodeNumber ? (
                  <label className="text-xs">
                    <span className="sr-only">{t("ingest.episodeImport.episodeNumberFor", { filename: itemLabel(item) })}</span>
                    <Input
                      inputMode="numeric"
                      value={item.editedEpisodeNumber}
                      onChange={(event) => {
                        const value = event.target.value;
                        const episode = /^\d+$/.test(value) ? Number(value) : null;
                        const conflictsBatch = state.items.some((candidate) => candidate.file_id !== item.file_id && parsedEpisode(candidate) === episode && candidate.action !== "skip");
                        dispatch({
                          type: "episode",
                          fileId: item.file_id,
                          value,
                          conflictKind: episode !== null && existingEpisodes.has(episode)
                            ? "existing"
                            : episode !== null && conflictsBatch ? "batch" : null,
                        });
                      }}
                      placeholder={t("ingest.episodeImport.episodeNumber")}
                    />
                  </label>
                ) : (
                  <span className="text-sm">{item.episode_number ? t("ingest.episodeImport.episodeLabel", { number: item.episode_number }) : "—"}</span>
                )}

                <span className={item.status === "invalid" ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
                  {item.error ?? item.warnings?.join(t("ingest.episodeImport.warningSeparator")) ?? t(`ingest.episodeImport.status.${item.status}`)}
                </span>

                {item.status === "conflict" && (
                  <div className="flex gap-1" role="radiogroup" aria-label={`${itemLabel(item)} 冲突处理`}>
                    {!item.batchDuplicate && <Button role="radio" type="button" size="sm" tabIndex={item.action === null || item.action === "overwrite" ? 0 : -1} variant={item.action === "overwrite" ? "default" : "outline"} aria-label={t("ingest.episodeImport.overwriteFile", { filename: itemLabel(item) })} aria-checked={item.action === "overwrite"} onKeyDown={(event) => chooseConflictAction(event, item, "overwrite")} onClick={() => dispatch({ type: "action", fileId: item.file_id, value: "overwrite" })}>{t("ingest.episodeImport.overwrite")}</Button>}
                    <Button role="radio" type="button" size="sm" tabIndex={item.action === "skip" || item.batchDuplicate ? 0 : -1} variant={item.action === "skip" ? "default" : "outline"} aria-label={t("ingest.episodeImport.skipFile", { filename: itemLabel(item) })} aria-checked={item.action === "skip"} onKeyDown={(event) => chooseConflictAction(event, item, "skip")} onClick={() => dispatch({ type: "action", fileId: item.file_id, value: "skip" })}>{t("ingest.episodeImport.skip")}</Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {step === 2 && hasDuplicate && <p role="alert" className="text-sm text-destructive">{t("ingest.episodeImport.duplicate")}</p>}

        {step === 3 && <section className="space-y-4 rounded-lg border p-5">
          <h3 className="font-semibold">本次提交范围</h3>
          <p>新增 {validItems.filter((item) => item.action === "import").length} 集 · 覆盖 {validItems.filter((item) => item.action === "overwrite").length} 集 · 跳过 {validItems.filter((item) => item.action === "skip").length} 集</p>
          {state.items.some((item) => item.status === "invalid") && <p className="text-sm text-destructive">解析失败的文件不会提交，请返回检查后单独修正。</p>}
          {validItems.some((item) => item.action === "overwrite") && <p className="text-sm text-amber-500">覆盖将替换对应集的源文；已有下游内容会标记为待复核。</p>}
          <p className="text-sm text-muted-foreground">按原文解析和校对，使用当前项目配置。提交后可在任务中心查看进度。</p>
        </section>}
        {leavePrompt && <div role="alert" className="space-y-3 rounded-lg border border-amber-500/30 p-4"><p>当前导入内容尚未提交。关闭会丢弃本次预检。</p><div className="flex gap-2"><Button variant="outline" onClick={() => setLeavePrompt(false)}>继续编辑</Button><Button variant="destructive" onClick={() => onOpenChange(false)}>放弃并关闭</Button></div></div>}

        <DialogFooter>
          <Button type="button" variant="outline" disabled={commitMutation.isPending || previewMutation.isPending} onClick={() => requestClose(false)}>{t("common.cancel")}</Button>
          {step > 1 && <Button type="button" variant="outline" disabled={commitMutation.isPending} onClick={() => setStep(step === 3 ? 2 : 1)}>{step === 3 ? "返回检查" : "返回选择"}</Button>}
          {step === 2 && <Button type="button" disabled={!canSubmit} onClick={() => setStep(3)}>{t("ingest.episodeImport.confirm")}</Button>}
          {step === 3 && <Button type="button" disabled={!canSubmit || commitMutation.isPending} onClick={() => void handleSubmit()}>{commitMutation.isPending ? t("ingest.episodeImport.submitting") : "提交导入"}</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
