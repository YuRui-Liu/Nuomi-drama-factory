import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { CraftDetails, CraftReportPanel, craftStageLabel } from "./craft-report";
const api = vi.hoisted(() => ({ getGenerationCraft: vi.fn(), getRewriteCraft: vi.fn() }));
vi.mock("./api", () => ({ scriptCreationApi: api }));

it("shows evidenced blockers and last draft separately, with stale warning", () => {
  render(<CraftDetails stale quality={{ status: "needs_revision" }} records={[
    { stage: "draft", output: { markdown: "第一稿" }, attempts: 1, elapsed_seconds: 1 },
    { stage: "structure_repair_1", output: { markdown: "保留的最终草稿" }, attempts: 1, elapsed_seconds: 1 },
    { stage: "structure_review_1", output: { summary: "动机断裂", coverage: ["人物目标"], issues: [{ id: "i1", severity: "blocker", basis: "context", dimension: "人物动机", quote: "忽然离开", start: 3, end: 7, reason: "缺少触发", repair_goal: "补上触发事件", reference_quote: "他承诺留下" }] }, attempts: 1, elapsed_seconds: 1 },
  ]} />);
  expect(screen.getByText("需要修改")).toBeInTheDocument();
  expect(screen.getByText("保留的最终草稿")).toBeVisible();
  expect(screen.queryByText("第一稿")).not.toBeInTheDocument();
  expect(screen.getByText(/参考依据：他承诺留下/)).toBeInTheDocument();
  expect(screen.getByText(/需要重新审查/)).toBeInTheDocument();
  expect(screen.getByText("修订目标：补上触发事件")).toBeInTheDocument();
});
it("marks legacy reports unreviewed", async () => {
  api.getRewriteCraft.mockResolvedValue({ owner_id: "old", status: "completed", stale: false, steps: [], records: [] });
  render(<CraftReportPanel project="p" ownerId="old" kind="rewrite" status="completed" />);
  expect(await screen.findByText("未审查")).toBeInTheDocument();
});
it("labels stages in terms of writing progress", () => {
  expect(craftStageLabel("draft")).toBe("正在起草");
  expect(craftStageLabel("structure_review_1")).toBe("结构审查");
  expect(craftStageLabel("structure_repair_1")).toBe("正在修订");
  expect(craftStageLabel("dialogue_review")).toBe("对白审查");
});
it("opens author repair evidence without treating it as QC approval", async () => {
  const user = userEvent.setup();
  render(<CraftDetails quality={{ status: "needs_revision" }} records={[{ stage: "repair", session_mode: "replay", output: { repair_notes: [{ issue_id: "i", diagnosis: "缺少触发", strategy: "增加来信", changed_quote: "读完信，他离开了" }] }, elapsed_seconds: 1, attempts: 1 }]} />);
  await user.click(screen.getByText("作者修订策略与证据 · 第 1 轮"));
  expect(screen.getByText("修订策略：增加来信")).toBeVisible();
  expect(screen.getByText("修改证据：读完信，他离开了")).toBeVisible();
  expect(screen.getByText("需要修改")).toBeVisible();
  expect(screen.getByText("历史重放创作会话")).toBeVisible();
  expect(screen.queryByText("创作审查通过")).not.toBeInTheDocument();
});
