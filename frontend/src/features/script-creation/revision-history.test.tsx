import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RevisionHistory } from "./revision-history";
import { scriptCreationApi } from "./api";
import userEvent from "@testing-library/user-event";
import type { ScriptDocument } from "./types";

const document: ScriptDocument = {
  id: "d", kind: "episode_script", title: "第一集", episode_number: 1,
  current_revision_id: "r", adopted_revision_id: null, source_origin: null,
  created_at: "", updated_at: "",
  revision: { id: "r", document_id: "d", parent_revision_id: null,
    markdown: "当前", blocks: [{ id: "b", markdown: "当前" }],
    client_mutation_id: "create", created_at: "", restored_from_revision_id: null },
};

afterEach(() => vi.restoreAllMocks());

describe("RevisionHistory", () => {
  it("shows current revision while history loads", () => {
    render(<RevisionHistory project="demo" document={document} saved={false} onApplied={vi.fn()} />);
    expect(screen.getByText(/当前版本/)).toBeInTheDocument();
  });

  it("restores a selected historical revision from the current head", async () => {
    const older = { ...document.revision, id: "old", markdown: "旧版", created_at: "2026-01-01T00:00:00Z" };
    vi.spyOn(scriptCreationApi, "revisions").mockResolvedValue([older, document.revision]);
    const restore = vi.spyOn(scriptCreationApi, "restore").mockResolvedValue(document);
    const applied = vi.fn();
    render(<RevisionHistory project="demo" document={document} saved onApplied={applied} />);
    await screen.findByText("old");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /old/ }));
    await user.click(screen.getByRole("button", { name: "恢复所选版本" }));
    await waitFor(() => expect(restore).toHaveBeenCalledWith("demo", "d", "old", "r", expect.any(String)));
    await waitFor(() => expect(applied).toHaveBeenCalledOnce());
  });
});
