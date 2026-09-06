import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Cpu, Image, Loader2, RefreshCw, Server, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  useKnowledgeRuntimeStatus,
  useMediaProviderAccounts,
  useOllamaModels,
  useRunningHubWorkflows,
  useSaveKnowledgeRuntimeSettings,
  useSaveMediaProviderAccount,
  useRecognizeCodex,
  type MediaProviderAccount,
  type RunningHubWorkflowSettings,
} from "@/lib/queries/knowledge-runtime";
import { cn } from "@/lib/utils";
import { TextTaskRoutingPanel } from "@/components/settings/text-task-routing-panel";

function StatusPill({ ready, children }: { ready: boolean; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium",
        ready ? "bg-emerald-500/12 text-emerald-300" : "bg-amber-500/12 text-amber-200",
      )}
    >
      {ready ? <CheckCircle2 className="size-3" /> : <TriangleAlert className="size-3" />}
      {children}
    </span>
  );
}

export function KnowledgeRuntimeSection({ open }: { open: boolean }) {
  const { t } = useTranslation();
  const status = useKnowledgeRuntimeStatus(open);
  const runtime = status.data;
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:11434");
  const [model, setModel] = useState("");
  const [batchSize, setBatchSize] = useState(8);
  const models = useOllamaModels(baseUrl, open);
  const saveRuntime = useSaveKnowledgeRuntimeSettings();
  const recognizeCodex = useRecognizeCodex();

  useEffect(() => {
    if (!runtime) return;
    setBaseUrl(runtime.ollama.baseUrl);
    setModel(runtime.ollama.model);
    setBatchSize(runtime.ollama.batchSize);
  }, [runtime]);

  const modelNames = useMemo(
    () => (models.data ?? []).map((item) => item.name || item.model || "").filter(Boolean),
    [models.data],
  );

  const handleSaveRuntime = async () => {
    if (!baseUrl.trim() || !model.trim()) {
      toast.error(t("settings.runtime.ollamaMissing", { defaultValue: "请先填写 Ollama 地址并选择 Embedding 模型" }));
      return;
    }
    try {
      await saveRuntime.mutateAsync({ baseUrl: baseUrl.trim(), model, batchSize });
      toast.success(t("settings.runtime.ollamaSaved", { defaultValue: "Ollama 测试通过，配置已保存" }));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <section className="space-y-5 px-5 py-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-heading text-base font-medium">运行时与媒体</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            文本与规划任务按下方路由选择 Codex 或模型 API；向量使用本地 Ollama，媒体生产仍由 GRSAI 与 RunningHub 执行。
          </p>
        </div>
        <StatusPill ready={runtime?.ready === true}>
          {status.isLoading
            ? "检查中"
            : status.isError
              ? "后端待重启"
              : runtime?.ready
                ? "知识运行时就绪"
                : "需要配置"}
        </StatusPill>
      </div>

      {runtime?.message ? (
        <div className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-muted-foreground">
          {runtime.message}
        </div>
      ) : null}

      <TextTaskRoutingPanel open={open} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="bg-white/[0.025]">
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2"><Cpu className="size-4" />Codex CLI</CardTitle>
              <StatusPill ready={runtime?.codex.ready === true}>
                {status.isLoading
                  ? "检查中"
                  : status.isError
                    ? "状态不可用"
                    : runtime?.codex.state === "not_installed"
                      ? "未安装"
                      : runtime?.codex.state === "version_unsupported"
                        ? "版本过低"
                        : runtime?.codex.state === "not_authenticated"
                          ? "未登录"
                          : runtime?.codex.state === "ready"
                            ? "可用"
                            : "无法启动"}
              </StatusPill>
            </div>
            <CardDescription>可用于剧本解析、知识抽取、导演规划和 H3 提示词等结构化任务，无需填写额外 API Key。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-md border border-white/8 bg-black/20 px-3 py-2 text-xs">
              <div className="text-muted-foreground">版本</div>
              <div className="mt-1 font-mono">{runtime?.codex.version || "—"}</div>
            </div>
            <div className="rounded-md border border-white/8 bg-black/20 px-3 py-2 text-xs">
              <div className="text-muted-foreground">启动路径</div>
              <div className="mt-1 break-all font-mono">{runtime?.codex.path || "—"}</div>
            </div>
            {runtime?.codex.message ? (
              <p className="text-xs text-muted-foreground">{runtime.codex.message}</p>
            ) : null}
            <div className="flex justify-end">
              <Button type="button" variant="outline" size="sm" onClick={() => recognizeCodex.mutate()} disabled={recognizeCodex.isPending}>
                {recognizeCodex.isPending ? (
                  <Loader2 aria-label="正在识别 Codex" className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="size-3.5" />
                )}
                重新识别
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-white/[0.025]">
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2"><Server className="size-4" />Ollama Embedding</CardTitle>
              <StatusPill ready={runtime?.ollama.configured === true}>
                {runtime?.ollama.configured ? `维度 ${runtime.ollama.dimension}` : "未配置"}
              </StatusPill>
            </div>
            <CardDescription>只用于本地向量化；保存前会真实调用模型并自动记录维度与 digest。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Field label="Ollama 地址">
              <Input aria-label="Ollama 地址" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
            </Field>
            <Field label="Embedding 模型">
              <div className="flex gap-2">
                <select
                  aria-label="Embedding 模型"
                  className="h-9 min-w-0 flex-1 rounded-md border border-input bg-transparent px-3 text-sm"
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                >
                  <option value="">选择已安装模型</option>
                  {model && !modelNames.includes(model) ? <option value={model}>{model}</option> : null}
                  {modelNames.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
                <Button type="button" variant="outline" size="icon-sm" aria-label="刷新 Ollama 模型" onClick={() => models.refetch()}>
                  <RefreshCw className={cn("size-3.5", models.isFetching && "animate-spin")} />
                </Button>
              </div>
            </Field>
            <Field label="批量大小">
              <Input aria-label="批量大小" type="number" min={1} value={batchSize} onChange={(event) => setBatchSize(Math.max(1, Number(event.target.value) || 1))} />
            </Field>
            {runtime?.ollama.digest ? <p className="truncate font-mono text-[10px] text-muted-foreground">digest: {runtime.ollama.digest}</p> : null}
            <div className="flex justify-end">
              <Button type="button" size="sm" onClick={handleSaveRuntime} disabled={saveRuntime.isPending || !model}>
                {saveRuntime.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
                测试并保存
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <MediaProviderSettings open={open} />
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-1.5"><Label className="text-[11px] text-muted-foreground">{label}</Label>{children}</div>;
}

function MediaProviderSettings({ open }: { open: boolean }) {
  const providers = useMediaProviderAccounts(open);
  return (
    <div>
      <div className="mb-3">
        <h4 className="font-heading text-sm font-medium">媒体生产服务</h4>
        <p className="mt-1 text-xs text-muted-foreground">API Key 使用当前 Windows 用户加密存储，服务端不会回显真实 Key。</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <MediaProviderCard open={open} kind="grsai" icon={<Image className="size-4" />} account={(providers.data ?? []).find((item) => item.provider_type === "grsai")} />
        <MediaProviderCard open={open} kind="runninghub" icon={<Server className="size-4" />} account={(providers.data ?? []).find((item) => item.provider_type === "runninghub")} />
      </div>
    </div>
  );
}

function MediaProviderCard({ open, kind, icon, account }: { open: boolean; kind: "grsai" | "runninghub"; icon: React.ReactNode; account?: MediaProviderAccount }) {
  const { t } = useTranslation();
  const save = useSaveMediaProviderAccount();
  const isRunningHub = kind === "runninghub";
  const [baseUrl, setBaseUrl] = useState(isRunningHub ? "https://www.runninghub.cn" : "");
  const [apiKey, setApiKey] = useState("");
  const [concurrency, setConcurrency] = useState(isRunningHub ? 3 : 2);
  const [grsaiModel, setGrsaiModel] = useState("gpt-image-2");
  const workflows = useRunningHubWorkflows(open && isRunningHub);
  const [workflowIds, setWorkflowIds] = useState<RunningHubWorkflowSettings>({
    image_upscale: "",
    video_minimax_h3: "2089723723468328961",
    video_minimax_h3_ref: "2096502793044582401",
    video_minimax_h3_ref_max_images: 5,
    tts_qwen3_voice_design: "",
    tts_indextts2_voice_clone: "",
  });
  const [videoRefMaxImages, setVideoRefMaxImages] = useState("5");

  useEffect(() => {
    if (!account) return;
    setBaseUrl(account.base_url ?? "");
    setConcurrency(account.max_concurrency);
    if (account.model) setGrsaiModel(account.model);
  }, [account]);

  useEffect(() => {
    if (!workflows.data) return;
    setWorkflowIds((current) => ({ ...current, ...workflows.data }));
    setVideoRefMaxImages(String(workflows.data.video_minimax_h3_ref_max_images ?? 5));
  }, [workflows.data]);

  const setWorkflowId = (
    key: Exclude<keyof RunningHubWorkflowSettings, "video_minimax_h3_ref_max_images">,
    value: string,
  ) => {
    setWorkflowIds((current) => ({ ...current, [key]: value }));
  };

  const handleSave = async () => {
    if (!baseUrl.trim() || (!account?.credential_configured && !apiKey.trim())) {
      toast.error("请填写服务地址与 API Key");
      return;
    }
    const normalizedVideoRefMaxImages = videoRefMaxImages.trim();
    const parsedVideoRefMaxImages = Number(normalizedVideoRefMaxImages);
    if (
      isRunningHub
      && (!/^\d+$/.test(normalizedVideoRefMaxImages)
        || !Number.isInteger(parsedVideoRefMaxImages)
        || parsedVideoRefMaxImages < 1
        || parsedVideoRefMaxImages > 10)
    ) {
      toast.error(t("settings.runtime.runningHubVideoRefMaxInvalid", {
        defaultValue: "带 Ref 导演台全局 Ref 上限必须是 1 到 10 的整数",
      }));
      return;
    }
    try {
      await save.mutateAsync({
        id: `${kind}-main`,
        provider_type: kind,
        base_url: baseUrl.trim(),
        model: isRunningHub ? undefined : grsaiModel,
        api_key: apiKey,
        workflows: isRunningHub
          ? { ...workflowIds, video_minimax_h3_ref_max_images: parsedVideoRefMaxImages }
          : undefined,
        max_concurrency: concurrency,
        poll_concurrency: Math.max(concurrency, isRunningHub ? 10 : 4),
        queue_limit: isRunningHub ? 100 : 50,
      });
      setApiKey("");
      toast.success(`${isRunningHub ? "RunningHub" : "GRSAI"} 配置已保存`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <Card className="bg-white/[0.025]">
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2">{icon}{isRunningHub ? "RunningHub" : "GRSAI"}</CardTitle>
          <StatusPill ready={account?.credential_configured === true}>{account?.credential_configured ? "已配置" : "未配置"}</StatusPill>
        </div>
        <CardDescription>{isRunningHub ? "执行图片超分、视频、配音等已发布工作流。" : "执行单图和分镜宫格生成。"}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Field label="服务地址"><Input aria-label={`${isRunningHub ? "RunningHub" : "GRSAI"} 服务地址`} value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://..." /></Field>
        <Field label="API Key">
          <Input
            aria-label={`${isRunningHub ? "RunningHub" : "GRSAI"} API Key`}
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={account?.credential_configured ? "已安全保存；留空表示不修改" : "请输入 API Key"}
          />
        </Field>
        {!isRunningHub ? (
          <Field label="图片模型">
            <Select value={grsaiModel} onValueChange={(value) => setGrsaiModel(value ?? "gpt-image-2")}>
              <SelectTrigger aria-label="GRSAI 图片模型" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[
                  "gpt-image-2", "gpt-image-2-vip", "nano-banana", "nano-banana-fast",
                  "nano-banana-2", "nano-banana-2-cl", "nano-banana-2-2k-cl",
                  "nano-banana-2-4k-cl", "nano-banana-pro", "nano-banana-pro-vt",
                  "nano-banana-pro-cl", "nano-banana-pro-vip", "nano-banana-pro-4k-vip",
                ].map((model) => <SelectItem key={model} value={model}>{model}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
        ) : null}
        <Field label="最大并发"><Input aria-label={`${isRunningHub ? "RunningHub" : "GRSAI"} 最大并发`} type="number" min={1} value={concurrency} onChange={(event) => setConcurrency(Math.max(1, Number(event.target.value) || 1))} /></Field>
        {isRunningHub ? (
          <div className="space-y-3 rounded-lg border border-white/8 bg-black/15 p-3">
            <div>
              <div className="text-xs font-medium">已支持工作流</div>
              <p className="mt-1 text-[11px] text-muted-foreground">只填写 RunningHub 发布后的 Workflow ID；节点映射由内置工作流模板管理。</p>
            </div>
            <WorkflowIdField label="图片超分 Workflow ID" value={workflowIds.image_upscale} onChange={(value) => setWorkflowId("image_upscale", value)} />
            <WorkflowIdField label="MiniMax H3 图生视频 Workflow ID" value={workflowIds.video_minimax_h3} onChange={(value) => setWorkflowId("video_minimax_h3", value)} />
            <WorkflowIdField
              label={t("settings.runtime.runningHubVideoRefWorkflow", {
                defaultValue: "MiniMax H3 带 Ref 导演台 Workflow ID",
              })}
              value={workflowIds.video_minimax_h3_ref}
              onChange={(value) => setWorkflowId("video_minimax_h3_ref", value)}
            />
            <Field label={t("settings.runtime.runningHubVideoRefMaxImages", {
              defaultValue: "带 Ref 导演台全局 Ref 上限",
            })}>
              <Input
                aria-label={t("settings.runtime.runningHubVideoRefMaxImages", {
                  defaultValue: "带 Ref 导演台全局 Ref 上限",
                })}
                type="number"
                min={1}
                max={10}
                step={1}
                value={videoRefMaxImages}
                onChange={(event) => setVideoRefMaxImages(event.target.value)}
              />
            </Field>
            <WorkflowIdField label="Qwen3 音色设计 Workflow ID" value={workflowIds.tts_qwen3_voice_design} onChange={(value) => setWorkflowId("tts_qwen3_voice_design", value)} />
            <WorkflowIdField label="IndexTTS2 声音克隆 Workflow ID" value={workflowIds.tts_indextts2_voice_clone} onChange={(value) => setWorkflowId("tts_indextts2_voice_clone", value)} />
          </div>
        ) : null}
        <div className="flex justify-end"><Button type="button" variant="outline" size="sm" onClick={handleSave} disabled={save.isPending}>{save.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}保存</Button></div>
      </CardContent>
    </Card>
  );
}

function WorkflowIdField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <Field label={label}>
      <Input
        aria-label={label}
        inputMode="numeric"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="未配置"
      />
    </Field>
  );
}
