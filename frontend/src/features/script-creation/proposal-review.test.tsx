import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProposalReview } from "./proposal-review";
import { scriptCreationApi } from "./api";
import type { ScriptDocument } from "./types";

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
