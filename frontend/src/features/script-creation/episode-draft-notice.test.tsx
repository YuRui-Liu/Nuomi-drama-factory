import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { EpisodeDraftNotice } from "./episode-draft-notice";
const api = vi.hoisted(() => ({ listGenerations: vi.fn(), getGenerationCraft: vi.fn() }));
vi.mock("./api", () => ({ scriptCreationApi: api }));
vi.mock("./craft-report", () => ({ CraftReportPanel: ({ ownerId }: { ownerId: string }) => <p>草稿报告 {ownerId}</p> }));
it("shows the selected episode's retained draft and ignores another episode's newer run", async () => {
  api.listGenerations.mockResolvedValue([
    { id: "other", status: "needs_review", steps: [{ kind: "episode_script", episode_number: 3 }] },
    { id: "target", status: "needs_review", steps: [{ kind: "episode_script", episode_number: 2 }] },
  ]);
  render(<EpisodeDraftNotice project="p" episodeNumber={2} revisionKey="r" />);
  expect(await screen.findByText("第 2 集已生成草稿，待修改")).toBeVisible();
  expect(screen.getByText("草稿报告 target")).toBeVisible();
  expect(screen.queryByText("草稿报告 other")).not.toBeInTheDocument();
});
it("applies only the final draft of the selected episode and waits for persistence", async () => {
  api.listGenerations.mockResolvedValue([{ id: "target", status: "needs_review", steps: [{ key: "episode_script:2", kind: "episode_script", episode_number: 2 }] }]);
  api.getGenerationCraft.mockResolvedValue({ records: [
    { step_key: "episode_script:2", output: { markdown: "第一稿" } },
    { step_key: "episode_script:2", output: { markdown: "第二集最终稿" } },
    { step_key: "episode_script:3", output: { markdown: "第三集" } },
  ] });
  const apply = vi.fn().mockResolvedValue(undefined);
  render(<EpisodeDraftNotice project="p" episodeNumber={2} revisionKey="r" onApply={apply} />);
  await userEvent.click(await screen.findByRole("button", { name: "采用最终草稿并覆盖本集正文" }));
  expect(apply).toHaveBeenCalledWith("第二集最终稿");
  expect(await screen.findByText("最终草稿已覆盖本集正文并保存。审查意见仍保留。" )).toBeVisible();
});
it("does not claim success when saving fails", async () => {
  api.listGenerations.mockResolvedValue([{ id: "target", status: "needs_review", steps: [{ key: "episode_script:2", kind: "episode_script", episode_number: 2 }] }]);
  api.getGenerationCraft.mockResolvedValue({ records: [{ step_key: "episode_script:2", output: { markdown: "正文" } }] });
  render(<EpisodeDraftNotice project="p" episodeNumber={2} revisionKey="r" onApply={async () => { throw new Error("版本冲突"); }} />);
  await userEvent.click(await screen.findByRole("button", { name: "采用最终草稿并覆盖本集正文" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("版本冲突");
  expect(screen.queryByText(/最终草稿已覆盖本集正文并保存/)).not.toBeInTheDocument();
});
it("does not resurrect an old review after a newer successful run", async () => {
  api.listGenerations.mockResolvedValue([
    { id: "latest", status: "completed", steps: [{ kind: "episode_script", episode_number: 2 }] },
    { id: "old", status: "needs_review", steps: [{ kind: "episode_script", episode_number: 2 }] },
  ]);
  const { container } = render(<EpisodeDraftNotice project="p" episodeNumber={2} revisionKey="r" />);
  await vi.waitFor(() => expect(api.listGenerations).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});
