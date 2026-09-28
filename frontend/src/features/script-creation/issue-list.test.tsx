import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { IssueList } from "./issue-list";
import { scriptCreationApi } from "./api";
import type { ConsistencyRun, ScriptDocument } from "./types";

vi.mock("./api", () => ({ scriptCreationApi: {
  listConsistencyRuns: vi.fn(), startConsistencyRun: vi.fn(), getConsistencyRun: vi.fn(),
  listProposals: vi.fn(), markIntentional: vi.fn(), createConsistencyTargets: vi.fn(),
} }));
const api = vi.mocked(scriptCreationApi);
const doc = (id: string, kind: ScriptDocument["kind"]): ScriptDocument => ({
  id, kind, title: id, episode_number: kind === "episode_script" ? 2 : null,
  current_revision_id: "r1", adopted_revision_id: null, source_origin: null, created_at: "", updated_at: "",
  revision: { id: "r1", document_id: id, parent_revision_id: null, markdown: "证据", blocks: [{ id: "b1", markdown: "证据" }],
    client_mutation_id: "c", created_at: "", restored_from_revision_id: null },
});
const episode = doc("episode", "episode_script");
const people = doc("people", "people");
const run: ConsistencyRun = { id: "run", episode_document_id: episode.id,
  context_revisions: { episode: "r1", people: "r1" }, mode: "actual", proposal_id: null,
  hypothetical_document_id: null, status: "completed", task_id: "task", error: null,
  issues: [{ id: "issue", run_id: "run", category: "fact", kind: "character_knowledge",
    explanation: "知情顺序冲突", suggested_action: "修改台词", mode: "actual", proposal_id: null,
    source: { document_id: "people", revision_id: "r1", block_id: "b1", start: 0, end: 2, quote: "证据" },
    target: { document_id: "episode", revision_id: "r1", block_id: "b1", start: 0, end: 2, quote: "证据" },
    context_revisions: { episode: "r1", people: "r1" }, stale: false, intentional_reason: null }],
};
beforeEach(() => {
  vi.clearAllMocks();
  api.listConsistencyRuns.mockResolvedValue([run]);
  api.listProposals.mockResolvedValue([]);
});

it("requires an explicit target, then opens that document for per-document review", async () => {
  const user = userEvent.setup();
  const navigate = vi.fn(); const review = vi.fn();
  api.createConsistencyTargets.mockResolvedValue([{ id: "job", document_id: "episode", status: "pending" } as never]);
  render(<IssueList project="demo" documents={[people, episode]} selected={episode} saved onNavigate={navigate} onReview={review} />);
  await screen.findByText("知情顺序冲突");
  expect(screen.getByRole("button", { name: "生成关联改稿候选" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: "目标文档 episode" }));
  await user.click(screen.getByRole("button", { name: "生成关联改稿候选" }));
  await waitFor(() => expect(api.createConsistencyTargets).toHaveBeenCalledWith("demo", "issue", ["episode"]));
  expect(review).toHaveBeenCalledWith("episode");
});

it("starts actual and hypothetical checks from the same panel and restores runs", async () => {
  const user = userEvent.setup();
  api.listProposals.mockImplementation(async (_project, id) => id === "people" ? [{ id: "proposal", document_id: "people", status: "pending" } as never] : []);
  api.startConsistencyRun.mockResolvedValue({ ...run, status: "pending", issues: [] });
  render(<IssueList project="demo" documents={[people, episode]} selected={episode} saved onNavigate={vi.fn()} onReview={vi.fn()} />);
  await screen.findByText("知情顺序冲突");
  await user.click(screen.getByRole("button", { name: "检查当前集" }));
  expect(api.startConsistencyRun).toHaveBeenCalledWith("demo", expect.objectContaining({ episode_document_id: "episode", proposal_id: null }));
  await user.selectOptions(screen.getByRole("combobox", { name: "待采纳候选" }), "proposal");
  await user.click(screen.getByRole("button", { name: "检查若采纳影响" }));
  expect(api.startConsistencyRun).toHaveBeenCalledWith("demo", expect.objectContaining({ proposal_id: "proposal" }));
});

it("blocks checks while any draft is unsaved and keeps stale or failed results visible", async () => {
  render(<IssueList project="demo" documents={[people, episode]} selected={episode} saved={false}
    onNavigate={vi.fn()} onReview={vi.fn()} />);
  await screen.findByText("知情顺序冲突");
  expect(screen.getByRole("button", { name: "检查当前集" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "生成关联改稿候选" })).toBeDisabled();
});


it("keeps the checked episode visible while navigating to a source document", async () => {
  const first = doc("first", "episode_script");
  const history = { ...run, episode_document_id: "episode" };
  api.listConsistencyRuns.mockResolvedValue([history]);
  const view = render(<IssueList project="demo" documents={[first, people, episode]} selected={episode} saved
    onNavigate={vi.fn()} onReview={vi.fn()} />);
  await screen.findByText("知情顺序冲突");
  view.rerender(<IssueList project="demo" documents={[first, people, episode]} selected={people} saved
    onNavigate={vi.fn()} onReview={vi.fn()} />);
  expect(screen.getByText("知情顺序冲突")).toBeInTheDocument();
});
