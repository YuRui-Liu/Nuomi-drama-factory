import { createHash } from "node:crypto";
import { dirname } from "node:path";
import { mkdir, readFile, realpath, writeAtomic } from "@toonflow/file";
import type { ProviderTaskRequest } from "@toonflow/providers";
import type { MediaGenerationRequest } from "@toonflow/tools-scaffold/runtime";
import { configuredProvider, prepareRequest, readReference, assetBytes } from "@/utils/media/generation";
import { lockWorkspaceFiles, resolveWorkspacePath } from "@/utils/workspace/files";
import { read as readProduction } from "./operations";
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
  if (task.input.type === "video" && task.input.request.providerId === "runninghub") {
    const request = task.input.request;
    const content = result.record.content;
    if (content.kind !== "h3Plan") failure("H3 视频任务必须绑定已采用的 H3 规划");
    const segment = content.segments.find(item => item.id === request.h3SegmentId);
    if (!segment || segment.mode !== "ref2va" || request.modelId !== "h3RefDirector" || segment.prompt !== request.prompt || segment.requestedSeconds !== request.duration) failure("H3 请求与采用的规划段不一致");
    const frames = 17 * Math.ceil((segment.requestedSeconds * 24 - 5) / 17) + 5;
    if (segment.effectiveFrames !== frames || frames > 360) failure("H3 规划帧数与执行帧格不一致或超过 15 秒");
    const images = [];
    for (const reference of segment.references) {
      if (reference.role !== "subject") failure("当前 H3 执行器只接受图片全参考");
      const source = await readProduction(directory, { action: "read", ref: reference.asset });
      if (!("record" in source) || !source.record || !("media" in source.record.content)) failure("H3 引用缺少媒体成果");
      images.push({ path: source.record.content.media.path, mimeType: source.record.content.media.mimeType });
    }
    if (JSON.stringify(images) !== JSON.stringify(request.images ?? [])) failure("H3 执行素材或顺序与采用规划不一致");
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
    if (provider.sourceRevision !== task.providerRevision || configurationHash(provider.config) !== task.configurationHash) failure("供应商配置或适配代码已变化，请恢复原配置后查询原任务");
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
      } catch {
        task.status = "needsReview";
        task.message = "提交未取得明确回执，请核对供应商状态，禁止自动重提";
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
