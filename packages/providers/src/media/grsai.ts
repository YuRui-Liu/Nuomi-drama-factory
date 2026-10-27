const rules = [
  { type: "input", field: "apiKey" as const, title: "API Key", value: "", props: { type: "password", showPassword: true, autocomplete: "off" } },
  { type: "input", field: "baseUrl" as const, title: "请求地址", value: "https://grsai.dakka.com.cn" },
] as const;

async function request(context: ProviderContext, path: string, body?: Record<string, unknown>) {
  const apiKey = String(context.config.apiKey ?? "").trim();
  if (!apiKey) throw new Error("请配置 Grsai API Key");
  const baseUrl = String(context.config.baseUrl ?? "https://grsai.dakka.com.cn").replace(/\/$/, "");
  if (!["https://grsai.dakka.com.cn", "https://grsaiapi.com"].includes(baseUrl)) throw new Error("请选择 Grsai 官方国内或全球节点");
  const response = await context.tool.fetch(`${baseUrl}${path}`, {
    method: body ? "POST" : "GET", headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}), signal: context.signal,
  });
  if (!response.ok) throw new Error(`Grsai 请求失败：HTTP ${response.status}`);
  const result: unknown = await response.json();
  if (!result || typeof result !== "object" || Array.isArray(result)) throw new Error("Grsai 返回格式无效");
  return result as Record<string, unknown>;
}

export default {
  id: "grsai", label: "Grsai", version: "2.0.0", rules,
  readme: "使用 Grsai 异步图片接口，持久制作任务保存任务编号并查询原任务。配置与 Nuomi 共用账号时请使用同一官方节点。",
  models: [{ id: "gpt-image-2", label: "GPT Image 2", type: "image", imageSizes: ["1024x1024", "1536x1024", "1024x1536"], imageRatios: ["1:1", "3:2", "2:3"] }] satisfies ProviderModel[],
  async submitMediaTask(input: MediaTaskRequest): Promise<{ taskId: string }> {
    if (input.type !== "image" || input.request.model !== "gpt-image-2") throw new Error("Grsai 当前仅接入 GPT Image 2 图片生成");
    const image = input.request;
    if (!image.prompt.trim()) throw new Error("图片提示词不能为空");
    if (Object.keys(image.other ?? {}).length) throw new Error("当前 Grsai 接口不接收额外参数");
    const dimensions: Record<string, string> = { "1:1": "1024x1024", "3:2": "1536x1024", "2:3": "1024x1536" };
    if (image.ratio && !dimensions[image.ratio]) throw new Error("当前 Grsai 出图支持 1:1、3:2、2:3；其他画幅需明确裁切方案");
    const size = image.size ?? dimensions[image.ratio ?? "1:1"]!;
    if (!Object.values(dimensions).includes(size) || (image.ratio && dimensions[image.ratio] !== size)) throw new Error("出图尺寸无效或与画幅冲突");
    const images = (image.images ?? []).map(reference => {
      if (reference.type === "url") {
        const url = new URL(reference.url);
        if (url.protocol !== "https:" || url.username || url.password) throw new Error("图片参考地址无效");
        return url.href;
      }
      const data = reference.type === "binary" ? Buffer.from(reference.data).toString("base64") : reference.data.replace(/^data:[^;]+;base64,/, "");
      return `data:${reference.mimeType};base64,${data}`;
    });
    const result = await request(this, "/v1/api/generate", { model: image.model, prompt: image.prompt, images, aspectRatio: size, quality: "auto", replyType: "async" });
    if (!["running", "succeeded"].includes(String(result.status)) || typeof result.id !== "string" || !/^[\w-]{1,256}$/.test(result.id)) throw new Error("Grsai 未返回有效任务编号；请核对远端记录，勿自动重提");
    return { taskId: result.id };
  },
  async queryMediaTask(taskId: string): Promise<MediaTaskSnapshot> {
    if (!/^[\w-]{1,256}$/.test(taskId)) throw new Error("Grsai 任务编号无效");
    const result = await request(this, `/v1/api/result?id=${encodeURIComponent(taskId)}`);
    if (result.id !== taskId) throw new Error("Grsai 返回任务编号不匹配");
    if (["running", "pending", "queued"].includes(String(result.status))) return { status: "running" };
    if (["failed", "violation"].includes(String(result.status))) return { status: "failed", message: "Grsai 图片生成失败或被拒绝" };
    if (result.status !== "succeeded" || !Array.isArray(result.results) || result.results.length !== 1) throw new Error("Grsai 未返回唯一图片结果，保留原任务等待核对");
    const item = result.results[0];
    if (!item || typeof item !== "object" || typeof item.url !== "string") throw new Error("Grsai 图片结果缺少地址");
    const url = new URL(item.url);
    if (url.protocol !== "https:" || url.username || url.password) throw new Error("Grsai 图片结果地址无效");
    return { status: "completed", assets: [{ type: "url", url: url.href, mediaType: "image" }] };
  },
} satisfies ProviderDefinition<typeof rules>;
