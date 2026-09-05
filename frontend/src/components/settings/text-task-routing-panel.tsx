import { useEffect, useState } from "react";
import { Bot, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  useSaveTaskRuntimeConfig,
  useTaskRuntimeConfig,
  type AgentTaskRoute,
  type TaskReasoningEffort,
  type TaskRuntimeName,
} from "@/lib/queries/model-gateway";

const DEFAULT_API_MODEL = "deepseek-v4-flash";
const DEFAULT_CODEX_MODEL = "gpt-5.6-sol";

export function TextTaskRoutingPanel({ open }: { open: boolean }) {
  const query = useTaskRuntimeConfig(open);
  const save = useSaveTaskRuntimeConfig();
  const [routes, setRoutes] = useState<Record<string, AgentTaskRoute>>({});

  useEffect(() => {
    const roles = query.data?.data.roles;
    if (!roles) return;
    setRoutes(Object.fromEntries(roles.map((item) => [item.id, item.route])));
  }, [query.data]);

  const patchRoute = (id: string, patch: Partial<AgentTaskRoute>) => {
    setRoutes((current) => ({
      ...current,
      [id]: { ...current[id], ...patch },
    }));
  };

  const changeRuntime = (id: string, runtime: TaskRuntimeName) => {
    const current = routes[id];
    patchRoute(id, {
      runtime,
      model:
        runtime === "codex"
          ? current?.model.startsWith("deepseek-") ? DEFAULT_CODEX_MODEL : current?.model || DEFAULT_CODEX_MODEL
          : current?.model.startsWith("gpt-") ? DEFAULT_API_MODEL : current?.model || DEFAULT_API_MODEL,
      reasoning_effort: runtime === "codex" ? current?.reasoning_effort || "medium" : null,
    });
  };

  const handleSave = async () => {
    try {
      await save.mutateAsync(routes);
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
          为每类任务选择 Codex 运行时或普通模型 API。配置在任务入队时冻结，不影响图片、视频和配音供应商。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {query.isLoading ? <p className="text-xs text-muted-foreground">正在加载任务路由…</p> : null}
        {query.isError ? <p className="text-xs text-red-300">任务路由 API 不可用，请重启后端。</p> : null}
        {(query.data?.data.roles ?? []).map((item) => {
          const route = routes[item.id] ?? item.route;
          return (
            <div key={item.id} className="grid gap-3 rounded-lg border border-white/10 bg-black/15 p-3 lg:grid-cols-[minmax(180px,1.2fr)_150px_minmax(180px,1fr)_140px] lg:items-end">
              <div>
                <div className="text-sm font-medium text-foreground">{item.label}</div>
                <div className="mt-1 font-mono text-[10px] text-muted-foreground">{item.id}</div>
              </div>
              <Field label="执行运行时">
                <Select value={route.runtime} onValueChange={(value) => changeRuntime(item.id, value as TaskRuntimeName)}>
                  <SelectTrigger aria-label={`${item.label}执行运行时`}>
                    <SelectValue>{(value: string) => value === "codex" ? "Codex" : "模型 API"}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="codex">Codex</SelectItem>
                    <SelectItem value="model_api">模型 API</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label="模型">
                <Input aria-label={`${item.label}模型`} value={route.model} onChange={(event) => patchRoute(item.id, { model: event.target.value })} />
              </Field>
              <Field label="推理强度">
                <Select
                  value={route.reasoning_effort ?? "none"}
                  onValueChange={(value) => patchRoute(item.id, { reasoning_effort: value as TaskReasoningEffort })}
                  disabled={route.runtime !== "codex"}
                >
                  <SelectTrigger aria-label={`${item.label}推理强度`}><SelectValue>{(value: string) => value}</SelectValue></SelectTrigger>
                  <SelectContent>
                    {(["none", "minimal", "low", "medium", "high", "xhigh"] as const).map((effort) => (
                      <SelectItem key={effort} value={effort}>{effort}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
          );
        })}
        <div className="flex items-center justify-between gap-3 pt-1">
          <p className="text-[11px] text-muted-foreground">模型 API 的地址与 Key 继续在“兼容网关”配置。</p>
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
