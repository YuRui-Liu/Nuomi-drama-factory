import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GroupVideoQualityControls } from "@/components/episode/narrative-workbench/group-video-quality-controls";

const mocks = vi.hoisted(() => ({ post: vi.fn(), refetch: vi.fn(), tasks: [] as unknown[] }));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post } }));
vi.mock("@/lib/api-errors", () => ({ jsonWithBackendError: (value: unknown) => value }));
vi.mock("@/lib/queries/tasks", () => ({ useTasks: () => ({ data: { data: mocks.tasks }, refetch: mocks.refetch }) }));
const stage = { status: "completed" as const, revision: 2, video_asset: "/existing.mp4",
  quality_reviews: [{ status: "failed" as const, review_id: "review", media_fingerprint: "fingerprint",
    evidence: [{ frame_label: "middle", observation: "face distorted" }],
    issues: [{ issue_id: "issue", dimension: "face", description: "distorted", frame_labels: ["middle"] }] }] };
const mount = (value = stage) => render(<QueryClientProvider client={new QueryClient()}>
  <GroupVideoQualityControls project="p" episode={1} groupId="g" stage={value} />
</QueryClientProvider>);

describe("existing video quality workflow", () => {
  beforeEach(() => { mocks.post.mockReset(); mocks.tasks = []; });
  it("queues only the quality endpoint and retains a pending UI", async () => {
    mocks.post.mockResolvedValue({ ok: true, data: { scope: "quality-scope" } });
    mount();
    await userEvent.click(screen.getByRole("button", { name: "检查现有视频" }));
    expect(mocks.post).toHaveBeenCalledWith("api/v1/projects/p/episodes/1/narrative-groups/g/video/quality-check", { json: { revision: 2 } });
    expect(screen.getByRole("button", { name: "检查中…" })).toBeDisabled();
  });
  it("requires a reason and submits immutable review/issue/media binding", async () => {
    mocks.post.mockResolvedValue({ ok: true });
    mount();
    expect(screen.getByRole("button", { name: "保存人工处理记录" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("人工处理问题"), "review:issue");
    await userEvent.type(screen.getByLabelText("人工处理理由"), "核对原始帧，确有畸变");
    await userEvent.click(screen.getByRole("button", { name: "保存人工处理记录" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith(expect.stringContaining("quality-disposition"), { json: {
      revision: 2, review_id: "review", issue_id: "issue", media_fingerprint: "fingerprint",
      reason: "核对原始帧，确有畸变", decision: "acknowledged",
    } }));
    expect(screen.queryByRole("option", { name: "通过" })).not.toBeInTheDocument();
  });
  it("retains an observed running task across completion after remount", async () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const scope = "group_g_video_quality_r2_attempt";
    mocks.tasks = [{ task_type: "narrative_group_video_quality", scope, status: "running" }];
    const view = render(<QueryClientProvider client={client}><GroupVideoQualityControls project="p" episode={1} groupId="g" stage={stage} /></QueryClientProvider>);
    expect(screen.getByRole("button", { name: "检查中…" })).toBeDisabled();
    mocks.tasks = [{ task_type: "narrative_group_video_quality", scope, status: "completed" }];
    view.rerender(<QueryClientProvider client={client}><GroupVideoQualityControls project="p" episode={1} groupId="g" stage={stage} /></QueryClientProvider>);
    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    expect(screen.getByRole("status")).toHaveTextContent("检查结束，结果已更新");
    mocks.tasks = [];
    view.rerender(<QueryClientProvider client={client}><GroupVideoQualityControls project="p" episode={1} groupId="g" stage={stage} /></QueryClientProvider>);
    expect(screen.getByRole("button", { name: "检查现有视频" })).toBeEnabled();
  });
  it("does not confuse revision 2 with revision 20", () => {
    mocks.tasks = [{ task_type: "narrative_group_video_quality", scope: "group_g_video_quality_r20_attempt", status: "running" }];
    mount();
    expect(screen.getByRole("button", { name: "检查现有视频" })).toBeEnabled();
  });
});
