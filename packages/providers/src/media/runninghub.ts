const rules = [
  { type: "input", field: "apiKey" as const, title: "API Key", value: "", props: { type: "password", showPassword: true, autocomplete: "off" } },
  { type: "input", field: "voiceDesignWorkflow" as const, title: "音色设计工作流", value: "2059260167811850242" },
  { type: "input", field: "voiceCloneWorkflow" as const, title: "音色克隆工作流", value: "2058388078015901697" },
  { type: "input", field: "h3Workflow" as const, title: "H3 导演台工作流", value: "2114738928094892033" },
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

async function submitH3(context: ProviderContext, video: VideoRequest) {
  if (video.model !== "h3RefDirector" || !Array.isArray(video.mode) || video.mode.length !== 1 || !/^imageReference:[1-9]$/.test(video.mode[0] ?? "")) throw new Error("当前 H3 导演台仅开放图片全参考模式");
  if (!video.prompt.trim() || !video.images?.length || video.images.length > 9 || video.firstFrame || video.lastFrame || video.audios?.length || video.videos?.length) throw new Error("H3 图片全参考需要 1–9 张图片，不接受其他素材槽位");
  if (!video.duration || video.duration < 4 || video.duration > 15) throw new Error("H3 时长须在 4–15 秒内");
  const frames = 17 * Math.ceil((video.duration * 24 - 5) / 17) + 5;
  if (frames > 360) throw new Error("H3 帧格对齐后超过 15 秒，请缩短本段，不能截断对白");
  if (video.ratio && video.ratio !== "16:9") throw new Error("当前导演台只开放横屏方案");
  if (video.resolution && video.resolution !== "736P") throw new Error("当前导演台固定 1280×736 输出");
  const workflowId = String(context.config.h3Workflow ?? "");
  if (!/^\d+$/.test(workflowId)) throw new Error("H3 工作流编号无效");
  const response = await request(context, "/api/openapi/getJsonApiFormat", { workflowId });
  const graph = object(typeof response.prompt === "string" ? JSON.parse(response.prompt) : response.prompt);
  if (object(graph["12"]).class_type !== "MiniMaxH3Director" || object(graph["7"]).class_type !== "SaveVideo") throw new Error("H3 云端工作流结构已变化");
  const refs = [];
  for (const [index, reference] of video.images.entries()) {
    if (reference.type === "url") throw new Error("H3 参考图片必须由工作区读取");
    const bytes = reference.type === "binary" ? Buffer.from(reference.data) : Buffer.from(reference.data.replace(/^data:[^;]+;base64,/, ""), "base64");
    if (!bytes.length || bytes.length > 100 * 1024 * 1024) throw new Error("H3 图片为空或超过 100MB");
    const form = new FormData();
    form.append("file", new Blob([bytes], { type: reference.mimeType }), reference.name?.split(/[\\/]/).pop() || `reference${index}.png`);
    const upload = await context.tool.fetch("https://www.runninghub.cn/openapi/v2/media/upload/binary", { method: "POST", headers: { Authorization: `Bearer ${String(context.config.apiKey).trim()}` }, body: form, signal: context.signal });
    if (!upload.ok) throw new Error(`H3 素材上传失败：HTTP ${upload.status}`);
    const result = object(await upload.json());
    if (result.code != null && ![0, "0", 200, "200"].includes(result.code as string | number)) throw new Error("H3 素材上传被拒绝");
    const file = object(result.data ?? result).fileName;
    if (typeof file !== "string" || !file) throw new Error("H3 素材上传未返回文件编号");
    refs.push({ index, imageFile: file, fileName: "", type: "input", subfolder: "" });
  }
  const inputs = object(object(graph["12"]).inputs);
  const taskType = "r2v — 参考主体生视频(Reference to Video)";
  const timeline = {
    version: 5, editMode: "segment", timelineMode: "prompt_batch", totalFrames: frames, frameRate: 24,
    width: 1280, height: 736, refMaxSize: 1280, durationSec: frames / 24,
    global: { taskType, prompt: "", refs, refAudios: [], refVideos: [], continuousReference: false, commonEnabled: true },
    output: { mode: "fixed", width: 1280, height: 736, multiple: 32, longEdge: 1280, exportMode: "all", audioMode: "generate", continuityEnabled: false, exportSourceImages: false, exportPreFaceRefine: false, refImageSize: "match" },
    runSelectEnabled: false, runSelection: [], videoClips: [], keyframes: [], shots: [], batchWorkspaces: [],
    gen: { defaultFrameCount: frames }, liveTaePreview: false, batchDetailMode: "solo",
    segments: [{ id: String(video.other?.segmentId ?? "segment"), start: 0, length: frames, frameCount: frames, durationSec: frames / 24, prompt: video.prompt, negativePrompt: "", taskType, refs: [], refAudios: [], refVideos: [], genImage: { imageFile: "", fileName: "" }, startImage: null, endImage: null, continuityFromPrev: false, refImageSize: "match", loras: [] }],
  };
  Object.assign(inputs, { timeline_data: JSON.stringify(timeline), global_prompt: "", total_frames: frames, frame_rate: 24, task_type: taskType, width: 1280, height: 736, minimax_director_ui: "", cache_name: "", export_source_images: false, export_pre_face_refine: false });
  const data = await request(context, "/task/openapi/create", { workflowId, nodeInfoList: [], workflow: JSON.stringify(graph) });
  if (typeof data.taskId !== "string" || !/^\d+$/.test(data.taskId)) throw new Error("H3 未返回有效任务编号，禁止自动重提");
  return { taskId: data.taskId };
}

export default {
  id: "runninghub",
  label: "RunningHub",
  version: "2.0.0",
  readme: "通过持久制作任务执行音色设计与独立配音，查询和取消均使用原任务编号。克隆配音需要一段已采用的参考音频。",
  rules,
  models: [
    { id: "qwenVoiceDesign", label: "Qwen3 音色设计", type: "audio" },
    { id: "indexVoiceClone", label: "IndexTTS2 独立配音", type: "audio" },
    { id: "h3RefDirector", label: "MiniMax H3 导演台·图片全参考", type: "video", mode: [["imageReference:9"]], audio: true, durationResolutionMap: [{ duration: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14], resolution: ["736P"] }] },
  ] satisfies ProviderModel[],
  async submitMediaTask(input: MediaTaskRequest): Promise<{ taskId: string }> {
    if (input.type === "video") return submitH3(this, input.request);
    if (input.type !== "audio" || !["qwenVoiceDesign", "indexVoiceClone"].includes(input.request.model)) throw new Error("当前 RunningHub 适配器仅支持音色设计与克隆配音");
    const audio = input.request;
    const cloning = audio.model === "indexVoiceClone";
    if (!audio.text?.trim() || (!cloning && !audio.prompt?.trim())) throw new Error("朗读文本与音色设计描述不能为空");
    if (audio.images?.length || audio.voice || [audio.speed, audio.volume, audio.pitch, audio.language, audio.format, audio.sampleRate, audio.emotion, audio.normalizeText, audio.bitrateKbps].some(value => value != null) || Object.keys(audio.other ?? {}).length) throw new Error("当前音色工作流不支持额外音频参数");
    if (cloning ? audio.audios?.length !== 1 || !!audio.prompt : !!audio.audios?.length) throw new Error("克隆配音需要且仅需要一段参考音频；音色设计不接收参考音频");
    const workflowId = (cloning ? this.config.voiceCloneWorkflow : this.config.voiceDesignWorkflow).trim();
    if (!/^\d+$/.test(workflowId)) throw new Error("音色工作流编号无效");
    const graphResult = await request(this, "/api/openapi/getJsonApiFormat", { workflowId });
    let graph = object(typeof graphResult.prompt === "string" ? JSON.parse(graphResult.prompt) : graphResult.prompt);
    const nodeInfoList = [
      { nodeId: "14", fieldName: "text", fieldValue: audio.text.trim() },
      { nodeId: "15", fieldName: "text", fieldValue: audio.prompt?.trim() ?? "" },
    ];
    if (cloning) {
      // ACT: 首个独立配音闭环只执行中性分支；不暴露尚未验证的情绪模式。
      if (!graph["103"]) {
        if (["1", "14", "17", "20"].some(id => object(graph[id]).class_type !== "IndexTTS2Run")) throw new Error("不支持的 IndexTTS2 工作流版本");
        const keep = new Set<string>();
        const pending = ["5"];
        while (pending.length) {
          const id = pending.pop()!;
          if (keep.has(id)) continue;
          const node = object(graph[id]);
          keep.add(id);
          for (const value of Object.values(object(node.inputs))) {
            if (Array.isArray(value) && value.length === 2 && typeof value[0] === "string" && Number.isInteger(value[1])) pending.push(value[0]);
          }
        }
        graph = Object.fromEntries([...keep].map(id => [id, graph[id]]));
      }
      if (Object.values(graph).filter(node => object(node).class_type === "SaveAudio").length !== 1 || Object.values(graph).filter(node => object(node).class_type === "IndexTTS2Run").length !== 1) throw new Error("克隆工作流必须只执行一个配音分支和一个音频输出");
      nodeInfoList.splice(0, nodeInfoList.length,
        { nodeId: "4", fieldName: "prompt", fieldValue: audio.text.trim() },
        { nodeId: "10", fieldName: "audio", fieldValue: "" },
        { nodeId: "1", fieldName: "emo_alpha", fieldValue: "1" },
        { nodeId: "1", fieldName: "use_random", fieldValue: "false" },
      );
      if (graph["103"]) nodeInfoList.push({ nodeId: "103", fieldName: "value", fieldValue: "1" });
    }
    for (const node of nodeInfoList) {
      if (!Object.hasOwn(object(object(graph[node.nodeId]).inputs), node.fieldName)) throw new Error("音色工作流输入字段与已支持版本不匹配");
    }
    if (cloning) {
      const reference = audio.audios![0]!;
      if (reference.type === "url") throw new Error("克隆参考音频须由工作区读取");
      const bytes = reference.type === "binary" ? Buffer.from(reference.data) : Buffer.from(reference.data.replace(/^data:[^;]+;base64,/, ""), "base64");
      if (!bytes.length || bytes.length > 100 * 1024 * 1024) throw new Error("参考音频为空或超过 100MB");
      const form = new FormData();
      form.append("file", new Blob([bytes], { type: reference.mimeType }), reference.name?.split(/[\\/]/).pop() || "reference.wav");
      const response = await this.tool.fetch("https://www.runninghub.cn/openapi/v2/media/upload/binary", { method: "POST", headers: { Authorization: `Bearer ${this.config.apiKey.trim()}` }, body: form, signal: this.signal });
      if (!response.ok) throw new Error(`参考音频上传失败：HTTP ${response.status}`);
      const result = object(await response.json());
      if (result.code != null && ![0, "0", 200, "200"].includes(result.code as string | number)) throw new Error("RunningHub 拒绝音频上传");
      const uploaded = object(result.data ?? result);
      const file = uploaded.fileName ?? uploaded.filename;
      if (typeof file !== "string" || !file.trim()) throw new Error("音频上传未返回文件编号");
      nodeInfoList.find(node => node.nodeId === "10")!.fieldValue = file;
    }
    const data = await request(this, "/task/openapi/create", { workflowId, nodeInfoList, ...(cloning ? { workflow: JSON.stringify(graph) } : {}) });
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
    if (!Array.isArray(outputs) || outputs.length !== 1) throw new Error("工作流必须返回唯一媒体输出，请核对原任务");
    const assets = outputs.map(value => {
      const result = object(value);
      const url = new URL(String(result.fileUrl ?? result.url ?? result.downloadUrl ?? ""));
      if (url.protocol !== "https:" || url.username || url.password) throw new Error("RunningHub 输出地址无效");
      const mediaType = /\.(mp4|webm|mov)$/i.test(url.pathname) || ["video", "mp4", "webm", "mov"].includes(String(result.fileType).toLowerCase()) ? "video" as const : "audio" as const;
      return { type: "url" as const, url: url.href, mediaType };
    });
    return { status: "completed", assets };
  },
  async cancelMediaTask(taskId: string) {
    if (!/^\d+$/.test(taskId)) throw new Error("RunningHub 任务编号无效");
    await request(this, "/task/openapi/cancel", { taskId });
  },
} satisfies ProviderDefinition<typeof rules>;
