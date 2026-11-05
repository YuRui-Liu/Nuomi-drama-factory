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
  if (result.code != null && ![0, "0", 200, "200"].includes(result.code as string | number)) throw new Error(`RunningHub 拒绝请求：${context.tool.errorMessage(result) || "请检查账号与工作流权限"}`);
  return object(result.data ?? result);
}

async function submitH3(context: ProviderContext, video: VideoRequest) {
  const referenceMode = video.model === "h3RefDirector";
  if (!referenceMode && video.model !== "h3FrameDirector") throw new Error("未知 H3 导演台模型");
  if (!video.prompt.trim() || video.audios?.length || video.videos?.length) throw new Error("当前 H3 导演台需要提示词，仅支持图片或首尾帧输入");
  if (referenceMode) {
    if (!Array.isArray(video.mode) || video.mode.length !== 1 || !/^imageReference:[1-9]$/.test(video.mode[0] ?? "") || !video.images?.length || video.images.length > 9 || video.firstFrame || video.lastFrame) throw new Error("H3 图片全参考需要 1–9 张图片，不接受首尾帧槽位");
  } else {
    const validFrames = video.mode === "text" ? !video.firstFrame && !video.lastFrame
      : video.mode === "startEndRequired" ? !!video.firstFrame && !!video.lastFrame
      : video.mode === "endFrameOptional" ? !!video.firstFrame
      : video.mode === "startFrameOptional" ? !!video.lastFrame : false;
    if (!validFrames || video.images?.length) throw new Error("H3 首尾帧模式与素材槽位不匹配，不能把参考图代替首尾帧");
  }
  if (!video.duration || video.duration < 4 || video.duration > 15) throw new Error("H3 时长须在 4–15 秒内");
  const frames = 17 * Math.ceil((video.duration * 24 - 5) / 17) + 5;
  if (frames > 360) throw new Error("H3 帧格对齐后超过 15 秒，请缩短本段，不能截断对白");
  if (video.ratio && video.ratio !== "16:9") throw new Error("当前导演台只开放横屏方案");
  const resolution = video.resolution ?? "1.0";
  if (resolution !== "0.8" && resolution !== "1.0") throw new Error("H3 导演台分辨率仅支持 0.8 或 1.0 档");
  const width = resolution === "0.8" ? 1024 : 1280;
  const height = resolution === "0.8" ? 576 : 736;
  const workflowId = String(context.config.h3Workflow ?? "");
  if (!/^\d+$/.test(workflowId)) throw new Error("H3 工作流编号无效");
  const response = await request(context, "/api/openapi/getJsonApiFormat", { workflowId });
  const graph = object(typeof response.prompt === "string" ? JSON.parse(response.prompt) : response.prompt);
  if (object(graph["12"]).class_type !== "MiniMaxH3Director" || object(graph["7"]).class_type !== "SaveVideo") throw new Error("H3 云端工作流结构已变化");
  if (!referenceMode) {
    const loader = object(graph["1"]);
    const frameLoader = object(graph["21"]);
    const frameModel = object(frameLoader.inputs).unet_name;
    if (loader.class_type !== "UNETLoader" || frameLoader.class_type !== "UNETLoader" || typeof frameModel !== "string" || !frameModel.includes("fl2va")) throw new Error("H3 工作流缺少已配置的首尾帧模型");
    object(loader.inputs).unet_name = frameModel;
  }
  const sourceImages = referenceMode ? video.images! : [video.firstFrame, video.lastFrame].filter((item): item is MediaInput => !!item);
  const uploaded = [];
  for (const [index, reference] of sourceImages.entries()) {
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
    uploaded.push({ index, imageFile: file, fileName: "", type: "input", subfolder: "" });
  }
  const inputs = object(object(graph["12"]).inputs);
  const refs = referenceMode ? uploaded : [];
  const first = !referenceMode && video.firstFrame ? uploaded[0]! : null;
  const last = !referenceMode && video.lastFrame ? uploaded[video.firstFrame ? 1 : 0]! : null;
  // ACT: 尾帧单独生成、无锚点文本生成都使用导演台的 FL2V 路径，不虚构 l2v 任务类型。
  const taskType = referenceMode ? "r2v — 参考主体生视频(Reference to Video)"
    : first && !last ? "i2v — 首帧生视频(Image-to-Video)" : "fl2v — 首尾帧生视频(First-Last Frame)";
  const segmentId = String(video.other?.segmentId ?? "segment");
  const timeline = {
    version: 5, editMode: "segment", timelineMode: referenceMode ? "prompt_batch" : first && !last ? "i2v" : "fl2v", totalFrames: frames, frameRate: 24,
    width, height, refMaxSize: width, durationSec: frames / 24,
    global: { taskType, prompt: "", refs, refAudios: [], refVideos: [], continuousReference: false, commonEnabled: true },
    output: { mode: "fixed", width, height, multiple: 32, longEdge: width, exportMode: "all", audioMode: video.generateAudio === false ? "mute" : "generate", continuityEnabled: false, exportSourceImages: false, exportPreFaceRefine: false, refImageSize: "match" },
    runSelectEnabled: false, runSelection: [], videoClips: [], keyframes: [],
    shots: referenceMode ? [] : [{ id: segmentId, durationSec: frames / 24, prompt: video.prompt, negativePrompt: "", startImage: first, endImage: last, continuityFromPrev: false }], batchWorkspaces: [],
    gen: { defaultFrameCount: frames }, liveTaePreview: false, batchDetailMode: "solo",
    segments: [{ id: segmentId, start: 0, length: frames, frameCount: frames, durationSec: frames / 24, prompt: video.prompt, negativePrompt: "", taskType, refs: [], refAudios: [], refVideos: [], genImage: first, startImage: first, endImage: last, isStartFrame: !!first, isEndFrame: !!last, continuityFromPrev: false, refImageSize: "match", loras: [] }],
  };
  if (video.generateAudio === false) {
    if (object(graph["6"]).class_type !== "CreateVideo") throw new Error("H3 工作流的视频封装节点已变化，无法保证独立配音");
    delete object(object(graph["6"]).inputs).audio;
  }
  Object.assign(inputs, { timeline_data: JSON.stringify(timeline), global_prompt: "", total_frames: frames, frame_rate: 24, task_type: taskType, width, height, minimax_director_ui: "", cache_name: "", export_source_images: false, export_pre_face_refine: false });
  const data = await request(context, "/task/openapi/create", { workflowId, nodeInfoList: [], workflow: JSON.stringify(graph) });
  if (typeof data.taskId !== "string" || !/^\d+$/.test(data.taskId)) throw new Error("H3 未返回有效任务编号，禁止自动重提");
  return { taskId: data.taskId };
}

export default {
  id: "runninghub",
  label: "RunningHub",
  version: "2.1.0",
  readme: "通过持久制作任务执行音色设计与独立配音，查询和取消均使用原任务编号。克隆配音需要一段已采用的参考音频。",
  rules,
  models: [
    { id: "qwenVoiceDesign", label: "Qwen3 音色设计", type: "audio" },
    { id: "indexVoiceClone", label: "IndexTTS2 独立配音", type: "audio" },
    { id: "h3RefDirector", label: "MiniMax H3 导演台·图片全参考", type: "video", mode: [["imageReference:9"]], audio: "optional", durationResolutionMap: [{ duration: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14], resolution: ["1.0", "0.8"] }] },
    { id: "h3FrameDirector", label: "MiniMax H3 导演台·首帧／首尾帧／尾帧／文本", type: "video", mode: ["endFrameOptional", "startEndRequired", "startFrameOptional", "text"], audio: "optional", durationResolutionMap: [{ duration: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14], resolution: ["1.0", "0.8"] }] },
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
