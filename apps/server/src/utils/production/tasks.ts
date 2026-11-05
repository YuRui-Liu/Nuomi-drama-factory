import { createHash } from "node:crypto";
import { dirname } from "node:path";
import { mkdir, readFile, realpath, writeAtomic } from "@toonflow/file";
import type { ProviderTaskRequest } from "@toonflow/providers";
import type { MediaGenerationRequest } from "@toonflow/tools-scaffold/runtime";
import { styleInstruction, h3StyleInstruction, h3TechniqueInstruction } from "@toonflow/node-shared/production/schemas";
import { configuredProvider, prepareRequest, readReference, assetBytes } from "@/utils/media/generation";
import { lockWorkspaceFiles, resolveWorkspacePath } from "@/utils/workspace/files";
import { read as readProduction, referenceMedia } from "./operations";
import { failure } from "./store";
import { taskCommandSchema, taskSchema, type ProductionTask } from "./taskSchema";

function digest(value: string | Uint8Array) { return createHash("sha256").update(value).digest("hex"); }
function configurationHash(config: unknown) {
  return digest(JSON.stringify(config, (_key, value) => value && typeof value === "object" && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value));
}

async function references(directory: string, request: MediaGenerationRequest) {
  const items = [...request.images ?? [], ...request.videos ?? [], ...request.audios ?? [], ...request.firstFrame ? [request.firstFrame] : [], ...request.lastFrame ? [request.lastFrame] : []];
  const result = [];
  for (const item of items) {
    const media = await readReference(directory, item, item.mimeType.split("/")[0]!);
    result.push({ path: item.path, hash: digest(Buffer.from(media.data, "base64")) });
  }
  return result;
}

async function save(directory: string, task: ProductionTask, exclusive = false) {
  task.updatedAt = new Date().toISOString();
  const target = await resolveWorkspacePath(directory, `production/tasks/${task.id}.json`, true);
  await mkdir(dirname(target.path), { recursive: true });
  await resolveWorkspacePath(directory, `production/tasks/${task.id}.json`);
  await writeAtomic(target.path, JSON.stringify(taskSchema.parse(task), null, 2), { exclusive });
}

async function load(directory: string, id: string) {
  const target = await resolveWorkspacePath(directory, `production/tasks/${id}.json`, true);
  try {
    const task = taskSchema.parse(JSON.parse(await readFile(target.path, "utf8")));
    if (task.id !== id) failure("任务记录与文件不匹配", 400);
    return task;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") failure("制作任务不存在", 404);
    throw error;
  }
}

async function approvedPlan(directory: string, task: Pick<ProductionTask, "plan" | "input">) {
  const result = await readProduction(directory, { action: "read", ref: task.plan });
  if (!("record" in result) || !result.record || result.adoption?.selected.revision !== task.plan.revision || result.staleDependencies?.length) failure("生成方案尚未采用或依赖已变化");
  const currentStyle = await readProduction(directory, { action: "readDefaultStyle" });
  const hasDefaultStyle = "ref" in currentStyle && !!currentStyle.ref;
  if (task.input.type === "image") {
    const content = result.record.content;
    if (hasDefaultStyle && content.kind !== "imagePlan") failure("作品已有默认风格，图片任务必须绑定已采用的独立出图方案");
    if (content.kind === "imagePlan") {
      if (content.prompt !== task.input.request.prompt) failure("图片提示词与已采用的出图方案不一致");
      if (hasDefaultStyle && !content.style) failure("出图方案缺少已采用的风格快照");
      if (content.style && !content.prompt.includes(styleInstruction(content.style))) failure("图片提示词缺少完整的风格快照");
    }
  }
  if (task.input.type === "video" && task.input.request.providerId === "runninghub") {
    const request = task.input.request;
    const content = result.record.content;
    if (content.kind !== "h3Plan") failure("H3 视频任务必须绑定已采用的 H3 规划");
    const segment = content.segments.find(item => item.id === request.h3SegmentId);
    if (!segment || request.modelId !== (segment.mode === "ref2va" ? "h3RefDirector" : "h3FrameDirector") || segment.prompt !== request.prompt || segment.requestedSeconds !== request.duration) failure("H3 请求与采用的规划段不一致");
    if (hasDefaultStyle && !segment.style) failure("H3 生成段缺少已采用的风格快照");
    if (segment.style) {
      const field = segment.mode === "ref2va" ? "detailed_description" : "integrated_multimodal_description";
      const body = segment.prompt.split(`${field}:`)[1]?.split(/\n(?:overall_soundscape|non_diegetic_music):/)[0] ?? "";
      if (!segment.stylePrompt || !body.includes(h3StyleInstruction(segment.style, segment.stylePrompt))) failure("H3 画面描述缺少风格的英文执行描述，请重新保存并采用方案");
    }
    if (segment.technique) {
      const field = segment.mode === "ref2va" ? "detailed_description" : "integrated_multimodal_description";
      const body = segment.prompt.split(`${field}:`)[1]?.split(/\n(?:overall_soundscape|non_diegetic_music):/)[0] ?? "";
      if (!segment.techniquePrompt || !body.includes(h3TechniqueInstruction(segment.technique, segment.techniquePrompt))) failure("H3 画面描述缺少手法的英文执行描述，请重新保存并采用方案");
    }
    const expectedMode = { t2va: "text", i2va: "endFrameOptional", fl2va: "startEndRequired", l2va: "startFrameOptional" };
    if (segment.mode === "ref2va"
      ? !Array.isArray(request.mode) || request.mode.length !== 1 || !/^imageReference:[1-9]$/.test(request.mode[0] ?? "")
      : request.mode !== expectedMode[segment.mode]) failure("H3 执行模式与采用规划不一致");
    if (request.generateAudio !== true) failure("H3 制作任务须开启原生音轨，保留背景音和音效；独立配音在后期仅替换原对白");
    if (request.ratio && request.ratio !== "16:9") failure("当前 H3 工作流仅支持 16:9");
    if (request.resolution && !["736P", "1.0", "0.8"].includes(request.resolution)) failure("当前 H3 工作流仅支持 736P、1.0 或 0.8");
    const frames = 17 * Math.ceil((segment.requestedSeconds * 24 - 5) / 17) + 5;
    if (segment.effectiveFrames !== frames || frames > 360) failure("H3 规划帧数与执行帧格不一致或超过 15 秒");
    const images: { path: string; mimeType: string }[] = [];
    const dialogue = new Map<string, string>();
    const scripts = new Set<string>();
    let firstFrame: { path: string; mimeType: string } | undefined;
    let lastFrame: { path: string; mimeType: string } | undefined;
    for (const reference of segment.references) {
      if (!["subject", "firstFrame", "lastFrame"].includes(reference.role)) failure("当前 H3 执行器尚未开放音视频参考");
      const source = await readProduction(directory, { action: "read", ref: reference.asset });
      if (!("record" in source) || !source.record) failure("H3 引用缺少图片成果");
      if (segment.style) {
        const sourceContent = source.record.content;
        const sourceStyle = sourceContent.kind === "storyboard" ? sourceContent.shots.find(shot => shot.id === reference.shotId)?.style
          : sourceContent.kind === "asset" ? sourceContent.style : undefined;
        if (!sourceStyle || sourceStyle.ref.id !== segment.style.ref.id || sourceStyle.ref.revision !== segment.style.ref.revision) failure("H3 图片素材缺少相同的风格版本，请先核对并绑定素材风格");
      }
      if (segment.dialogueIds.length && source.record.content.kind === "storyboard") {
        const ref = source.record.content.script;
        const key = `${ref.id}:${ref.revision}`;
        if (!scripts.has(key)) {
          const script = await readProduction(directory, { action: "read", ref });
          if (!("record" in script) || !script.record || script.record.content.kind !== "script") failure("H3 分镜缺少来源剧本");
          for (const shot of script.record.content.scenes.flatMap(scene => scene.shots)) {
            if (segment.sourceShotIds.includes(shot.id)) for (const line of shot.dialogue) dialogue.set(line.id, line.text);
          }
          scripts.add(key);
        }
      }
      const resolved = referenceMedia(source.record.content, reference.shotId);
      if (!resolved.mimeType.startsWith("image/")) failure("H3 引用缺少图片成果");
      const media = { path: resolved.path, mimeType: resolved.mimeType };
      if (reference.role === "firstFrame") firstFrame = media;
      else if (reference.role === "lastFrame") lastFrame = media;
      else images.push(media);
    }
    if (segment.dialogueIds.length) {
      const spoken = [...segment.prompt.matchAll(/<d>\s*\[([^\]]+)\]\s*([\s\S]*?)<\/d>/g)];
      if (spoken.length !== segment.dialogueIds.length) failure("H3 须逐句包含 <d>[语言] 原文</d>，不能只描述说话动作或依赖后期配音");
      for (const [index, id] of segment.dialogueIds.entries()) {
        const text = dialogue.get(id);
        const line = spoken[index]!;
        if (!text || line[2]!.trim() !== text.trim()) failure("H3 对白原文或顺序与来源剧本不一致");
        if (/\p{Script=Han}/u.test(text) && line[1]!.trim() !== "Chinese") failure("中文对白必须使用 [Chinese] 语言标签");
      }
      if (/do not generate speech|no generated speech|no speech or vocalization/i.test(segment.prompt)) failure("有对白的 H3 方案不能同时禁止生成对白；独立配音只在后期替换原人声");
    }
    const sameMedia = (left: typeof firstFrame, right: typeof firstFrame) => left?.path === right?.path && left?.mimeType === right?.mimeType;
    if (JSON.stringify(images) !== JSON.stringify(request.images ?? []) || !sameMedia(firstFrame, request.firstFrame) || !sameMedia(lastFrame, request.lastFrame) || request.audios?.length || request.videos?.length) failure("H3 执行素材、首尾帧槽位或顺序与采用规划不一致");
  }
}

export async function markPresented(directory: string, id: string, presentation: NonNullable<ProductionTask["presentation"]>) {
  const root = await realpath(directory);
  const target = await resolveWorkspacePath(root, `production/tasks/${id}.json`, true);
  const release = lockWorkspaceFiles([target.path]);
  try {
    const task = await load(root, id);
    task.presentation = presentation;
    await save(root, task);
    return task;
  } finally {
    release();
  }
}

export async function executeTask(directory: string, input: unknown) {
  const args = taskCommandSchema.parse(input);
  const root = await realpath(directory);
  const id = args.action === "prepare" ? `task${digest(args.requestId).slice(0, 32)}` : args.taskId;
  if (args.action === "read") return load(root, id);
  const target = await resolveWorkspacePath(root, `production/tasks/${id}.json`, true);
  const release = lockWorkspaceFiles([target.path]);
  try {
    if (args.action === "prepare") {
      const existing = await load(root, id).catch(error => { if (error.status === 404) return null; throw error; });
      if (existing) {
        if (existing.requestId !== args.requestId || JSON.stringify(existing.plan) !== JSON.stringify(args.plan) || JSON.stringify(existing.input) !== JSON.stringify(args.input)) failure("请求 ID 已用于其他方案");
        return existing;
      }
      await approvedPlan(root, args);
      const provider = await configuredProvider(args.input.request, args.input.type, AbortSignal.timeout(30000), root);
      if (provider.rules?.some(rule => rule.field === "apiKey") && (typeof provider.config.apiKey !== "string" || !provider.config.apiKey.trim())) failure("请先在媒体模型设置中配置供应商 API Key", 400);
      if (!provider.submitMediaTask || !provider.queryMediaTask) failure("供应商尚未提供可恢复任务接口", 400);
      const now = new Date().toISOString();
      const task: ProductionTask = {
        id, requestId: args.requestId, plan: args.plan, input: args.input,
        providerRevision: provider.sourceRevision, configurationHash: configurationHash(provider.config),
        status: "prepared", references: await references(root, args.input.request), outputs: [], createdAt: now, updatedAt: now,
      };
      await save(root, task, true);
      return task;
    }
    const task = await load(root, id);
    if (["completed", "failed", "cancelled"].includes(task.status)) return task;
    if (task.status === "submitting" || (task.status === "needsReview" && !task.providerTaskId)) {
      task.status = "needsReview";
      task.message = "提交结果未知，不能重复提交；请核对供应商任务记录";
      await save(root, task);
      return task;
    }
    // ACT: 每次操作独立超时，不随浏览器/MCP 断连取消远端任务。无页面时通过 refresh 恢复查询。
    const provider = await configuredProvider(task.input.request, task.input.type, AbortSignal.timeout(120000), root);
    // ACT: 查询仅使用原任务编号，允许修复密钥或升级适配器后恢复；提交和取消仍校验原配置。
    if (args.action !== "refresh" && (provider.sourceRevision !== task.providerRevision || configurationHash(provider.config) !== task.configurationHash)) failure("供应商配置或适配代码已变化，请重新核对任务配置");
    if (!provider.submitMediaTask || !provider.queryMediaTask) failure("供应商缺少任务恢复能力", 400);
    if (args.action === "submit") {
      if (task.status !== "prepared") return task;
      await approvedPlan(root, task);
      if (JSON.stringify(await references(root, task.input.request)) !== JSON.stringify(task.references)) failure("参考素材已变化，请重新准备生成方案");
      const { type, ...request } = await prepareRequest(root, task.input.type, task.input.request, readReference, provider.signal);
      task.approval = args.approval;
      task.status = "submitting";
      await save(root, task);
      try {
        const submitted = await provider.submitMediaTask.call(provider, { type, request } as ProviderTaskRequest);
        if (typeof submitted.taskId !== "string" || !submitted.taskId.trim() || submitted.taskId.length > 256) throw new Error("供应商未返回有效任务编号");
        task.providerTaskId = submitted.taskId;
        task.status = "running";
        task.message = undefined;
      } catch (error) {
        task.status = "needsReview";
        const detail = provider.tool.errorMessage(error instanceof Error ? error.message : error);
        task.message = `提交未取得明确回执，请核对供应商状态，禁止自动重提${detail ? `：${detail.slice(0, 1000)}` : ""}`;
      }
      await save(root, task);
      return task;
    }
    if (args.action === "cancel" && task.status === "prepared") {
      task.status = "cancelled";
      await save(root, task);
      return task;
    }
    if (!task.providerTaskId) return task;
    if (args.action === "cancel") {
      if (!provider.cancelMediaTask) failure("供应商不支持远端取消，任务仍可能产生费用", 400);
      task.status = "cancelRequested";
      await save(root, task);
      await provider.cancelMediaTask.call(provider, task.providerTaskId);
      return task;
    }
    const snapshot = await provider.queryMediaTask.call(provider, task.providerTaskId);
    if (snapshot.status === "running") { if (task.status !== "cancelRequested") task.status = "running"; }
    else if (snapshot.status === "failed") { task.status = "failed"; task.message = provider.tool.errorMessage(snapshot.message) || "供应商任务失败"; }
    else if (snapshot.status === "cancelled") task.status = "cancelled";
    else if (snapshot.status === "completed") {
      if (!Array.isArray(snapshot.assets) || !snapshot.assets.length) failure("供应商完成任务但未返回媒体", 502);
      task.status = "downloading";
      await save(root, task);
      for (let index = task.outputs.length; index < snapshot.assets.length; index++) {
        const asset = await assetBytes(snapshot.assets[index]!, task.input.type, provider.signal);
        const extension = asset.mimeType.split("/")[1]!.replace("mpeg", "mp3").replace("quicktime", "mov");
        const path = `assets/production/${task.id}/output${index}.${extension}`;
        const file = await resolveWorkspacePath(root, path, true);
        await mkdir(dirname(file.path), { recursive: true });
        await resolveWorkspacePath(root, path);
        try { await writeAtomic(file.path, asset.bytes, { exclusive: true }); }
        catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
          if (digest(await readFile(file.path)) !== digest(asset.bytes)) failure("任务输出路径已有不同内容，不能覆盖");
        }
        task.outputs.push({ path, mimeType: asset.mimeType, mediaType: task.input.type });
        await save(root, task);
      }
      task.status = "completed";
      task.message = undefined;
    } else failure("供应商返回未知任务状态", 502);
    await save(root, task);
    return task;
  } finally { release(); }
}
