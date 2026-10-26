const rules = [
  { type: "input", field: "apiKey" as const, title: "API Key", value: "", props: { type: "password", showPassword: true, autocomplete: "off" } },
  { type: "input", field: "voiceDesignWorkflow" as const, title: "音色设计工作流", value: "2059260167811850242" },
] as const;

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("RunningHub 返回格式无效");
  return value as Record<string, unknown>;
}

async function request(context: ProviderContext, path: string, body: Record<string, unknown>) {
  const apiKey = typeof context.config.apiKey === "string" ? context.config.apiKey.trim() : "";
  if (!apiKey) throw new Error("请配置 RunningHub API Key");
  const response = await context.tool.fetch(`https://www.runninghub.cn${path}`, {
    method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ ...body, apiKey }), signal: context.signal,
  });
  if (!response.ok) throw new Error(`RunningHub 请求失败：HTTP ${response.status}`);
  const result = object(await response.json());
  if (result.code != null && ![0, "0", 200, "200"].includes(result.code as string | number)) throw new Error("RunningHub 拒绝请求，请检查账号与工作流权限");
  return object(result.data ?? result);
}

export default {
  id: "runninghub",
  label: "RunningHub",
  version: "2.0.0",
  readme: "通过持久制作任务执行音色设计，查询和取消均使用原任务编号。",
  rules,
  models: [{ id: "qwenVoiceDesign", label: "Qwen3 音色设计", type: "audio" }] satisfies ProviderModel[],
  async submitMediaTask(input: MediaTaskRequest): Promise<{ taskId: string }> {
    if (input.type !== "audio" || input.request.model !== "qwenVoiceDesign") throw new Error("当前 RunningHub 适配器仅支持音色设计");
    const audio = input.request;
    if (!audio.text?.trim() || !audio.prompt?.trim()) throw new Error("试听文本与音色描述不能为空");
    if (audio.audios?.length || audio.images?.length || audio.voice || audio.speed != null || Object.keys(audio.other ?? {}).length) throw new Error("音色设计只接收试听文本和音色描述");
    const workflowId = this.config.voiceDesignWorkflow.trim();
    if (!/^\d+$/.test(workflowId)) throw new Error("音色设计工作流编号无效");
    const graphResult = await request(this, "/api/openapi/getJsonApiFormat", { workflowId });
    const graph = object(typeof graphResult.prompt === "string" ? JSON.parse(graphResult.prompt) : graphResult.prompt);
    for (const id of ["14", "15"]) {
      if (typeof object(object(graph[id]).inputs).text !== "string") throw new Error("音色设计工作流节点与已支持版本不匹配");
    }
    const data = await request(this, "/task/openapi/create", { workflowId, nodeInfoList: [
      { nodeId: "14", fieldName: "text", fieldValue: audio.text.trim() },
      { nodeId: "15", fieldName: "text", fieldValue: audio.prompt.trim() },
    ] });
    if (typeof data.taskId !== "string" || !/^\d+$/.test(data.taskId)) throw new Error("RunningHub 未返回有效任务编号，请勿自动重新提交");
    return { taskId: data.taskId };
  },
  async queryMediaTask(taskId: string): Promise<MediaTaskSnapshot> {
    if (!/^\d+$/.test(taskId)) throw new Error("RunningHub 任务编号无效");
    const data = await request(this, "/openapi/v2/query", { taskId });
    const status = String(data.taskStatus ?? data.status ?? "").toLowerCase();
    if (["queued", "pending", "running", "processing"].includes(status)) return { status: "running" };
    if (["failed", "failure"].includes(status)) return { status: "failed", message: "RunningHub 生成失败" };
    if (["cancelled", "canceled"].includes(status)) return { status: "cancelled" };
    if (!["success", "succeeded", "completed"].includes(status)) throw new Error("RunningHub 返回未知任务状态，保留原任务等待核对");
    const outputs = data.results ?? data.outputs;
    if (!Array.isArray(outputs) || !outputs.length) throw new Error("RunningHub 成功任务尚无音频输出");
    const assets = outputs.map(value => {
      const result = object(value);
      const url = new URL(String(result.fileUrl ?? result.url ?? result.downloadUrl ?? ""));
      if (url.protocol !== "https:" || url.username || url.password) throw new Error("RunningHub 输出地址无效");
      return { type: "url" as const, url: url.href, mediaType: "audio" as const };
    }).sort((a, b) => a.url.localeCompare(b.url));
    return { status: "completed", assets };
  },
  async cancelMediaTask(taskId: string) {
    if (!/^\d+$/.test(taskId)) throw new Error("RunningHub 任务编号无效");
    await request(this, "/task/openapi/cancel", { taskId });
  },
} satisfies ProviderDefinition<typeof rules>;
