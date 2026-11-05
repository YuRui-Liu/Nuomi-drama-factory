import { t } from "@/lib/i18n";
import { basename, dirname, isAbsolute, relative, resolve } from "node:path";
import { realpath, stat, mkdir, readdir, lstat, rm, rmdir, readFile } from "@toonflow/file";
import { z } from "zod";
import { productionExecuteSchema, productionReadSchema } from "@toonflow/node-shared/production/operations";
import * as production from "@/utils/production/operations";
import { taskCommandSchema, type ProductionTask } from "@/utils/production/taskSchema";
import { executeTask, markPresented } from "@/utils/production/tasks";
import { detectMimeType } from "@/utils/media/priceReference";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { CanvasContext } from "@toonflow/tools-scaffold/runtime";
import type { McpTool } from "@toonflow/mcp";
import { createAgentTools } from "@/agent/tools";
import { run as runAgent } from "@/agent";
import conf from "@/utils/conf";
import { callControl, getConnection, listConnections } from "@/utils/mcp/control";
import { appOperations, runAppOperation } from "@/utils/mcp/operations";
import { listTools } from "@/utils/plugins/tools";
import { isWithin, lockWorkspaceFiles, protectWorkspaceRoot, renameWorkspaceFile, resolveWorkspacePath, writeWorkspaceFile } from "@/utils/workspace/files";
import { getRuntimePlatform } from "@/lib/platform";
import { thinkingLevels } from "@/lib/modelReasoning";

const targetSchema = z.strictObject({ connectionId: z.uuid().optional(), directory: z.string().min(1).max(4096).optional(), canvasId: z.string().min(1).max(256).optional() });
const requestSchema = z.strictObject({ target: targetSchema.optional(), args: z.record(z.string(), z.unknown()) });
let authorizationController = new AbortController();
for (const key of ["settings.mcp.enabled", "settings.mcp.token"] as const) conf.onDidChange(key, () => {
  authorizationController.abort();
  authorizationController = new AbortController();
});

async function resolveDirectory(directory?: string) {
  if (!directory || !isAbsolute(directory)) throw new Error("请在 target.directory 指定绝对工作目录，或先打开项目");
  const path = await realpath(directory);
  if (!(await stat(path)).isDirectory()) throw new Error("工作目录不是文件夹");
  if (!getRuntimePlatform().local) {
    const root = await realpath(resolve(dirname(conf.path), "workspaces"));
    if (!isWithin(root, path)) throw new Error("服务器部署只能使用 data/workspaces 内的工作区");
  }
  return path;
}

async function resolveTarget(target: z.infer<typeof targetSchema> = {}, requireDirectory = true) {
  const requestedDirectory = target.directory ? await resolveDirectory(target.directory) : undefined;
  const connection = getConnection(target.connectionId, requestedDirectory);
  if (target.connectionId && requestedDirectory && connection?.state.directory !== requestedDirectory) throw new Error("目标页面的工作区已切换，请重新获取 getAppState");
  if (target.canvasId && connection?.state.canvasId !== target.canvasId) throw new Error("目标画布已切换，请重新获取 getAppState");
  const directory = requireDirectory ? requestedDirectory ?? await resolveDirectory(connection?.state.directory ?? undefined) : undefined;
  return { connection, directory };
}

function wrapTool(name: string, description: string, schema: object, execute: (args: Record<string, unknown>, target: z.infer<typeof targetSchema>, signal: AbortSignal) => Promise<unknown>): McpTool {
  // 插件 JSON Schema 的根引用在包裹后仍指向原来的参数对象。
  const argsSchema = JSON.parse(JSON.stringify(schema).replace(/"\$ref":"#(?=\/|")/g, '"$ref":"#/properties/args'));
  return {
    name, description,
    inputSchema: { type: "object", properties: { target: z.toJSONSchema(targetSchema), args: argsSchema }, required: ["args"], additionalProperties: false },
    async execute(input, signal) {
      const { args, target = {} } = requestSchema.parse(input);
      return execute(args, target, signal);
    },
  };
}

const uiSchemas = {
  openProject: z.strictObject({ directory: z.string().min(1).max(4096) }),
  switchPanel: z.strictObject({ panel: z.enum(["canvas", "document"]) }),
  getDocument: z.strictObject({}),
  openDocument: z.strictObject({ path: z.string().max(4096).optional(), canvasPath: z.string().max(4096).optional(), nodeId: z.string().max(256).optional(), handleId: z.string().max(256).optional() }),
  writeDocument: z.strictObject({ text: z.string().max(10_000_000), expectedText: z.string().max(10_000_000) }),
  getSettings: z.strictObject({}),
  updateSettings: z.strictObject({ patch: z.record(z.string(), z.json()).refine(patch => !["mcp", "stores"].some(key => Object.hasOwn(patch, key)), "不能通过 MCP 修改连接凭证或项目列表") }),
};
const uiDescriptions: Record<keyof typeof uiSchemas, string> = {
  openProject: "在目标 Toonflow 页面打开已有工作目录，并等待工作区就绪；操作前获取 getAppState 的 connectionId。",
  switchPanel: "切换工作区的 canvas 画布或 document 文档面板，先保存当前编辑。",
  getDocument: "读取当前文档内容和选择状态。writeDocument 必须携带本次读取的 text 作为 expectedText。",
  openDocument: "打开工作区中的文档文件(path)，或画布中的文本节点(canvasPath、nodeId，可选handleId)。",
  writeDocument: "通过文档编辑器保存当前文档；expectedText 必须匹配当前内容，防止覆盖用户新输入。",
  getSettings: "读取页面当前设置，访问凭证脱敏。",
  updateSettings: "合并保存指定顶层设置项，同时更新页面；不修改 mcp 凭证与 stores 项目列表，嵌套设置应先读取再合并。",
};

export function redactSecrets(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactSecrets);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, /(?:api.?key|access.?token|refresh.?token|password|secret|authorization)$|^token$/i.test(key) ? (item ? "[REDACTED]" : "") : redactSecrets(item)]));
}

async function assertFileNotOpen(directory: string, path: string) {
  const target = (await resolveWorkspacePath(directory, relative(directory, resolve(directory, path)), true)).path;
  for (const { state } of listConnections()) {
    if (state.directory !== directory) continue;
    const document = state.document as { selection?: { filePath?: string; canvasPath?: string } } | undefined;
    const openPaths = [state.canvasId, document?.selection?.filePath, document?.selection?.canvasPath];
    for (const file of openPaths) {
      if (!file) continue;
      const opened = (await resolveWorkspacePath(directory, relative(directory, resolve(directory, file)), true)).path;
      if (isWithin(target, opened)) throw new Error("文件正在 Toonflow 中打开，请使用画布或文档工具修改，关闭后再执行文件操作");
    }
  }
}

type CanvasNodeSummary = { type?: string; label?: string; position?: { x: number; y: number } };
// ACT: 只读取节点类型、名称和坐标用于分区避让；单次最多翻 20 页，超出时按已读节点避让。
async function canvasNodeSummaries(connectionId: string, directory: string, signal: AbortSignal) {
  const ids: string[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < 20; page++) {
    const result = await callControl(connectionId, "findCanvasNodes", { limit: 100, ...(cursor ? { cursor } : {}) }, signal, directory) as
      { nodes?: { id?: string }[]; hasMore?: boolean; nextCursor?: string | null } | undefined;
    for (const node of result?.nodes ?? []) if (node.id) ids.push(node.id);
    cursor = result?.hasMore ? result.nextCursor ?? undefined : undefined;
    if (!cursor) break;
  }
  const nodes: CanvasNodeSummary[] = [];
  for (let index = 0; index < ids.length; index += 20) {
    const result = await callControl(connectionId, "getCanvasNodes", { nodeIds: ids.slice(index, index + 20), fields: ["label", "type", "position"] }, signal, directory) as
      { nodes?: CanvasNodeSummary[] } | undefined;
    for (const node of result?.nodes ?? []) {
      if (typeof node.position?.x === "number" && typeof node.position?.y === "number") nodes.push(node);
    }
  }
  return nodes;
}

// ACT: 任务完成后自动把视频成果放到当前画布可见位置；重复呈现由任务记录的 presentation 拦住，失败不改变任务结果。
async function presentTaskVideos(connectionId: string, directory: string, task: ProductionTask, signal: AbortSignal) {
  if (task.status !== "completed" || task.presentation || task.input.type !== "video") return;
  const outputs = task.outputs.filter(output => output.mediaType === "video");
  if (!outputs.length) return;
  const canvas = await callControl(connectionId, "getCanvas", {}, signal, directory) as
    { canvasId?: string | null; viewport?: { x: number; y: number; zoom: number } } | undefined;
  if (!canvas?.canvasId) return;
  const nodes = await canvasNodeSummaries(connectionId, directory, signal).catch(() => [] as CanvasNodeSummary[]);
  const shots = nodes.filter(node => node.type === "remote-videoNode" && node.label?.startsWith("视频成片"));
  // ACT: 成片区沿用已有成片节点的起始位置，没有时放到画布最下方；按镜号每行 4 个，节点实际尺寸未公开，用 520x360 网格估算避免重叠。
  const baseX = shots.length ? Math.min(...shots.map(node => node.position!.x)) : 20;
  const baseY = shots.length ? Math.min(...shots.map(node => node.position!.y)) : Math.max(0, ...nodes.map(node => node.position?.y ?? 0)) + 360;
  const placeShot = (shot: number) => {
    const column = shot > 0 ? (shot - 1) % 4 : 0;
    const row = shot > 0 ? Math.floor((shot - 1) / 4) : 0;
    const position = { x: baseX + column * 520, y: baseY + row * 360 };
    while (nodes.some(node => Math.abs(node.position!.x - position.x) < 470 && Math.abs(node.position!.y - position.y) < 350)) position.y += 360;
    nodes.push({ position });
    return position;
  };
  const label = task.input.request.h3SegmentId ?? task.requestId;
  const shotNumber = Number((task.input.request.h3SegmentId ?? "").match(/s(\d+)$/i)?.[1] ?? 0);
  const nodeIds: string[] = [];
  for (const [index, output] of outputs.entries()) {
    const added = await callControl(connectionId, "addNode", {
      type: "remote-videoNode",
      position: placeShot(shotNumber + index),
      label: `视频成片 · ${label}`,
    }, signal, directory) as { node?: { id?: string } } | undefined;
    const nodeId = added?.node?.id;
    if (!nodeId) continue;
    for (let attempt = 0; attempt < 10; attempt++) {
      const tools = await callControl(connectionId, "getNodeTools", { nodeIds: [nodeId] }, signal, directory) as
        { nodeTools?: { name?: string }[] } | undefined;
      if (tools?.nodeTools?.some(item => item.name === "node:setVideo")) {
        await callControl(connectionId, "nodeTools", { nodeId, name: "node:setVideo", args: { path: output.path, mimeType: output.mimeType } }, signal, directory);
        nodeIds.push(nodeId);
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 300));
    }
  }
  if (nodeIds.length) await markPresented(directory, task.id, { canvasId: canvas.canvasId, nodeIds });
}

export async function getMcpTools(): Promise<McpTool[]> {
  const authorizationSignal = authorizationController.signal;
  let pluginError: string | undefined;
  const tools: McpTool[] = [{
    name: "getAppState", description: "列出连接的 Toonflow 页面及其 connectionId、工作目录、画布、项目列表和节点能力。多个页面时必须用 target.connectionId 明确操作对象；无页面连接时只有显式 target.directory 的服务端工具可用。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    async execute() {
      const workspaceRoot = resolve(dirname(conf.path), "workspaces");
      await mkdir(workspaceRoot, { recursive: true });
      return { connections: listConnections(), workspaceRoot, ...(pluginError ? { pluginError } : {}) };
    },
  }];
  tools.push(wrapTool("productionRead", "读取版本化制作成果：list 分页返回摘要，read 返回指定修订及采用状态，readDefaultStyle 读取作品默认风格，listAffected 查询下游影响。不会启动生成。", z.toJSONSchema(productionReadSchema, { io: "input" }), async (args, target) => {
    const { directory } = await resolveTarget(target);
    return production.read(directory!, args);
  }));
  tools.push(wrapTool("productionExecute", "保存制作成果新修订、采用指定修订，或用 setDefaultStyle 设置已采用的作品默认风格。保存须提供 expectedRevision；采用须提供 expectedAdoption 及真实确认或委托依据；设置默认风格须提供 expectedDefault。只记录成果，不发起媒体生成。", z.toJSONSchema(productionExecuteSchema, { io: "input" }), async (args, target) => {
    const { directory } = await resolveTarget(target);
    return production.execute(directory!, args);
  }));
  tools.push(wrapTool("productionTask", "持久制作任务：prepare 固定采用方案与素材；submit 在用户授权后提交；read 读取本地记录；refresh 查询原任务并保存结果；cancel 请求取消。仅支持明确提供恢复接口的供应商。needsReview 且无远端编号时禁止重提。视频任务完成后会在当前画布自动生成视频节点并绑定成果。", z.toJSONSchema(taskCommandSchema, { io: "input" }), async (args, target, signal) => {
    const { connection, directory } = await resolveTarget(target);
    const task = await executeTask(directory!, args);
    if (connection) await presentTaskVideos(connection.id, directory!, task, signal).catch(error => console.warn("制作任务成果自动呈现失败", error));
    return task;
  }));
  const mediaSchema = z.strictObject({ path: z.string().min(1).max(4096) });
  tools.push(wrapTool("productionMedia", "读取工作区实际图片或音频，以 MCP 原生媒体内容返回，供视听检查。单文件最多 10MB；视频须先通过现有 FFmpeg 工具提取帧和音轨。读取成功不代表验收通过；客户端无法感知媒体时必须记录未验证。", z.toJSONSchema(mediaSchema), async (args, target, signal) => {
    const { path } = mediaSchema.parse(args);
    const { directory } = await resolveTarget(target);
    const source = await resolveWorkspacePath(directory!, path);
    const info = await stat(source.path);
    const maxBytes = 10 * 1024 * 1024;
    if (!info.isFile() || info.size === 0 || info.size > maxBytes) throw new Error("媒体须为不超过 10MB 的非空普通文件");
    const bytes = await readFile(source.path, { signal });
    if (bytes.length > maxBytes) throw new Error("媒体超过 10MB，请先提取较短音频或缩小图片");
    const mimeType = detectMimeType(bytes, "");
    const type = ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(mimeType) ? "image"
      : ["audio/wav", "audio/mpeg", "audio/flac", "audio/aac", "audio/ogg"].includes(mimeType) ? "audio" : undefined;
    if (!type) throw new Error("不支持直接读取该媒体格式；视频请先提取帧和音轨");
    return { content: [
      { type: "text", text: JSON.stringify({ path, mimeType, bytes: bytes.length, sha256: new Bun.CryptoHasher("sha256").update(bytes).digest("hex") }) },
      { type, mimeType, data: bytes.toString("base64") },
    ] };
  }));
  for (const [name, schema] of Object.entries(uiSchemas)) {
    tools.push(wrapTool(name, uiDescriptions[name as keyof typeof uiSchemas], z.toJSONSchema(schema), async (input, target, signal) => {
      const args = schema.parse(input);
      const { connection, directory } = await resolveTarget(target, !["openProject", "getSettings", "updateSettings"].includes(name));
      if (!connection) throw new Error("请先打开 Toonflow 桌面或网页");
      if (name === "openProject") await resolveDirectory((args as { directory: string }).directory);
      const result = await callControl(connection.id, name, args, signal, directory);
      return name === "getSettings" || name === "updateSettings" ? redactSecrets(result) : result;
    }));
  }
  const canvasStub: CanvasContext = { id: "mcp", tools: [], async call() { throw new Error("尚未绑定画布"); } };
  // ACT: 插件损坏时仍保留应用管理工具，允许读取错误并修复插件。
  const definitions = await createAgentTools(dirname(conf.path), canvasStub, undefined, false).catch(error => {
    pluginError = error instanceof Error ? error.message : String(error);
    return [];
  });
  for (const definition of definitions) {
    if (tools.some(tool => tool.name === definition.name)) throw new Error(t`MCP 工具名称重复：${definition.name}`);
    tools.push(wrapTool(definition.name, [definition.description, ...(definition.promptGuidelines ?? [])].join("\n"), definition.parameters, async (args, target, signal) => {
      const { connection, directory } = await resolveTarget(target);
      if (["write", "edit"].includes(definition.name) && typeof args.path === "string") await assertFileNotOpen(directory!, args.path);
      const canvas: CanvasContext | undefined = connection ? {
        id: connection.state.canvasId ?? "mcp", tools: connection.state.tools,
        call: (request, callSignal) => callControl(connection.id, request.name, request.args, callSignal ?? signal, directory),
      } : undefined;
      const current = (await createAgentTools(directory!, canvas, undefined, false)).find(tool => tool.name === definition.name);
      if (!current) throw new Error("工具已禁用，或所需 Toonflow 页面未连接，请重新读取工具列表");
      // ACT: 现有插件依赖 createTools 注入的宿主能力；MCP 没有 Pi 对话，访问会话能力时明确报错。
      const context = new Proxy({ cwd: directory, mode: "rpc", hasUI: false, model: undefined, signal }, {
        get(value, key) { if (Reflect.has(value, key)) return Reflect.get(value, key); throw new Error(t`MCP 不提供内置 Agent 会话能力：${String(key)}`); },
      }) as unknown as ExtensionContext;
      return current.execute(crypto.randomUUID(), args, signal, undefined, context);
    }));
  }
  const fileSchema = z.strictObject({
    action: z.enum(["list", "mkdir", "rename", "remove", "readBinary", "writeBinary"]), path: z.string().max(4096).default(""),
    target: z.string().max(4096).optional(), recursive: z.boolean().default(false),
    base64: z.string().max(28_000_000).base64().optional(), exclusive: z.boolean().default(true),
  });
  tools.push(wrapTool("workspaceFiles", "列出、创建目录、重命名或删除工作区文件；readBinary/writeBinary通过base64传输不超过20MB的媒体，写入默认不覆盖。文本读写复用已启用的文件工具；打开中的画布或文档必须通过专门工具修改。", z.toJSONSchema(fileSchema, { io: "input" }), async (input, target, signal) => {
    const args = fileSchema.parse(input);
    const workspaceTool = (await listTools()).find(tool => tool.name === "workspace");
    if (!workspaceTool?.enabled || workspaceTool.loadError) throw new Error("工作区文件工具未启用或加载失败");
    if (!["list", "readBinary"].includes(args.action) && "readOnly" in workspaceTool.config && workspaceTool.config.readOnly === true) throw new Error("当前工作区文件工具为只读模式");
    const { directory } = await resolveTarget(target);
    const source = await resolveWorkspacePath(directory!, args.path);
    signal.throwIfAborted();
    if (args.action === "list") return (await readdir(source.path, { withFileTypes: true })).filter(item => item.isFile() || item.isDirectory()).map(item => ({ name: item.name, type: item.isDirectory() ? "directory" : "file" }));
    if (args.action === "readBinary") {
      const info = await stat(source.path);
      if (!info.isFile() || info.size > 20 * 1024 * 1024) throw new Error("文件必须是不超过20MB的普通文件");
      return { path: args.path, base64: (await readFile(source.path, { signal })).toString("base64") };
    }
    protectWorkspaceRoot(directory!, source.path);
    await assertFileNotOpen(directory!, args.path);
    const destination = args.action === "rename" && args.target ? await resolveWorkspacePath(directory!, args.target) : undefined;
    if (args.action === "rename" && !destination) throw new Error("重命名需要提供 args.target");
    if (destination) {
      protectWorkspaceRoot(directory!, destination.path);
      destination.path = resolve(dirname(destination.path), basename(resolve(directory!, args.target!)));
    }
    const release = lockWorkspaceFiles([source.path, ...(destination ? [destination.path] : [])]);
    try {
      if (args.action === "writeBinary") {
        if (args.base64 === undefined) throw new Error("写入二进制文件需要 base64");
        const bytes = Buffer.from(args.base64, "base64");
        if (bytes.length > 20 * 1024 * 1024) throw new Error("文件不能超过20MB");
        await writeWorkspaceFile(source.path, bytes, args.exclusive);
      } else if (args.action === "mkdir") await mkdir(source.path);
      else if (destination) await renameWorkspaceFile(source.path, destination.path);
      else if ((await lstat(source.path)).isDirectory() && !args.recursive) await rmdir(source.path);
      else await rm(source.path, { recursive: args.recursive });
    } finally { release(); }
    return { success: true };
  }));
  tools.push(wrapTool("listAppOperations", "按需查询插件、媒体供应商、素材库和 Agent 历史管理操作及其参数。先查询 schema，再调用 appOperation。", z.toJSONSchema(z.strictObject({ name: z.string().optional() })), async args => {
    return appOperations.filter(item => !args.name || item.name === args.name).map(({ name, description, parameters, path }) => {
      const schema = z.toJSONSchema(parameters);
      if (path.startsWith("/api/agent/")) {
        delete schema.properties?.directory;
        schema.required = schema.required?.filter(key => key !== "directory");
      }
      return { name, description, parameters: schema };
    });
  }));
  tools.push(wrapTool("appOperation", "执行 listAppOperations 公布的应用管理操作，parameters 必须符合对应 schema。文件与节点操作使用专门工具；安装来源、覆盖和卸载须符合用户请求。", z.toJSONSchema(z.strictObject({ name: z.string(), parameters: z.record(z.string(), z.json()) })), async (args, target, signal) => {
    const operation = appOperations.find(item => item.name === args.name);
    if (!operation) throw new Error("应用操作不存在，请查询 listAppOperations");
    const parameters = { ...args.parameters as Record<string, unknown> };
    if (operation.path.startsWith("/api/agent/")) parameters.directory = (await resolveTarget(target)).directory;
    const result = await runAppOperation(operation.name, parameters, signal);
    const refreshErrors: string[] = [];
    if (operation.refresh) {
      const name = operation.refresh.nameField ? parameters[operation.refresh.nameField] : (result as { name?: string } | null)?.name;
      for (const connection of listConnections()) {
        try { await callControl(connection.id, "refreshResources", {
          type: operation.refresh.type, name,
          ...(operation.name === "deleteMediaProvider" ? { removedProviderId: (parameters.fileName as string).slice(0, -3) } : {}),
        }, signal); }
        catch (error) { refreshErrors.push(error instanceof Error ? error.message : String(error)); }
      }
    }
    return { result: redactSecrets(result), ...(refreshErrors.length ? { refreshErrors } : {}) };
  }));
  const runAgentSchema = z.strictObject({
    prompt: z.string().trim().min(1), providerId: z.string().min(1), modelId: z.string().min(1),
    sessionFile: z.string().regex(/^[\w-]+\.jsonl$/).optional(), thinkingLevel: z.enum(thinkingLevels).optional(),
  });
  tools.push(wrapTool("runAgent", "按用户请求调用 Toonflow 内置 Agent，等待本轮完成并返回对话文件与回复；会使用配置的模型。外部 Agent 可直接操作其他工具，仅需要委托内置 Agent 时调用。支持 MCP 取消，历史保存到工作区。", z.toJSONSchema(runAgentSchema), async (input, target, signal) => {
    const args = runAgentSchema.parse(input);
    const { directory, connection } = await resolveTarget(target);
    const canvas: CanvasContext | undefined = connection ? {
      id: connection.state.canvasId ?? "mcp", tools: connection.state.tools,
      call: (request, callSignal) => callControl(connection.id, request.name, request.args, callSignal ?? signal, directory),
    } : undefined;
    const blocks = new Map<string, string>();
    let sessionFile = args.sessionFile;
    await runAgent({ ...args, cwd: directory!, canvas, signal }, event => {
      if (event.type === "session") sessionFile = event.file;
      if (event.type === "text") blocks.set(event.blockId, event.content ?? (blocks.get(event.blockId) ?? "") + (event.delta ?? ""));
    });
    return { sessionFile, text: [...blocks.values()].join("\n") };
  }));
  return tools.map(tool => ({
    ...tool,
    execute(input, signal) {
      const requestSignal = AbortSignal.any([signal, authorizationSignal]);
      requestSignal.throwIfAborted();
      return tool.execute(input, requestSignal);
    },
  }));
}
