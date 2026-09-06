import { useEffect, useMemo, useRef, useState } from "react";
import { Gauge, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useSaveTaskConcurrency,
  useTaskConcurrency,
  type TaskConcurrencyValues,
  type TaskLane,
} from "@/lib/queries/task-concurrency";

const LANES: TaskLane[] = ["default", "video", "world", "ffmpeg"];

const EMPTY_DRAFT: Record<TaskLane, string> = {
  default: "",
  video: "",
  world: "",
  ffmpeg: "",
};

type ValidationError = "required" | "integer" | "range" | null;

function validateDraft(value: string): ValidationError {
  if (!value.trim()) return "required";
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) return "integer";
  if (parsed < 1 || parsed > 32) return "range";
  return null;
}

export function TaskConcurrencyCard({ open }: { open: boolean }) {
  const { t } = useTranslation();
  const concurrency = useTaskConcurrency(open);
  const saveConcurrency = useSaveTaskConcurrency();
  const [draft, setDraft] = useState<Record<TaskLane, string>>(EMPTY_DRAFT);
  const dirtyLanes = useRef<Record<TaskLane, boolean>>({
    default: false,
    video: false,
    world: false,
    ffmpeg: false,
  });
  const [restartRequired, setRestartRequired] = useState(false);

  useEffect(() => {
    if (!concurrency.data) return;
    setDraft((current) =>
      Object.fromEntries(
        LANES.map((lane) => [
          lane,
          dirtyLanes.current[lane] && !concurrency.data.lanes[lane].managed_by_environment
            ? current[lane]
            : String(concurrency.data.lanes[lane].configured),
        ]),
      ) as Record<TaskLane, string>,
    );
    setRestartRequired(concurrency.data.restart_required);
  }, [concurrency.data]);

  const errors = useMemo(
    () =>
      Object.fromEntries(
        LANES.map((lane) => [
          lane,
          concurrency.data?.lanes[lane].managed_by_environment
            ? null
            : validateDraft(draft[lane]),
        ]),
      ) as Record<TaskLane, ValidationError>,
    [concurrency.data, draft],
  );
  const hasErrors = LANES.some((lane) => errors[lane] !== null);

  const laneLabel = (lane: TaskLane) =>
    t(`settings.taskConcurrency.lanes.${lane}`, {
      defaultValue: {
        default: "普通任务",
        video: "视频任务",
        world: "世界构建",
        ffmpeg: "FFmpeg",
      }[lane],
    });

  const validationMessage = (error: Exclude<ValidationError, null>) =>
    t(`settings.taskConcurrency.validation.${error}`, {
      defaultValue: {
        required: "不能为空",
        integer: "请输入整数",
        range: "请输入 1–32 之间的整数",
      }[error],
    });

  const handleSave = async () => {
    if (!concurrency.data || hasErrors) return;
    const values = Object.fromEntries(
      LANES.map((lane) => [
        lane,
        concurrency.data.lanes[lane].managed_by_environment
          ? concurrency.data.lanes[lane].configured
          : Number(draft[lane]),
      ]),
    ) as TaskConcurrencyValues;

    try {
      const saved = await saveConcurrency.mutateAsync(values);
      dirtyLanes.current = { default: false, video: false, world: false, ffmpeg: false };
      setDraft(
        Object.fromEntries(
          LANES.map((lane) => [lane, String(saved.lanes[lane].configured)]),
        ) as Record<TaskLane, string>,
      );
      setRestartRequired(saved.restart_required);
      toast.success(
        t("settings.taskConcurrency.saved", {
          defaultValue: "配置已保存，重启服务后生效",
        }),
      );
    } catch (error) {
      toast.error(
        error instanceof Error && error.name === "TaskConcurrencyServerError"
          ? error.message
          : t("settings.taskConcurrency.saveFailed", { defaultValue: "保存任务并发设置失败" }),
      );
    }
  };

  return (
    <Card className="bg-white/[0.025]">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <Gauge className="size-4" />
            {t("settings.taskConcurrency.title", { defaultValue: "任务并发" })}
          </CardTitle>
          {restartRequired ? (
            <span className="rounded-full bg-amber-500/12 px-2 py-1 text-[11px] font-medium text-amber-200">
              {t("settings.taskConcurrency.restartPending", { defaultValue: "待重启" })}
            </span>
          ) : null}
        </div>
        <CardDescription>
          {t("settings.taskConcurrency.description", {
            defaultValue: "设置 CE 全局任务并发上限。保存后需要重启服务才会生效。",
          })}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {concurrency.isLoading ? (
          <p className="text-xs text-muted-foreground">
            {t("settings.taskConcurrency.loading", { defaultValue: "正在加载并发设置…" })}
          </p>
        ) : concurrency.isError ? (
          <p className="text-xs text-destructive">
            {t("settings.taskConcurrency.loadFailed", { defaultValue: "无法加载任务并发设置，请重试。" })}
          </p>
        ) : null}

        {concurrency.data
          ? LANES.map((lane) => {
              const status = concurrency.data.lanes[lane];
              const limits = status.running_limits;
              const limitsDiffer = new Set([limits.project, limits.user, limits.executor]).size > 1;
              const error = errors[lane];
              const inputId = `task-concurrency-${lane}`;
              const errorId = `${inputId}-error`;

              return (
                <div
                  key={lane}
                  className="grid gap-2 rounded-lg border border-white/10 bg-black/15 p-3 sm:grid-cols-[minmax(140px,1fr)_110px_minmax(180px,1.4fr)] sm:items-center"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Label htmlFor={inputId}>{laneLabel(lane)}</Label>
                    {status.managed_by_environment ? (
                      <span className="rounded-full bg-sky-500/12 px-2 py-0.5 text-[10px] font-medium text-sky-200">
                        {t("settings.taskConcurrency.managedByEnvironment", {
                          defaultValue: "由环境变量管理",
                        })}
                      </span>
                    ) : null}
                  </div>
                  <div>
                    <Input
                      id={inputId}
                      aria-label={laneLabel(lane)}
                      aria-invalid={Boolean(error)}
                      aria-describedby={error ? errorId : undefined}
                      type="number"
                      min={1}
                      max={32}
                      step={1}
                      value={draft[lane]}
                      disabled={status.managed_by_environment || saveConcurrency.isPending}
                      onChange={(event) => {
                        dirtyLanes.current[lane] = true;
                        setDraft((current) => ({ ...current, [lane]: event.target.value }));
                      }}
                    />
                    {error ? (
                      <p id={errorId} className="mt-1 text-[11px] text-destructive">
                        {validationMessage(error)}
                      </p>
                    ) : null}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    <div>
                      {t("settings.taskConcurrency.currentUsage", {
                        active: status.active,
                        executor: limits.executor,
                        defaultValue: "当前运行 {{active}} / {{executor}}",
                      })}
                    </div>
                    {limitsDiffer ? (
                      <div className="mt-1 text-[11px]">
                        {t("settings.taskConcurrency.limitDetails", {
                          project: limits.project,
                          user: limits.user,
                          executor: limits.executor,
                          defaultValue: "项目 {{project}} · 用户 {{user}} · 执行器 {{executor}}",
                        })}
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })
          : null}

        {hasErrors && concurrency.data ? (
          <p role="alert" className="text-xs text-destructive">
            {t("settings.taskConcurrency.invalidSummary", {
              defaultValue: "请修正标记的并发数后再保存。",
            })}
          </p>
        ) : null}

        <div className="flex justify-end">
          <Button
            type="button"
            size="sm"
            onClick={handleSave}
            disabled={!concurrency.data || hasErrors || saveConcurrency.isPending}
          >
            {saveConcurrency.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {t("settings.taskConcurrency.save", { defaultValue: "保存并发设置" })}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
