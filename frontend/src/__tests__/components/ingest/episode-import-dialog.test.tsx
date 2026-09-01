// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const language = vi.hoisted(() => ({ value: "zh" }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { filename?: string; number?: number }) => {
      const copy: Record<string, [string, string]> = {
        "ingest.episodeImport.title": ["导入分集剧本", "Import episode scripts"],
        "ingest.episodeImport.intentHint": ["按原文解析和校对，不改写剧情", "Parse and validate without rewriting the plot"],
        "ingest.episodeImport.selectFiles": ["选择分集剧本", "Select episode scripts"],
        "ingest.episodeImport.overwriteAll": ["全部覆盖", "Overwrite all"],
        "ingest.episodeImport.skipAll": ["全部跳过", "Skip all"],
        "ingest.episodeImport.overwrite": ["覆盖", "Overwrite"],
        "ingest.episodeImport.skip": ["跳过", "Skip"],
        "ingest.episodeImport.confirm": ["确认导入", "Confirm import"],
        "ingest.episodeImport.submitting": ["提交中…", "Submitting…"],
        "ingest.episodeImport.duplicate": ["集号不能重复", "Episode numbers must be unique"],
        "ingest.episodeImport.errors.stale": ["预检已过期，请重新选择文件预检", "The preview expired. Select the files again to refresh it."],
        "ingest.episodeImport.warningSeparator": ["；", "; "],
        "common.cancel": ["取消", "Cancel"],
      };
      if (key === "ingest.episodeImport.overwriteFile") return `${language.value === "zh" ? "覆盖" : "Overwrite"} ${options?.filename}`;
      if (key === "ingest.episodeImport.skipFile") return `${language.value === "zh" ? "跳过" : "Skip"} ${options?.filename}`;
      if (key === "ingest.episodeImport.episodeNumberFor") return language.value === "zh" ? `${options?.filename} 集号` : `Episode number for ${options?.filename}`;
      if (key === "ingest.episodeImport.episodeLabel") return language.value === "zh" ? `第 ${options?.number} 集` : `Episode ${options?.number}`;
      const statuses: Record<string, [string, string]> = {
        new: ["新增", "New"], conflict: ["冲突", "Conflict"], needs_episode_number: ["待补充", "Number required"], invalid: ["解析失败", "Could not parse"],
      };
      if (key.startsWith("ingest.episodeImport.status.")) return statuses[key.split(".").pop()!][language.value === "zh" ? 0 : 1];
      return copy[key]?.[language.value === "zh" ? 0 : 1] ?? key;
    },
  }),
}));

const preview = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const commit = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));

vi.mock("@/lib/queries/ingest", () => ({
  usePreviewEpisodeImports: () => preview,
  useCommitEpisodeImport: () => commit,
}));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: React.PropsWithChildren<{ open: boolean }>) =>
    open ? <div>{children}</div> : null,
  DialogContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogHeader: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DialogTitle: ({ children }: React.PropsWithChildren) => <h2>{children}</h2>,
  DialogFooter: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}));

import { EpisodeImportDialog } from "@/components/ingest/EpisodeImportDialog";

const base = {
  preview_id: "preview-1",
  base_revision: 7,
};

function item(overrides: Record<string, unknown>) {
  return {
    file_id: "file-a",
    filename: "E01.md",
    content_hash: "hash",
    episode_number: 1,
    title: "第一集",
    status: "new",
    ...overrides,
  };
}

function renderDialog(
  onOpenChange = vi.fn(),
  existingEpisodeNumbers: number[] = [],
) {
  render(
    <EpisodeImportDialog
      project="demo"
      open
      onOpenChange={onOpenChange}
      existingEpisodeNumbers={existingEpisodeNumbers}
    />,
  );
  return onOpenChange;
}

it("makes the existing-script import boundary explicit", () => {
  renderDialog();
  expect(screen.getByText("按原文解析和校对，不改写剧情")).toBeInTheDocument();
});

async function upload(files = [new File(["a"], "E01.md")]) {
  await userEvent.upload(screen.getByLabelText("选择分集剧本"), files);
}

describe("EpisodeImportDialog", () => {
  beforeEach(() => {
    preview.mutateAsync.mockReset();
    commit.mutateAsync.mockReset();
    preview.isPending = false;
    commit.isPending = false;
    language.value = "zh";
  });

  it("previews all selected files and displays them in episode order", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        ...base,
        files: [
          item({ file_id: "b", filename: "E10.md", episode_number: 10 }),
          item({ file_id: "a", filename: "E02.md", episode_number: 2 }),
        ],
      },
    });
    renderDialog();

    const files = [new File(["10"], "E10.md"), new File(["2"], "E02.md")];
    await upload(files);

    expect(preview.mutateAsync).toHaveBeenCalledWith(files);
    const rows = await screen.findAllByTestId("episode-import-row");
    expect(within(rows[0]).getByText("E02.md")).toBeInTheDocument();
    expect(within(rows[1]).getByText("E10.md")).toBeInTheDocument();
  });

  it("sorts equal and missing episode numbers by display name with filename fallback", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        ...base,
        files: [
          item({
            file_id: "missing-b",
            filename: "z-missing.md",
            display_name: "B-空集",
            episode_number: null,
            status: "needs_episode_number",
          }),
          item({
            file_id: "equal-b",
            filename: "z-equal.md",
            display_name: "B-同集",
            episode_number: 3,
          }),
          item({
            file_id: "missing-a",
            filename: "A-空集.md",
            display_name: "  ",
            episode_number: null,
            status: "needs_episode_number",
          }),
          item({
            file_id: "equal-a",
            filename: "A-同集.md",
            episode_number: 3,
          }),
        ],
      },
    });
    renderDialog();
    await upload();

    const rows = await screen.findAllByTestId("episode-import-row");
    expect(within(rows[0]).getByText("A-同集.md")).toBeInTheDocument();
    expect(within(rows[1]).getByText("B-同集")).toBeInTheDocument();
    expect(within(rows[2]).getByText("A-空集.md")).toBeInTheDocument();
    expect(within(rows[3]).getByText("B-空集")).toBeInTheDocument();
  });

  it("distinguishes split candidates by display name and commits their unique file ids", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        ...base,
        files: [
          item({
            file_id: "split-2",
            filename: "合集.docx",
            display_name: "合集.docx · 第 2 集",
            episode_number: 2,
          }),
          item({
            file_id: "split-1",
            filename: "合集.docx",
            display_name: "合集.docx · 第 1 集",
            episode_number: 1,
          }),
        ],
      },
    });
    commit.mutateAsync.mockResolvedValue({ ok: true, task_type: "episode_import" });
    renderDialog();
    await upload();

    const rows = await screen.findAllByTestId("episode-import-row");
    expect(within(rows[0]).getByText("合集.docx · 第 1 集")).toBeInTheDocument();
    expect(within(rows[1]).getByText("合集.docx · 第 2 集")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "确认导入" }));
    expect(commit.mutateAsync).toHaveBeenCalledWith({
      preview_id: "preview-1",
      expected_revision: 7,
      resolutions: [
        { file_id: "split-1", episode_number: 1, action: "import" },
        { file_id: "split-2", episode_number: 2, action: "import" },
      ],
    });
  });

  it("uses split display names for conflict controls and manual episode labels", async () => {
    const user = userEvent.setup();
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        ...base,
        files: [
          item({
            file_id: "split-conflict",
            filename: "合集.docx",
            display_name: "合集.docx · 第 1 集",
            status: "conflict",
          }),
          item({
            file_id: "split-manual",
            filename: "合集.docx",
            display_name: "合集.docx · 待识别分集",
            episode_number: null,
            status: "needs_episode_number",
          }),
        ],
      },
    });
    renderDialog();
    await upload();

    expect(screen.getByRole("radiogroup", { name: "合集.docx · 第 1 集 冲突处理" })).toBeInTheDocument();
    expect(screen.getByLabelText("合集.docx · 待识别分集 集号")).toBeInTheDocument();

    const overwrite = screen.getByRole("radio", { name: "覆盖 合集.docx · 第 1 集" });
    overwrite.focus();
    await user.keyboard("{ArrowRight}");

    expect(screen.getByRole("radio", { name: "跳过 合集.docx · 第 1 集" })).toHaveFocus();
  });

  it("requires a valid episode number for an unrecognized file", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        ...base,
        files: [item({ episode_number: null, status: "needs_episode_number", filename: "extra.md" })],
      },
    });
    renderDialog();
    await upload();

    const submit = await screen.findByRole("button", { name: "确认导入" });
    expect(submit).toBeDisabled();
    const input = screen.getByLabelText("extra.md 集号");
    await userEvent.type(input, "2");
    expect(submit).toBeEnabled();
  });

  it("turns a manually entered existing episode into a conflict", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        ...base,
        files: [item({ episode_number: null, status: "needs_episode_number", filename: "extra.md" })],
      },
    });
    renderDialog(vi.fn(), [2]);
    await upload();

    await userEvent.type(screen.getByLabelText("extra.md 集号"), "2");

    expect(screen.getByText("冲突")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认导入" })).toBeDisabled();
    await userEvent.click(screen.getByRole("radio", { name: "覆盖 extra.md" }));
    expect(screen.getByRole("button", { name: "确认导入" })).toBeEnabled();
  });

  it("requires every conflict to choose overwrite or skip", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: { ...base, files: [item({ status: "conflict" })] },
    });
    renderDialog();
    await upload();

    const submit = await screen.findByRole("button", { name: "确认导入" });
    expect(submit).toBeDisabled();
    await userEvent.click(screen.getByRole("radio", { name: "覆盖 E01.md" }));
    expect(submit).toBeEnabled();
    await userEvent.click(screen.getByRole("radio", { name: "跳过 E01.md" }));
    expect(screen.getByRole("radio", { name: "覆盖 E01.md" })).toHaveAttribute("aria-checked", "false");
  });

  it("supports arrow-key selection within each conflict radio group", async () => {
    const user = userEvent.setup();
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: { ...base, files: [item({ status: "conflict" })] },
    });
    renderDialog();
    await upload();

    const overwrite = await screen.findByRole("radio", { name: "覆盖 E01.md" });
    overwrite.focus();
    await user.keyboard("{ArrowRight}");

    const skip = screen.getByRole("radio", { name: "跳过 E01.md" });
    expect(skip).toHaveFocus();
    expect(skip).toHaveAttribute("aria-checked", "true");
    expect(overwrite).toHaveAttribute("tabindex", "-1");
    expect(skip).toHaveAttribute("tabindex", "0");
  });

  it("supports arrow-key selection when the display name contains selector characters", async () => {
    const user = userEvent.setup();
    const displayName = "合集\"终稿\"\\拆分.docx · 第 1 集";
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        ...base,
        files: [item({ display_name: displayName, status: "conflict" })],
      },
    });
    renderDialog();
    await upload();

    const overwrite = await screen.findByRole("radio", { name: `覆盖 ${displayName}` });
    overwrite.focus();
    await user.keyboard("{ArrowRight}");

    expect(screen.getByRole("radio", { name: `跳过 ${displayName}` })).toHaveFocus();
  });

  it("supports batch overwrite and skip actions", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        ...base,
        files: [
          item({ file_id: "a", filename: "E01.md", status: "conflict" }),
          item({ file_id: "b", filename: "E02.md", episode_number: 2, status: "conflict" }),
        ],
      },
    });
    renderDialog();
    await upload();

    await userEvent.click(await screen.findByRole("button", { name: "全部覆盖" }));
    expect(screen.getAllByRole("radio", { name: /覆盖 E0/ })).toSatisfy(
      (buttons: HTMLElement[]) => buttons.every((button) => button.getAttribute("aria-checked") === "true"),
    );
    await userEvent.click(screen.getByRole("button", { name: "全部跳过" }));
    expect(screen.getAllByRole("radio", { name: /跳过 E0/ })).toSatisfy(
      (buttons: HTMLElement[]) => buttons.every((button) => button.getAttribute("aria-checked") === "true"),
    );
  });

  it("does not let one parse failure block valid files", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        ...base,
        files: [
          item({ file_id: "bad", filename: "bad.docx", episode_number: null, status: "invalid", error: "解析失败" }),
          item({ file_id: "good", filename: "E02.md", episode_number: 2 }),
        ],
      },
    });
    commit.mutateAsync.mockResolvedValue({ ok: true });
    renderDialog();
    await upload();

    expect(await screen.findByText("解析失败")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "确认导入" }));
    expect(commit.mutateAsync).toHaveBeenCalledWith({
      preview_id: "preview-1",
      expected_revision: 7,
      resolutions: [{ file_id: "good", episode_number: 2, action: "import" }],
    });
  });

  it("treats all-skip as a no-op without calling commit", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: { ...base, files: [item({ status: "conflict" })] },
    });
    const onOpenChange = renderDialog();
    await upload();
    await userEvent.click(await screen.findByRole("radio", { name: "跳过 E01.md" }));
    await userEvent.click(screen.getByRole("button", { name: "确认导入" }));

    expect(commit.mutateAsync).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("shows a stale-preview message when commit returns 409", async () => {
    preview.mutateAsync.mockResolvedValue({
      ok: true,
      data: { ...base, files: [item({})] },
    });
    const error = new (await import("@/lib/api-errors")).BackendStatusError(
      "项目内容已变化，请重新预检", 409,
      { detail: { code: "EPISODE_IMPORT_REVISION_CONFLICT" } },
    );
    commit.mutateAsync.mockRejectedValue(error);
    renderDialog();
    await upload();
    await userEvent.click(await screen.findByRole("button", { name: "确认导入" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("预检已过期，请重新选择文件预检");
  });

  it("passes the accepted task response to onCommitted", async () => {
    preview.mutateAsync.mockResolvedValue({ ok: true, data: { ...base, files: [item({})] } });
    const accepted = { ok: true, task_type: "episode_import", task_id: "task-1", message: "已提交" };
    commit.mutateAsync.mockResolvedValue(accepted);
    const onCommitted = vi.fn();
    render(<EpisodeImportDialog project="demo" open onOpenChange={vi.fn()} onCommitted={onCommitted} />);
    await upload();
    await userEvent.click(await screen.findByRole("button", { name: "确认导入" }));
    expect(onCommitted).toHaveBeenCalledWith(accepted);
  });

  it("allows duplicate conflicts to be skipped as a no-op", async () => {
    preview.mutateAsync.mockResolvedValue({ ok: true, data: { ...base, files: [
      item({ file_id: "a", filename: "a.md", status: "conflict" }),
      item({ file_id: "b", filename: "b.md", status: "conflict" }),
    ] } });
    const onOpenChange = renderDialog();
    await upload();
    await userEvent.click(await screen.findByRole("button", { name: "全部跳过" }));
    await userEvent.click(screen.getByRole("button", { name: "确认导入" }));
    expect(commit.mutateAsync).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("lets a batch-internal duplicate be renumbered into a new import", async () => {
    preview.mutateAsync.mockResolvedValue({ ok: true, data: { ...base, files: [
      item({ file_id: "a", filename: "a.md", status: "conflict", warnings: ["批次内部集号重复"] }),
      item({ file_id: "b", filename: "b.md", status: "conflict", warnings: ["批次内部集号重复"] }),
    ] } });
    commit.mutateAsync.mockResolvedValue({ ok: true, task_type: "episode_import" });
    renderDialog();
    await upload();

    await userEvent.clear(await screen.findByLabelText("a.md 集号"));
    await userEvent.type(screen.getByLabelText("a.md 集号"), "2");
    await userEvent.click(screen.getByRole("radio", { name: "跳过 b.md" }));
    await userEvent.click(screen.getByRole("button", { name: "确认导入" }));

    expect(commit.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ resolutions: [
      { file_id: "a", episode_number: 2, action: "import" },
      { file_id: "b", episode_number: 1, action: "skip" },
    ] }));
  });

  it("renders the complete dialog in English", async () => {
    language.value = "en";
    preview.mutateAsync.mockResolvedValue({ ok: true, data: { ...base, files: [item({ status: "conflict" })] } });
    renderDialog();

    expect(screen.getByRole("heading", { name: "Import episode scripts" })).toBeInTheDocument();
    await userEvent.upload(screen.getByLabelText("Select episode scripts"), new File(["a"], "E01.md"));
    expect(await screen.findByRole("radio", { name: "Overwrite E01.md" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Skip E01.md" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm import" })).toBeInTheDocument();
  });
});
