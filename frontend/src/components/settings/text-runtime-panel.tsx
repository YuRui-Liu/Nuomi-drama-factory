import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  type TextRuntimeProvider,
  useSaveTextRuntimeConfig,
  useTextRuntimeConfig,
} from "@/lib/queries/model-gateway";

const PRESETS: Record<TextRuntimeProvider, { baseUrl: string; model: string }> = {
  deepseek: {
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-v4-flash",
  },
  dramaclaw: {
    baseUrl: "https://relayclaw.cdnfg.com/v1",
    model: "DC-scene-builder-LLM",
  },
  openai_compatible: { baseUrl: "", model: "" },
};

export function TextRuntimePanel({ enabled = true }: { enabled?: boolean }) {
  const query = useTextRuntimeConfig(enabled);
  const save = useSaveTextRuntimeConfig();
  const runtime = query.data?.data;
  const [provider, setProvider] = useState<TextRuntimeProvider>("deepseek");
  const [baseUrl, setBaseUrl] = useState(PRESETS.deepseek.baseUrl);
  const [model, setModel] = useState(PRESETS.deepseek.model);
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    if (!runtime) return;
    setProvider(runtime.provider);
    setBaseUrl(runtime.baseUrl);
    setModel(runtime.model);
  }, [runtime?.provider, runtime?.baseUrl, runtime?.model]);

  const changeProvider = (next: TextRuntimeProvider) => {
    const previousPreset = PRESETS[provider];
    const nextPreset = PRESETS[next];
    setBaseUrl((current) =>
      !current.trim() || current === previousPreset.baseUrl ? nextPreset.baseUrl : current,
    );
    setModel((current) =>
      !current.trim() || current === previousPreset.model ? nextPreset.model : current,
    );
    setProvider(next);
  };

  const handleSave = async () => {
    const cleanBaseUrl = baseUrl.trim();
    const cleanModel = model.trim();
    if (!cleanBaseUrl || !cleanModel) {
      toast.error("Base URL 和模型名称不能为空");
      return;
    }
    try {
      const result = await save.mutateAsync({
        provider,
        baseUrl: cleanBaseUrl,
        model: cleanModel,
        ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}),
      });
      if (!result.ok) {
        toast.error(result.error || "保存普通文本模型失败");
        return;
      }
      setApiKey("");
      toast.success("普通文本模型已保存，下一个任务立即生效");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存普通文本模型失败");
    }
  };

  return (
    <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.025] p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-sm font-medium text-foreground">普通文本模型</h4>
          <p className="mt-1 text-xs text-muted-foreground">
            用于角色、场景、剧本分析和提示词等文本任务，不影响图片、视频和 Embedding。
          </p>
        </div>
        <span className={runtime?.configured ? "text-xs text-emerald-400" : "text-xs text-amber-400"}>
          {runtime?.configured ? "已配置" : "未配置 Key"}
        </span>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="text-runtime-provider">供应商</Label>
          <Select value={provider} onValueChange={(value) => changeProvider(value as TextRuntimeProvider)}>
            <SelectTrigger id="text-runtime-provider"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="deepseek">DeepSeek</SelectItem>
              <SelectItem value="dramaclaw">DramaClawAPI</SelectItem>
              <SelectItem value="openai_compatible">OpenAI 兼容</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="text-runtime-model">模型</Label>
          <Input id="text-runtime-model" value={model} onChange={(event) => setModel(event.target.value)} />
        </div>
        <div className="space-y-1.5 md:col-span-2">
          <Label htmlFor="text-runtime-base-url">Base URL</Label>
          <Input id="text-runtime-base-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
        </div>
        <div className="space-y-1.5 md:col-span-2">
          <Label htmlFor="text-runtime-api-key">API Key</Label>
          <Input
            id="text-runtime-api-key"
            type="password"
            value={apiKey}
            placeholder={runtime?.apiKeyConfigured ? `已配置 ${runtime.apiKeyPreview}` : "输入 API Key"}
            onChange={(event) => setApiKey(event.target.value)}
          />
        </div>
      </div>
      <div className="mt-4 flex justify-end">
        <Button onClick={handleSave} disabled={save.isPending || query.isLoading}>
          {save.isPending ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
          保存普通文本模型
        </Button>
      </div>
    </div>
  );
}
