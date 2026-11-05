import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import type { CollaborationTask } from "@/api/collaboration";
import * as collaborationApi from "@/api/collaboration";
import { CollaborationTaskCards } from "@/features/agent-runtime/CollaborationTaskCards";
import { useAuthStore } from "@/stores/auth-store";

vi.mock("@/api/collaboration", () => ({
  authorizeCollaborationTask: vi.fn(),
  authorizePreparedExecutionCandidate: vi.fn(),
  controlCollaborationTask: vi.fn(),
  getCollaborationTask: vi.fn(),
  listCollaborationTasks: vi.fn(),
}));

const createdAt = (day: number) => `2026-10-${String(day).padStart(2, "0")}T12:00:00Z`;

function task(overrides: Partial<CollaborationTask> = {}): CollaborationTask {
  const id = overrides.id ?? "task-default";
  const created = overrides.created_at ?? createdAt(18);
  return {
    id,
    revision: 1,
    state: "failed",
    source_request_id: `request-${id}`,
    canvas_id: "canvas-a",
    conversation_id: "conversation-a",
    user_message: "准备当前集的内容",
    intent: "prepare",
    speaker: {
      kind: "adoption",
      binding_revision: 1,
      adoption_id: `member-${id}`,
      adoption_revision: 1,
      profile: { name: `成员-${id}` },
    },
    goal: `任务目标-${id}`,
    acceptance: [],
    max_model_calls: 2,
    scope: { kind: "canvas", canvas_id: "canvas-a", node_ids: [] },
    error_code: "model_error",
    created_at: created,
    updated_at: overrides.updated_at ?? created,
    ...overrides,
  };
}

function renderCards() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReactFlowProvider>
        <CollaborationTaskCards project="project-a" canvas="canvas-a" conversation="conversation-a" active />
      </ReactFlowProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ username: "alice" });
  vi.clearAllMocks();
});

afterEach(() => cleanup());

it("shows the newest task first in a bounded, independently scrollable list and keeps failure and authorization details", async () => {
  const olderFailure = task({ id: "older", created_at: createdAt(17), error_code: "provider_timeout" });
  const newestAuthorization = task({
    id: "newest",
    created_at: createdAt(18),
    state: "awaiting_authorization",
    requested_action: {
      operation: "node.run",
      node_operation: "generate",
      node_id: "portrait-node",
      effect: "生成一张角色立绘",
    },
  });
  vi.mocked(collaborationApi.listCollaborationTasks).mockResolvedValue([olderFailure, newestAuthorization]);
  vi.mocked(collaborationApi.getCollaborationTask).mockImplementation(async (_project, _scope, taskId) =>
    taskId === newestAuthorization.id ? newestAuthorization : olderFailure,
  );

  const { container } = renderCards();

  const list = await screen.findByRole("region", { name: "协作任务列表" });
  expect(list).toHaveClass("max-h-[22vh]", "overflow-y-auto", "overscroll-contain");
  const cards = within(list).getAllByRole("article");
  expect(cards[0]).toHaveTextContent("成员-newest · 等待你授权");
  expect(cards[1]).toHaveTextContent("成员-older · 执行失败");
  expect(cards[1]).toHaveTextContent("恢复未完成，请根据服务端诊断核对任务状态。（provider_timeout）");
  expect(within(cards[0]).getByRole("checkbox", { name: "我确认并授权执行上面这项操作" })).toBeInTheDocument();
  expect(within(cards[0]).getByRole("button", { name: "授权并开始" })).toBeDisabled();
  expect(container.querySelector("section[aria-label='协作任务']")).toBeInTheDocument();
});

it("clamps the task goal until the existing details control is expanded", async () => {
  const goal = "从最新剧本提炼群像关系、视觉重点和本集情绪推进，再交付可核对的创作方案。";
  const currentTask = task({ id: "long-goal", state: "succeeded", goal });
  vi.mocked(collaborationApi.listCollaborationTasks).mockResolvedValue([currentTask]);
  vi.mocked(collaborationApi.getCollaborationTask).mockResolvedValue(currentTask);

  renderCards();

  const goalText = await screen.findByText(goal);
  expect(goalText).toHaveClass("line-clamp-3");
  fireEvent.click(screen.getByRole("button", { name: "详情" }));
  expect(await screen.findByRole("button", { name: "收起" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText(goal)).not.toHaveClass("line-clamp-3");
});

it.each([
  ["run_not_resumable", "当前任务没有可继续的步骤"],
  ["model_submission_unknown", "模型提交结果未知；系统不会重发原付费请求"],
])("shows the safe %s resume diagnostic without retrying the control request", async (code, message) => {
  const blockedTask = task({ id: `resume-${code}`, state: "blocked", error_code: null });
  vi.mocked(collaborationApi.listCollaborationTasks).mockResolvedValue([blockedTask]);
  vi.mocked(collaborationApi.controlCollaborationTask).mockRejectedValue(
    new ApiError("server rejected the resume", 409, {
      detail: { code, message: "server-only detail" },
    }),
  );

  renderCards();

  const resumeButton = await screen.findByRole("button", { name: "检查并恢复" });
  expect(resumeButton).toBeEnabled();
  fireEvent.click(resumeButton);

  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.getByRole("alert")).toHaveTextContent(code);
  expect(screen.getByRole("alert")).not.toHaveTextContent("server-only detail");
  await waitFor(() => expect(screen.queryByRole("button", { name: "检查并恢复" })).not.toBeInTheDocument());
  expect(collaborationApi.controlCollaborationTask).toHaveBeenCalledTimes(1);
});

it.each([
  ["model_submission_unknown", "模型提交结果未知；系统不会重发原付费请求"],
  ["run_not_resumable", "当前任务没有可继续的步骤"],
])("honors the server's non-resumable DTO reason %s without offering another request", async (reason, message) => {
  const blockedTask = task({
    id: `dto-${reason}`,
    state: "blocked",
    error_code: null,
    resume_available: false,
    resume_blocked_reason: reason,
  });
  vi.mocked(collaborationApi.listCollaborationTasks).mockResolvedValue([blockedTask]);

  renderCards();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(message);
  expect(alert).toHaveTextContent(reason);
  expect(screen.queryByRole("button", { name: "检查并恢复" })).not.toBeInTheDocument();
  expect(collaborationApi.controlCollaborationTask).not.toHaveBeenCalled();
});

it("explains an invalid preparation contract from the run's safe code without exposing raw details", async () => {
  const blockedTask = task({
    id: "invalid-preparation-contract",
    state: "blocked",
    error_code: null,
    resume_available: false,
    resume_blocked_reason: "invalid_contract",
    run: {
      error: {
        code: "invalid_contract",
        message: "引用索引不属于原委托",
        details: { input_refs: ["private-raw-reference"] },
      },
    },
  });
  vi.mocked(collaborationApi.listCollaborationTasks).mockResolvedValue([blockedTask]);

  renderCards();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("准备动作参数或引用范围不符合原委托授权");
  expect(alert).toHaveTextContent("invalid_contract");
  expect(alert).not.toHaveTextContent("引用索引不属于原委托");
  expect(alert).not.toHaveTextContent("private-raw-reference");
  expect(screen.queryByRole("button", { name: "检查并恢复" })).not.toBeInTheDocument();
});

it("blocks a continuation with an unknown model submission without retrying it", async () => {
  const blockedTask = task({
    id: "continuation-unknown",
    state: "blocked",
    error_code: null,
    continuations: [{
      id: "continuation-1",
      state: "blocked",
      run_id: "run-1",
      candidate: {
        canvas_id: "canvas-a",
        node_id: "image-node",
        node_type: "imageGenNode",
        operation: "generate",
        max_node_runs: 1,
        source_operation_id: "operation-1",
        request_id: "request-1",
      },
      source: {},
      error_code: "model_submission_unknown",
    }],
  });
  vi.mocked(collaborationApi.listCollaborationTasks).mockResolvedValue([blockedTask]);

  renderCards();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("模型提交结果未知；系统不会重发原付费请求");
  expect(alert).toHaveTextContent("model_submission_unknown");
  expect(screen.queryByRole("button", { name: "检查并恢复资产图续跑" })).not.toBeInTheDocument();
  expect(collaborationApi.controlCollaborationTask).not.toHaveBeenCalled();
});
