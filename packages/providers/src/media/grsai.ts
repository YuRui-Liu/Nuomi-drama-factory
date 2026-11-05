const rules = [
  { type: "input", field: "apiKey" as const, title: "API Key", value: "", props: { type: "password", showPassword: true, autocomplete: "off" } },
  { type: "input", field: "baseUrl" as const, title: "请求地址", value: "https://grsai.dakka.com.cn" },
] as const;

const models: ProviderModel[] = [
  {"id":"gpt-image-2","label":"gpt-image-2","type":"image","imageSizes":["1024x1024","1672x941","941x1672","1443x1090","1090x1443","1536x1024","1024x1536","1408x1120","1120x1408","1920x832","832x1920","896x1792","1792x896"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","9:21","1:2","2:1"]},
  {"id":"gpt-image-2.5","label":"gpt-image-2.5","type":"image","imageSizes":["1024x1024","1672x941","941x1672","1443x1090","1090x1443","1536x1024","1024x1536","1408x1120","1120x1408","1920x832","832x1920","896x1792","1792x896"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","9:21","1:2","2:1"]},
  {"id":"gpt-image-2-vip","label":"gpt-image-2-vip","type":"image","imageSizes":["1024x1024","2048x2048","2880x2880","1280x720","2048x1152","3840x2160","720x1280","1152x2048","2160x3840","1152x864","2304x1728","3264x2448","864x1152","1728x2304","2448x3264","1536x1024","2048x1360","3504x2336","1024x1536","1360x2048","2336x3504","1536x768","3072x1536","3840x1920","768x1536","1536x3072","1920x3840"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","2:1","1:2"]},
  {"id":"gpt-image-2.5-flare","label":"gpt-image-2.5-flare","type":"image","imageSizes":["1024x1024","2048x2048","2880x2880","1280x720","2048x1152","3840x2160","720x1280","1152x2048","2160x3840","1152x864","2304x1728","3264x2448","864x1152","1728x2304","2448x3264","1536x1024","2048x1360","3504x2336","1024x1536","1360x2048","2336x3504","1536x768","3072x1536","3840x1920","768x1536","1536x3072","1920x3840"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","2:1","1:2"]},
  {"id":"gpt-image-2.5-sunburst","label":"gpt-image-2.5-sunburst","type":"image","imageSizes":["1024x1024","2048x2048","2880x2880","1280x720","2048x1152","3840x2160","720x1280","1152x2048","2160x3840","1152x864","2304x1728","3264x2448","864x1152","1728x2304","2448x3264","1536x1024","2048x1360","3504x2336","1024x1536","1360x2048","2336x3504","1536x768","3072x1536","3840x1920","768x1536","1536x3072","1920x3840"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","2:1","1:2"]},
  {"id":"nano-banana","label":"nano-banana","type":"image","imageSizes":["1K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
  {"id":"nano-banana-fast","label":"nano-banana-fast","type":"image","imageSizes":["1K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
  {"id":"nano-banana-2","label":"nano-banana-2","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
  {"id":"nano-banana-2-cl","label":"nano-banana-2-cl","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
  {"id":"nano-banana-2-2k-cl","label":"nano-banana-2-2k-cl","type":"image","imageSizes":["2K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
  {"id":"nano-banana-2-4k-cl","label":"nano-banana-2-4k-cl","type":"image","imageSizes":["4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
  {"id":"nano-banana-2.1","label":"nano-banana-2.1","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
  {"id":"nano-banana-pro","label":"nano-banana-pro","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
  {"id":"nano-banana-pro-vt","label":"nano-banana-pro-vt","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
  {"id":"nano-banana-pro-cl","label":"nano-banana-pro-cl","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
  {"id":"nano-banana-pro-vip","label":"nano-banana-pro-vip","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
  {"id":"nano-banana-pro-4k-vip","label":"nano-banana-pro-4k-vip","type":"image","imageSizes":["4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
];
const imageDimensions: Record<string, string> = {"1:1":"1024x1024","16:9":"1672x941","9:16":"941x1672","4:3":"1443x1090","3:4":"1090x1443","3:2":"1536x1024","2:3":"1024x1536","5:4":"1408x1120","4:5":"1120x1408","21:9":"1920x832","9:21":"832x1920","1:2":"896x1792","2:1":"1792x896"};
const vipDimensions: Record<string, string[]> = {"1:1":["1024x1024","2048x2048","2880x2880"],"16:9":["1280x720","2048x1152","3840x2160"],"9:16":["720x1280","1152x2048","2160x3840"],"4:3":["1152x864","2304x1728","3264x2448"],"3:4":["864x1152","1728x2304","2448x3264"],"3:2":["1536x1024","2048x1360","3504x2336"],"2:3":["1024x1536","1360x2048","2336x3504"],"2:1":["1536x768","3072x1536","3840x1920"],"1:2":["768x1536","1536x3072","1920x3840"]};

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

async function submitImageTask(context: ProviderContext, image: ImageRequest) {
  const model = models.find(item => item.id === image.model);
  if (!model) throw new Error("Grsai 图片模型未接入，请重新选择模型");
  const banana = image.model.startsWith("nano-banana");
  if (!image.prompt.trim()) throw new Error("图片提示词不能为空");
  if (Object.keys(image.other ?? {}).length) throw new Error("当前 Grsai 接口不接收额外参数");
  const vip = ["gpt-image-2-vip", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"].includes(image.model);
  if (image.ratio && !model.imageRatios?.includes(image.ratio)) throw new Error("当前 Grsai 模型不支持所选画幅");
  const size = image.size ?? (banana ? model.imageSizes![0]! : vip ? vipDimensions[image.ratio ?? "1:1"]!.at(-1)! : imageDimensions[image.ratio ?? "1:1"]!);
  if (!model.imageSizes?.includes(size) || (!banana && image.ratio && !(vip ? vipDimensions[image.ratio]?.includes(size) : imageDimensions[image.ratio] === size))) throw new Error("出图尺寸无效或与画幅冲突");
  const images = (image.images ?? []).map(reference => {
    if (reference.type === "url") {
      const url = new URL(reference.url);
      if (url.protocol !== "https:" || url.username || url.password) throw new Error("图片参考地址无效");
      return url.href;
    }
    const data = reference.type === "binary" ? Buffer.from(reference.data).toString("base64") : reference.data.replace(/^data:[^;]+;base64,/, "");
    return `data:${reference.mimeType};base64,${data}`;
  });
  const result = await request(context, "/v1/api/generate", {
    model: image.model, prompt: image.prompt, images, replyType: "async", aspectRatio: banana ? image.ratio ?? "auto" : size,
    ...(banana ? ( ["nano-banana", "nano-banana-fast"].includes(image.model) ? {} : { imageSize: size }) : { quality: vip ? image.quality ?? "high" : "auto" }),
  });
  if (!["running", "succeeded"].includes(String(result.status)) || typeof result.id !== "string" || !/^[\w-]{1,256}$/.test(result.id)) throw new Error("Grsai 未返回有效任务编号；请核对远端记录，勿自动重提");
  return result.id;
}

async function readImageTask(context: ProviderContext, taskId: string): Promise<MediaTaskSnapshot> {
  if (!/^[\w-]{1,256}$/.test(taskId)) throw new Error("Grsai 任务编号无效");
  const result = await request(context, `/v1/api/result?id=${encodeURIComponent(taskId)}`);
  if (result.id !== taskId) throw new Error("Grsai 返回任务编号不匹配");
  if (["running", "pending", "queued"].includes(String(result.status))) return { status: "running" };
  if (["failed", "violation"].includes(String(result.status))) return { status: "failed", message: "Grsai 图片生成失败或被拒绝" };
  if (result.status !== "succeeded" || !Array.isArray(result.results) || result.results.length !== 1) throw new Error("Grsai 未返回唯一图片结果，保留原任务等待核对");
  const item = result.results[0];
  if (!item || typeof item !== "object" || typeof item.url !== "string") throw new Error("Grsai 图片结果缺少地址");
  const url = new URL(item.url);
  if (url.protocol !== "https:" || url.username || url.password) throw new Error("Grsai 图片结果地址无效");
  return { status: "completed", assets: [{ type: "url", url: url.href, mediaType: "image" }] };
}

function waitForTask(signal: AbortSignal) {
  signal.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 3000);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export default {
  id: "grsai", label: "Grsai", version: "2.2.0", rules,
  readme: "使用 Grsai 异步图片接口，持久制作任务保存任务编号并查询原任务。配置与 Nuomi 共用账号时请使用同一官方节点。",
  models: [
    {"id":"gpt-image-2","label":"gpt-image-2","type":"image","imageSizes":["1024x1024","1672x941","941x1672","1443x1090","1090x1443","1536x1024","1024x1536","1408x1120","1120x1408","1920x832","832x1920","896x1792","1792x896"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","9:21","1:2","2:1"]},
    {"id":"gpt-image-2.5","label":"gpt-image-2.5","type":"image","imageSizes":["1024x1024","1672x941","941x1672","1443x1090","1090x1443","1536x1024","1024x1536","1408x1120","1120x1408","1920x832","832x1920","896x1792","1792x896"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","9:21","1:2","2:1"]},
    {"id":"gpt-image-2-vip","label":"gpt-image-2-vip","type":"image","imageSizes":["1024x1024","2048x2048","2880x2880","1280x720","2048x1152","3840x2160","720x1280","1152x2048","2160x3840","1152x864","2304x1728","3264x2448","864x1152","1728x2304","2448x3264","1536x1024","2048x1360","3504x2336","1024x1536","1360x2048","2336x3504","1536x768","3072x1536","3840x1920","768x1536","1536x3072","1920x3840"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","2:1","1:2"]},
    {"id":"gpt-image-2.5-flare","label":"gpt-image-2.5-flare","type":"image","imageSizes":["1024x1024","2048x2048","2880x2880","1280x720","2048x1152","3840x2160","720x1280","1152x2048","2160x3840","1152x864","2304x1728","3264x2448","864x1152","1728x2304","2448x3264","1536x1024","2048x1360","3504x2336","1024x1536","1360x2048","2336x3504","1536x768","3072x1536","3840x1920","768x1536","1536x3072","1920x3840"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","2:1","1:2"]},
    {"id":"gpt-image-2.5-sunburst","label":"gpt-image-2.5-sunburst","type":"image","imageSizes":["1024x1024","2048x2048","2880x2880","1280x720","2048x1152","3840x2160","720x1280","1152x2048","2160x3840","1152x864","2304x1728","3264x2448","864x1152","1728x2304","2448x3264","1536x1024","2048x1360","3504x2336","1024x1536","1360x2048","2336x3504","1536x768","3072x1536","3840x1920","768x1536","1536x3072","1920x3840"],"imageRatios":["1:1","16:9","9:16","4:3","3:4","3:2","2:3","2:1","1:2"]},
    {"id":"nano-banana","label":"nano-banana","type":"image","imageSizes":["1K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
    {"id":"nano-banana-fast","label":"nano-banana-fast","type":"image","imageSizes":["1K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
    {"id":"nano-banana-2","label":"nano-banana-2","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
    {"id":"nano-banana-2-cl","label":"nano-banana-2-cl","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
    {"id":"nano-banana-2-2k-cl","label":"nano-banana-2-2k-cl","type":"image","imageSizes":["2K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
    {"id":"nano-banana-2-4k-cl","label":"nano-banana-2-4k-cl","type":"image","imageSizes":["4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
    {"id":"nano-banana-2.1","label":"nano-banana-2.1","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9","1:4","4:1","1:8","8:1"]},
    {"id":"nano-banana-pro","label":"nano-banana-pro","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
    {"id":"nano-banana-pro-vt","label":"nano-banana-pro-vt","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
    {"id":"nano-banana-pro-cl","label":"nano-banana-pro-cl","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
    {"id":"nano-banana-pro-vip","label":"nano-banana-pro-vip","type":"image","imageSizes":["1K","2K","4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
    {"id":"nano-banana-pro-4k-vip","label":"nano-banana-pro-4k-vip","type":"image","imageSizes":["4K"],"imageRatios":["auto","1:1","16:9","9:16","4:3","3:4","3:2","2:3","5:4","4:5","21:9"]},
  ],
  async generateImage(image: ImageRequest): Promise<MediaAsset[]> {
    const signal = AbortSignal.any([AbortSignal.timeout(30 * 60_000), ...(this.signal ? [this.signal] : [])]);
    const context = { ...this, signal };
    const taskId = await submitImageTask(context, image);
    while (true) {
      signal.throwIfAborted();
      const result = await readImageTask(context, taskId);
      if (result.status === "completed") return result.assets;
      if (result.status === "failed") throw new Error(result.message);
      await waitForTask(signal);
    }
  },
  async submitMediaTask(input: MediaTaskRequest): Promise<{ taskId: string }> {
    if (input.type !== "image") throw new Error("Grsai 当前接入图片生成");
    return { taskId: await submitImageTask(this, input.request) };
  },
  async queryMediaTask(taskId: string): Promise<MediaTaskSnapshot> {
    return readImageTask(this, taskId);
  },
} satisfies ProviderDefinition<typeof rules>;
