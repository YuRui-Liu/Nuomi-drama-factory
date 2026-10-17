const handoffCodeMessages: Record<string, string> = {
  project_context_required: "请先打开或创建项目工作区后重试。",
  INVALID_EPISODE: "集数无效，请返回剧本工作区后重试。",
  SCRIPT_DOCUMENT_NOT_FOUND: "剧本文档已不存在，请刷新剧本列表后重试。",
  ADOPTED_SCRIPT_REVISION_REQUIRED: "当前剧本版本尚未采用或已变化，请先采用最新保存版本，再重新预览交接。",
  DIRECTOR_HANDOFF_PREVIEW_STALE: "剧本来源已在预览后更新，请重新打开交接预览并确认后重试。",
  DIRECTOR_HANDOFF_MUTATION_CONFLICT: "这次交接请求与已提交记录不一致，请刷新工作区后重新提交。",
  DIRECTOR_WORKSPACE_NEEDS_REVIEW: "提交导演前需要完成当前版本的一致性检查，并处理未确认的事实问题。",
};

const handoffConflictReasons: Record<string, string> = {
  "prepare mutation id reused with different payload": "同一交接请求标识已对应其他内容，请刷新工作区后重新提交。",
  "script revision changed": "剧本版本已变化，请重新打开交接预览并确认后重试。",
  "reference revision changed": "交接引用版本已变化，请刷新引用与交接预览后重试。",
  "selected entity or asset changed": "交接所用的角色或资产引用已变化，请刷新交接预览后重试。",
  "selected relation is missing or stale": "交接所用的角色关系已变化，请刷新交接预览后重试。",
  "selected relation target changed": "交接所用的角色关系目标已变化，请刷新交接预览后重试。",
  "written appearance reference is stale": "交接所用的外观引用已变化，请刷新交接预览后重试。",
  "selected entities changed": "交接所用的角色或资产引用已变化，请刷新交接预览后重试。",
  "fact acknowledgement changed": "事实核对状态已变化，请重新运行一致性检查并再次确认交接。",
  "episode source changed": "剧本来源已变化，请重新打开交接预览并确认后重试。",
  "project source revision changed": "项目剧本来源已更新，请重新打开交接预览并确认后重试。",
  "handoff already confirmed with another mutation": "这次交接已由另一提交完成，请刷新剧本工作区查看最新状态。",
  "handoff cannot be confirmed": "交接状态已变化，请刷新预览后重新提交。",
  "handoff dispatch superseded": "交接任务状态已更新，请刷新工作区查看最新进度。",
  "handoff consumer superseded": "交接任务状态已更新，请刷新工作区查看最新进度。",
  "handoff source superseded": "剧本来源已变化，请重新打开交接预览并确认后重试。",
};

const handoffValidationReasons: Record<string, string> = {
  "selected scene ids require a reliable current parse": "当前剧本无法可靠识别场景，无法按所选场景范围更新；请改为更新全部场景，或先修正场次标题。",
  "empty screenplay cannot be handed off": "剧本正文为空，请补充内容后再交接。",
  "handoff requires a saved episode script": "请先保存并采用本集剧本，再提交交接。",
};

const standaloneHandoffReasonMessages: Record<string, string> = {
  "a current completed actual consistency check is required": "提交导演前需要完成当前版本的一致性检查。",
  "current consistency checks contain unresolved fact issues": "当前版本的一致性检查发现未处理的事实问题，请先处理或记录理由后再交接。",
};

type ErrorLike = {
  name?: unknown;
  message?: unknown;
  status?: unknown;
  body?: unknown;
  cause?: unknown;
  response?: {
    status?: unknown;
    json?: () => Promise<unknown>;
    clone?: () => { json?: () => Promise<unknown> };
  };
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? value as Record<string, unknown> : null;
}

function errorStatus(error: ErrorLike): number | undefined {
  const status = error.status ?? error.response?.status;
  return typeof status === "number" && Number.isFinite(status) ? status : undefined;
}

function errorBody(error: ErrorLike): unknown {
  if (error.body !== undefined) return error.body;
  const cause = asRecord(error.cause);
  if (cause && cause !== error) return errorBody(cause as ErrorLike);
  return undefined;
}

async function responseBody(error: ErrorLike): Promise<unknown> {
  const existingBody = errorBody(error);
  if (existingBody !== undefined) return existingBody;
  const response = error.response;
  if (!response) return undefined;
  try {
    if (response.clone) return await response.clone().json?.();
    return await response.json?.();
  } catch {
    return undefined;
  }
}

function detailCode(body: unknown): string | undefined {
  const record = asRecord(body);
  const detail = asRecord(record?.detail);
  const data = asRecord(record?.data);
  const code = detail?.code ?? record?.code ?? data?.error_code;
  return typeof code === "string" && code.trim() ? code.trim() : undefined;
}

function detailMessage(body: unknown): string | undefined {
  const record = asRecord(body);
  const detail = record?.detail;
  const detailRecord = asRecord(detail);
  const message = detailRecord?.message ?? detailRecord?.error ?? record?.error ?? record?.message;
  if (typeof message === "string" && message.trim()) return message.trim();
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  return undefined;
}

function handoffReasonMessage(code: string | undefined, message: string | undefined): string | undefined {
  if (code === "DIRECTOR_WORKSPACE_CONFLICT") {
    const normalized = message?.trim().toLowerCase();
    return (normalized && handoffConflictReasons[normalized])
      ?? "交接内容或项目来源已变化，请刷新交接预览后重试。";
  }
  if (code === "DIRECTOR_WORKSPACE_INVALID") {
    const normalized = message?.trim().toLowerCase();
    return (normalized && handoffValidationReasons[normalized])
      ?? "交接信息未通过校验，请检查剧本与更新范围后重试。";
  }
  return undefined;
}

function genericHttpError(status: number | undefined): string {
  if (status === 409) return "当前操作存在冲突，输入已保留。请刷新相关状态后重试。";
  if (status === 422) return "提交内容未通过校验，请检查后重试。";
  if (status !== undefined && status >= 500) return "服务暂时不可用，请稍后重试。";
  return "操作未能完成，请稍后重试。";
}

/** Safe synchronous fallback for query error labels that cannot read response bodies. */
export function productionActionErrorFallback(error: unknown): string {
  const record = asRecord(error);
  const status = record ? errorStatus(record as ErrorLike) : undefined;
  const message = typeof record?.message === "string" ? record.message : "";
  const isRawKyFailure = /^HTTPError:\s*Request failed with status code \d+\b/i.test(message)
    && /https?:\/\//i.test(message);
  if (status !== undefined || record?.name === "HTTPError" || isRawKyFailure) {
    const embeddedStatus = isRawKyFailure
      ? Number(/status code (\d+)/i.exec(message)?.[1])
      : undefined;
    return genericHttpError(status ?? (Number.isFinite(embeddedStatus) ? embeddedStatus : undefined));
  }
  if (error instanceof Error && error.message) return error.message;
  return "操作失败，输入已保留，请重试";
}

/** Format production action failures without exposing raw ky URLs or backend prose. */
export async function productionActionErrorMessage(error: unknown): Promise<string> {
  const record = asRecord(error);
  const status = record ? errorStatus(record as ErrorLike) : undefined;
  const body = record ? await responseBody(record as ErrorLike) : undefined;
  const code = detailCode(body);
  const message = detailMessage(body);

  if (code && handoffCodeMessages[code]) return handoffCodeMessages[code];
  const reasonMessage = handoffReasonMessage(code, message);
  if (reasonMessage) return reasonMessage;
  const standaloneReason = typeof record?.message === "string"
    ? standaloneHandoffReasonMessages[record.message.trim().toLowerCase()]
    : undefined;
  if (standaloneReason) return standaloneReason;

  return productionActionErrorFallback(error);
}
