// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AssetImportDialog } from "@/components/assets/asset-import-dialog";

const { confirmAsync, previewAsync, toastError } = vi.hoisted(() => ({
  confirmAsync: vi.fn(),
  previewAsync: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("sonner", () => ({
  toast: { error: toastError, info: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/api-errors", () => ({
  backendErrorToastMessage: (error: Error) => error.message,
}));

vi.mock("@/lib/queries/asset-imports", () => ({
  assetImportDispositionKey: (value: string) => value,
  assetImportResultToastValues: () => ({}),
  usePreviewAssetImport: () => ({
    isPending: false,
    mutateAsync: previewAsync,
    reset: vi.fn(),
  }),
  useConfirmAssetImport: () => ({
    isPending: false,
    mutateAsync: confirmAsync,
    reset: vi.fn(),
  }),
}));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: React.PropsWithChildren<{ open: boolean }>) =>
    open ? <div role="dialog">{children}</div> : null,
  DialogContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogFooter: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogTitle: ({ children }: React.PropsWithChildren) => <h2>{children}</h2>,
}));

function renderDialog() {
  return render(
    <AssetImportDialog
      project="demo"
      assetType="character"
      open
      onOpenChange={vi.fn()}
    />,
  );
}

const preview = {
  import_id: "import-123",
  asset_type: "character" as const,
  diffs: [
    {
      name: "谢砚秋",
      disposition: "conflict" as const,
      changes: [
        { field: "role", current: "", proposed: "修复师", disposition: "fill" as const },
        { field: "description", current: "原描述", proposed: "新描述", disposition: "preserve" as const },
        { field: "aliases", current: [], proposed: ["谢姑娘"], disposition: "conflict" as const },
      ],
    },
  ],
};

describe("AssetImportDialog", () => {
  beforeEach(() => {
    previewAsync.mockReset();
    confirmAsync.mockReset();
    toastError.mockReset();
  });

  it("exposes a labelled file input and accepts a .txt file", async () => {
    previewAsync.mockResolvedValue(preview);
    const user = userEvent.setup();
    renderDialog();

    const input = screen.getByLabelText("assets.import.fileLabel");
    await user.upload(input, new File(["# 人物表"], "人物表.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "assets.import.preview" }));

    expect(previewAsync).toHaveBeenCalledWith(expect.objectContaining({ name: "人物表.txt" }));
  });

  it("rejects a .pdf before preview and never calls the preview mutation", async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderDialog();

    await user.upload(
      screen.getByLabelText("assets.import.fileLabel"),
      new File(["pdf"], "人物表.pdf", { type: "application/pdf" }),
    );
    await user.click(screen.getByRole("button", { name: "assets.import.preview" }));

    expect(previewAsync).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith("assets.import.invalidFileType");
  });

  it("shows fill, preserve, and conflict then confirms with only the import id", async () => {
    previewAsync.mockResolvedValue(preview);
    confirmAsync.mockResolvedValue({});
    const user = userEvent.setup();
    renderDialog();

    await user.upload(
      screen.getByLabelText("assets.import.fileLabel"),
      new File(["# 人物表"], "人物表.md", { type: "text/markdown" }),
    );
    await user.click(screen.getByRole("button", { name: "assets.import.preview" }));

    expect(screen.getByText("assets.import.fieldDisposition.fill")).toBeInTheDocument();
    expect(screen.getByText("assets.import.fieldDisposition.preserve")).toBeInTheDocument();
    expect(screen.getByText("assets.import.fieldDisposition.conflict")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "assets.import.confirm" }));
    expect(confirmAsync).toHaveBeenCalledWith("import-123");
  });

  it("keeps the preview available for retry after an ordinary confirm error", async () => {
    previewAsync.mockResolvedValue(preview);
    confirmAsync.mockRejectedValue(new Error("temporary failure"));
    const user = userEvent.setup();
    renderDialog();

    await user.upload(
      screen.getByLabelText("assets.import.fileLabel"),
      new File(["# 人物表"], "人物表.md", { type: "text/markdown" }),
    );
    await user.click(screen.getByRole("button", { name: "assets.import.preview" }));
    await user.click(screen.getByRole("button", { name: "assets.import.confirm" }));

    expect(await screen.findByRole("button", { name: "assets.import.confirm" })).toBeInTheDocument();
    expect(screen.getByText("谢砚秋")).toBeInTheDocument();
    expect(toastError).toHaveBeenCalledWith("temporary failure");
  });
});
