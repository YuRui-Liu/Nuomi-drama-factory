import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProposalReview } from "./proposal-review";
import { scriptCreationApi } from "./api";
import userEvent from "@testing-library/user-event";
import type { ScriptDocument, ScriptProposal, RewriteJob } from "./types";

const document: ScriptDocument = {
  id: "d", kind: "episode_script", title: "第一集", episode_number: 1,
  current_revision_id: "r", adopted_revision_id: null, source_origin: null,
  created_at: "", updated_at: "",
  revision: { id: "r", document_id: "d", parent_revision_id: null,
    markdown: "旧句", blocks: [{ id: "b", markdown: "旧句" }],
    client_mutation_id: "create", created_at: "", restored_from_revision_id: null },
};

afterEach(() => vi.restoreAllMocks());

describe("ProposalReview", () => {
  it("disables rewrite and adoption while a draft is unsaved", () => {
    render(<ProposalReview project="demo" document={document} saved={false}
      selection={null} instruction="" onApplied={vi.fn()} />);
    expect(screen.getByRole("button", { name: "生成改稿候选" })).toBeDisabled();
  });

  it("recovers a pending rewrite after the panel remounts and loads its eventual proposal", async () => {
    const pending: RewriteJob = { id: "job", document_id: "d", base_revision_id: "r",
      start: 0, end: 2, scope: "selection", mode: "dialogue", instruction: "",
      preserve: "", before: "旧句", status: "pending", proposal_id: null, error: null };
    const proposal: ScriptProposal = { id: "p", document_id: "d", base_revision_id: "r", block_id: "b",
      start: 0, end: 2, before: "旧句", after: "新句", reason: "自然",
      dependencies: [], context_revisions: {}, round_id: "round", status: "pending",
      source_candidate_id: null, created_at: "" };
    vi.spyOn(scriptCreationApi, "listRewrites").mockResolvedValue([pending]);
    vi.spyOn(scriptCreationApi, "getRewrite").mockResolvedValue({ ...pending, status: "completed", proposal_id: "p" });
    const list = vi.spyOn(scriptCreationApi, "listProposals")
      .mockResolvedValueOnce([]).mockResolvedValue([proposal]);
    const view = render(<ProposalReview project="demo" document={document} saved selection={null}
      instruction="" onApplied={vi.fn()} />);
    await screen.findByText("新句");
    expect(list).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it("continues adjusting the original candidate range despite a different editor selection", async () => {
    const user = userEvent.setup();
    const proposal: ScriptProposal = { id: "p", document_id: "d", base_revision_id: "r", block_id: "b",
      start: 0, end: 2, before: "旧句", after: "新句", reason: "自然",
      dependencies: [], context_revisions: {}, round_id: "round", status: "pending",
      source_candidate_id: null, created_at: "" };
    vi.spyOn(scriptCreationApi, "listProposals").mockResolvedValue([proposal]);
    const start = vi.spyOn(scriptCreationApi, "startRewrite").mockResolvedValue({
      id: "job", document_id: "d", base_revision_id: "r", start: 0, end: 2,
      scope: "selection", mode: "dialogue", instruction: "", preserve: "", before: "旧句",
      status: "pending", proposal_id: null, error: null });
    render(<ProposalReview project="demo" document={document} saved
      selection={{ text: "句", start: 1, end: 2 }} instruction="" onApplied={vi.fn()} />);
    await screen.findByText("新句");
    await user.click(screen.getByRole("button", { name: "继续调整" }));
    await user.click(screen.getByRole("button", { name: "生成改稿候选" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("demo", expect.objectContaining({
      reference_proposal_id: "p", scope: "selection", start: 0, end: 2,
    })));
  });

  it.each(["selection", "scene"] as const)("continues a whole-document patch within its original %s scope", async (originalScope) => {
    const user = userEvent.setup();
    const markdown = originalScope === "selection"
      ? "preA\n\nBpost"
      : "# Episode\n\n### 1-1 DAY INT Room\nA\n\nB\n\n### 1-2 DAY EXT Dock\npost";
    const startOffset = originalScope === "selection" ? 3 : markdown.indexOf("### 1-1");
    const endOffset = originalScope === "selection" ? 7 : markdown.indexOf("### 1-2");
    const original = markdown.slice(startOffset, endOffset);
    const wholeDocument = { ...document, revision: { ...document.revision,
      markdown, blocks: [{ id: "b1", markdown: markdown.slice(0, startOffset) },
        { id: "b2", markdown: markdown.slice(startOffset) }] } };
    const proposal: ScriptProposal = { id: "p", document_id: "d", base_revision_id: "r", block_id: null,
      start: 0, end: Array.from(markdown).length, before: markdown, after: "candidate", reason: "refine",
      dependencies: [], context_revisions: {}, round_id: "job", status: "pending",
      source_candidate_id: null, created_at: "" };
    const sourceJob: RewriteJob = { id: "job", document_id: "d", base_revision_id: "r",
      start: startOffset, end: endOffset, scope: originalScope, mode: "dialogue", instruction: "",
      preserve: "", before: original, status: "completed", proposal_id: "p", error: null };
    vi.spyOn(scriptCreationApi, "listProposals").mockResolvedValue([proposal]);
    vi.spyOn(scriptCreationApi, "getRewrite").mockResolvedValue(sourceJob);
    const start = vi.spyOn(scriptCreationApi, "startRewrite").mockResolvedValue({
      ...sourceJob, id: "refined", status: "pending", proposal_id: null });
    render(<ProposalReview project="demo" document={wholeDocument} saved
      selection={null} instruction="" onApplied={vi.fn()} />);
    await screen.findByText("candidate");
    await user.click(screen.getByRole("button", { name: "继续调整" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "生成改稿候选" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "生成改稿候选" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("demo", expect.objectContaining({
      reference_proposal_id: "p", scope: originalScope, start: startOffset, end: endOffset,
    })));
  });

  it("requires a scene cursor and sends episode offsets in Unicode codepoints", async () => {
    const user = userEvent.setup();
    const emojiDocument = { ...document, revision: { ...document.revision,
      markdown: "甲😀乙", blocks: [{ id: "b", markdown: "甲😀乙" }] } };
    const start = vi.spyOn(scriptCreationApi, "startRewrite").mockResolvedValue({
      id: "job", document_id: "d", base_revision_id: "r", start: 0, end: 3,
      scope: "episode", mode: "dialogue", instruction: "", preserve: "", before: "甲😀乙",
      status: "pending", proposal_id: null, error: null });
    render(<ProposalReview project="demo" document={emojiDocument} saved selection={null}
      instruction="" onApplied={vi.fn()} />);
    await user.selectOptions(screen.getByRole("combobox", { name: "改稿范围" }), "scene");
    expect(screen.getByRole("button", { name: "生成改稿候选" })).toBeDisabled();
    expect(screen.getByText("先在编辑器中将光标放到目标场次。")).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "改稿范围" }), "episode");
    await user.click(screen.getByRole("button", { name: "生成改稿候选" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("demo", expect.objectContaining({
      scope: "episode", start: 0, end: 3,
    })));
  });

  it("adopts the exact proposal with the current baseline", async () => {
    const proposal = { id: "p", document_id: "d", base_revision_id: "r", block_id: "b",
      start: 0, end: 2, before: "旧句", after: "新句", reason: "自然",
      dependencies: [], context_revisions: {}, round_id: "round", status: "pending" as const,
      source_candidate_id: null, created_at: "" };
    vi.spyOn(scriptCreationApi, "listProposals").mockResolvedValue([proposal]);
    const adopt = vi.spyOn(scriptCreationApi, "acceptProposals").mockResolvedValue(document);
    const applied = vi.fn();
    render(<ProposalReview project="demo" document={document} saved selection={null}
      instruction="" onApplied={applied} />);
    await screen.findByText("新句");
    screen.getByRole("button", { name: "采纳此处" }).click();
    await waitFor(() => expect(adopt).toHaveBeenCalledWith("demo", ["p"], "r", expect.any(String)));
    await waitFor(() => expect(applied).toHaveBeenCalledOnce());
  });
});
