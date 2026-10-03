import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { type ChatBackend, useChatRuntimeConfig, useSaveChatRuntimeConfig } from "@/lib/queries/chat-runtime";
import { TASK_REASONING_EFFORTS, type TaskReasoningEffort } from "@/lib/queries/model-gateway";

const MODELS: Record<ChatBackend, string> = {
  hermes: "", codex: "gpt-5.6-sol", workbuddy: "default-model", deepseek_harness: "deepseek-v4-flash-vision-exp",
};

export function ChatRuntimePanel({ enabled = true }: { enabled?: boolean }) {
  const query = useChatRuntimeConfig(enabled);
  const save = useSaveChatRuntimeConfig();
  const runtime = query.data?.data;
  const [backend, setBackend] = useState<ChatBackend>("hermes");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState<TaskReasoningEffort | null>(null);
  useEffect(() => {
    if (!runtime) return;
    setBackend(runtime.backend); setModel(runtime.model); setEffort(runtime.reasoningEffort);
  }, [runtime?.backend, runtime?.model, runtime?.reasoningEffort]);

  const handleSave = async () => {
    if (backend !== "hermes" && !model.trim()) { toast.error("聊天模型不能为空"); return; }
    try {
      const result = await save.mutateAsync({ backend, model: model.trim(), reasoningEffort: backend === "hermes" ? null : effort });
      if (!result.ok) { toast.error(result.error || "保存聊天 Agent 失败"); return; }
      toast.success("聊天 Agent 已保存，下一轮对话生效");
    } catch (error) { toast.error(error instanceof Error ? error.message : "保存聊天 Agent 失败"); }
  };

  return (
    <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.025] p-4">
      <h4 className="text-sm font-medium text-foreground">聊天 Agent</h4>
      <p className="mt-1 text-xs text-muted-foreground">选择对话使用的 Agent，与后台文本任务运行时分别配置。保存后下一轮对话生效。</p>
      {query.isError ? <p role="alert" className="mt-2 text-xs text-destructive">加载聊天 Agent 配置失败，请重新打开设置。</p> : null}
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="chat-runtime-backend">聊天 Agent 运行时</Label>
          <Select value={backend} onValueChange={(value) => { const next = value as ChatBackend; setBackend(next); setModel(MODELS[next]); }}>
            <SelectTrigger id="chat-runtime-backend"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="hermes">Hermes</SelectItem>
              <SelectItem value="codex">Codex</SelectItem>
              <SelectItem value="workbuddy">WorkBuddy</SelectItem>
              <SelectItem value="deepseek_harness">DeepSeek Harness</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="chat-runtime-model">聊天模型</Label>
          <Input id="chat-runtime-model" value={model} onChange={(event) => setModel(event.target.value)} placeholder={backend === "hermes" ? "留空使用现有网关模型" : "输入模型 ID"} />
        </div>
        {backend !== "hermes" ? <div className="space-y-1.5">
          <Label htmlFor="chat-runtime-effort">聊天推理强度（可选）</Label>
          <Select value={effort ?? "default"} onValueChange={(value) => setEffort(value === "default" ? null : value as TaskReasoningEffort)}>
            <SelectTrigger id="chat-runtime-effort"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="default">运行时默认</SelectItem>{TASK_REASONING_EFFORTS.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent>
          </Select>
        </div> : null}
      </div>
      <div className="mt-4 flex justify-end"><Button onClick={handleSave} disabled={save.isPending || query.isLoading || query.isError}>保存聊天 Agent</Button></div>
    </div>
  );
}
