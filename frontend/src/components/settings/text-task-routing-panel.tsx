import { useEffect, useState } from "react";
import { Bot, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { ModelCombobox } from "@/components/settings/model-combobox";
import { useSettingsSnapshot } from "@/components/settings/settings-draft-context";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  TASK_REASONING_EFFORTS,
  useSaveTaskRuntimeConfig,
  useTaskRuntimeConfig,
  useTaskRuntimeModels,
  type AgentTaskRoute,
  type RuntimePreset,
  type TaskReasoningEffort,
  type TaskRuntimeName,
} from "@/lib/queries/model-gateway";

const DEFAULT_API_MODEL = "deepseek-v4-flash";
const DEFAULT_CODEX_MODEL = "gpt-5.6-sol";
const DEFAULT_WORKBUDDY_MODEL = "default-model";
const DEFAULT_HARNESS_MODEL = "deepseek-v4-flash-vision-exp";

/** 各运行时的内置默认模型；没有记忆值时用它填充。 */
function defaultModelFor(runtime: TaskRuntimeName): string {
  if (runtime === "workbuddy") return DEFAULT_WORKBUDDY_MODEL;
  if (runtime === "codex") return DEFAULT_CODEX_MODEL;
  if (runtime === "deepseek_harness") return DEFAULT_HARNESS_MODEL;
  return DEFAULT_API_MODEL;
}

/** 各运行时的内置默认推理强度；null 表示不指定。 */
function defaultEffortFor(runtime: TaskRuntimeName): TaskReasoningEffort | null {
  if (runtime === "codex") return "medium";
  if (runtime === "deepseek_harness") return "low";
  return null;
}

export function TextTaskRoutingPanel({ open }: { open: boolean }) {
  const query = useTaskRuntimeConfig(open);
  const modelsQuery = useTaskRuntimeModels(open);
  const save = useSaveTaskRuntimeConfig();
  const [routes, setRoutes] = useState<Record<string, AgentTaskRoute>>({});
  // 按运行时记忆模型与推理强度：切换 runtime 时先存下当前行的值，再用目标 runtime 的记忆值填充。
  const [presets, setPresets] = useState<Record<string, RuntimePreset>>({});
  const { dirty, markSaved } = useSettingsSnapshot("task-routing", { routes, presets });

  // 按运行时索引模型下拉清单；后端 models.json 或内置默认。
  const modelCatalog = modelsQuery.data?.data ?? { codex: [], workbuddy: [], model_api: [], deepseek_harness: [] };

  useEffect(() => {
    const data = query.data?.data;
    if (!data?.roles || dirty) return;
    const nextRoutes = Object.fromEntries(data.roles.map((item) => [item.id, item.route]));
    const nextPresets = data.runtime_presets ?? {};
    setRoutes(nextRoutes);
    setPresets(nextPresets);
    markSaved({ routes: nextRoutes, presets: nextPresets });
  }, [query.data]);

  const patchRoute = (id: string, patch: Partial<AgentTaskRoute>) => {
    setRoutes((current) => ({
      ...current,
      [id]: { ...current[id], ...patch },
    }));
  };

  const changeRuntime = (id: string, runtime: TaskRuntimeName) => {
    const current = routes[id];
    if (current?.runtime === runtime) return;
    const nextPresets = { ...presets };
    // harness 行显示的就是 presets.deepseek_harness 本身，没有行私有值需要记忆；
    // 若在这里用 routes[id] 的旧值覆写，会把用户在「统一配置」区的编辑静默回退。
    if (current && current.runtime !== "deepseek_harness") {
      nextPresets[current.runtime] = {
        model: current.model,
        reasoning_effort: current.reasoning_effort,
      };
    }
    const remembered = nextPresets[runtime];
    setPresets(nextPresets);
    patchRoute(id, {
      runtime,
      model: remembered?.model ?? defaultModelFor(runtime),
      reasoning_effort: remembered?.reasoning_effort ?? defaultEffortFor(runtime),
    });
  };

  const handleSave = async () => {
    try {
      await save.mutateAsync({ routes, runtime_presets: presets });
      markSaved({ routes, presets });
      toast.success("任务运行时路由已保存；新建任务将使用新配置");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <Card className="bg-white/[0.025]">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Bot className="size-4" />文本任务路由</CardTitle>
        <CardDescription>
          为每类文本任务选择 Codex、WorkBuddy、DeepSeek Harness 或模型 API。配置在任务入队时冻结，不影响图片、视频和配音供应商。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {query.isLoading ? <p className="text-xs text-muted-foreground">正在加载任务路由…</p> : null}
        {query.isError ? <p className="text-xs text-red-300">任务路由 API 不可用，请重启后端。</p> : null}
        {(query.data?.data.roles ?? []).map((item) => {
          const route = routes[item.id] ?? item.route;
          const usingHarness = route.runtime === "deepseek_harness";
          const harnessPreset = presets.deepseek_harness;
          return (
            <div key={item.id} className="grid gap-3 rounded-lg border border-white/10 bg-black/15 p-3 lg:grid-cols-[minmax(180px,1.2fr)_150px_minmax(180px,1fr)_140px] lg:items-end">
              <div>
                <div className="text-sm font-medium text-foreground">{item.label}</div>
                <div className="mt-1 font-mono text-[10px] text-muted-foreground">{item.id}</div>
              </div>
              <Field label="执行运行时">
                <Select value={route.runtime} onValueChange={(value) => changeRuntime(item.id, value as TaskRuntimeName)}>
                  <SelectTrigger aria-label={`${item.label}执行运行时`}>
                    <SelectValue>{(value: string) =>
                      value === "codex" ? "Codex"
                      : value === "workbuddy" ? "WorkBuddy"
                      : value === "deepseek_harness" ? "DeepSeek Harness"
                      : "模型 API"}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="codex">Codex</SelectItem>
                    <SelectItem value="workbuddy">WorkBuddy</SelectItem>
                    <SelectItem value="deepseek_harness">DeepSeek Harness</SelectItem>
                    <SelectItem value="model_api">模型 API</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label="模型">
                <ModelCombobox
                  ariaLabel={`${item.label}模型`}
                  value={usingHarness ? (harnessPreset?.model ?? DEFAULT_HARNESS_MODEL) : route.model}
                  options={modelCatalog[route.runtime] ?? []}
                  placeholder={defaultModelFor(route.runtime)}
                  disabled={usingHarness}
                  onChange={(next) => patchRoute(item.id, { model: next })}
                />
              </Field>
              <Field label="推理强度">
                <Select
                  value={(usingHarness ? harnessPreset?.reasoning_effort ?? "low" : route.reasoning_effort) ?? "none"}
                  disabled={usingHarness}
                  onValueChange={(value) => patchRoute(item.id, { reasoning_effort: value as TaskReasoningEffort })}
                >
                  <SelectTrigger aria-label={`${item.label}推理强度`}><SelectValue>{(value: string) => value}</SelectValue></SelectTrigger>
                  <SelectContent>
                    {TASK_REASONING_EFFORTS.map((effort) => (
                      <SelectItem key={effort} value={effort}>
                        {effort === "max" ? "max（仅 WorkBuddy）" : effort}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
          );
        })}
        <div className="rounded-lg border border-white/10 bg-black/15 p-3">
          <div className="text-sm font-medium text-foreground">DeepSeek Harness 统一配置</div>
          <div className="mt-1 text-[11px] text-muted-foreground">
            该运行时所有任务角色共用同一组模型与推理强度。
          </div>
          <div className="mt-3 grid gap-3 lg:grid-cols-2">
            <Field label="模型">
              <ModelCombobox
                ariaLabel="DeepSeek Harness 统一模型"
                value={presets.deepseek_harness?.model ?? DEFAULT_HARNESS_MODEL}
                options={modelCatalog.deepseek_harness ?? []}
                placeholder={DEFAULT_HARNESS_MODEL}
                onChange={(next) => setPresets((current) => ({
                  ...current,
                  deepseek_harness: {
                    model: next,
                    reasoning_effort: current.deepseek_harness?.reasoning_effort ?? "low",
                  },
                }))}
              />
            </Field>
            <Field label="推理强度">
              <Select
                value={presets.deepseek_harness?.reasoning_effort ?? "low"}
                onValueChange={(value) => setPresets((current) => ({
                  ...current,
                  deepseek_harness: {
                    model: current.deepseek_harness?.model ?? DEFAULT_HARNESS_MODEL,
                    reasoning_effort: value as TaskReasoningEffort,
                  },
                }))}
              >
                <SelectTrigger aria-label="DeepSeek Harness 统一推理强度">
                  <SelectValue>{(value: string) => value}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {TASK_REASONING_EFFORTS.map((effort) => (
                    <SelectItem key={effort} value={effort}>
                      {effort === "max" ? "max（仅 WorkBuddy）" : effort}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
        </div>
        <div className="flex items-center justify-between gap-3 pt-1">
          <p className="text-[11px] text-muted-foreground">WorkBuddy / Codex 各自的下拉候选模型由后端从 <span className="font-mono">.codebuddy/models.json</span>（项目级优先）或内置默认中拉取，模型 API 的模型名在“兼容网关”渠道中维护；任意字段都允许手动输入自定义 ID。WorkBuddy 可选推理强度（max 为其专属档位，模型 API 会忽略该档位）；DeepSeek Harness 的模型与推理强度由上方统一配置决定，各角色行只读以体现运行时级收口；图片输入的视觉检查请使用 Codex 或模型 API。</p>
          <Button type="button" size="sm" onClick={handleSave} disabled={save.isPending || Object.keys(routes).length === 0}>
            {save.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
            保存任务路由
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-1.5"><Label className="text-[11px] text-muted-foreground">{label}</Label>{children}</div>;
}
