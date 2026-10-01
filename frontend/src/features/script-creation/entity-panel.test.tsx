import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EntityPanel } from "./entity-panel";
import { scriptCreationApi } from "./api";
import type { NarrativeEntity, ScriptDocument } from "./types";
vi.mock("./api", () => ({ scriptCreationApi: { listEntities: vi.fn(), listEntityAssets: vi.fn(), putEntity: vi.fn() } }));
vi.mock("./prop-extraction-dialog", () => ({ AssetExtractionDialog: ({ document, assetType }: { document: ScriptDocument; assetType: string }) => <div role="dialog">从已保存文档提取：{document.id} · {assetType}</div> }));
const doc = { id: "people", kind: "people", title: "人物", current_revision_id: "r1", revision: { blocks: [{ id: "b1", markdown: "## Alice" }] } } as ScriptDocument;
const ent: NarrativeEntity = { entity_id: "e1", document_id: "people", block_id: "b1", name: "Alice", asset_type: "character", confirmed_revision: "r1", asset_id: "uuid", selected_revision: "r1", relations: [], appearances: [], asset_missing: false, entry_missing: false, stale: false, asset_name: "Alice" };
beforeEach(() => { vi.clearAllMocks(); vi.mocked(scriptCreationApi.listEntities).mockResolvedValue([ent]); vi.mocked(scriptCreationApi.listEntityAssets).mockResolvedValue([{ asset_id: "uuid", asset_type: "character", name: "Alice", description: "" }]); vi.mocked(scriptCreationApi.putEntity).mockResolvedValue(ent); });
describe("stable narrative assets", () => {
  it("opens prop extraction directly from the saved creation document", async () => {
    const source = { ...doc, kind: "props" as const };
    const user = userEvent.setup();
    const view = render(<EntityPanel project="demo" document={source} documents={[source]} allSaved />);
    await user.click(screen.getByRole("button", { name: "从创作道具表提取" }));
    expect(screen.queryByRole("button", { name: "登记设计条目" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent(`从已保存文档提取：${source.id}`);
    view.rerender(<EntityPanel project="demo" document={source} documents={[source]} allSaved={false} />);
    expect(screen.getByRole("button", { name: "从创作道具表提取" })).toBeDisabled();
  });
  it.each([["people", "character", "人物"], ["scenes", "scene", "场景"]] as const)("opens %s extraction with the current saved document", async (kind, assetType, label) => {
    const source = { ...doc, kind };
    render(<EntityPanel project="demo" document={source} documents={[source]} allSaved />);
    await userEvent.setup().click(screen.getByRole("button", { name: `从创作${label}表提取` }));
    expect(screen.getByRole("dialog")).toHaveTextContent(`${source.id} · ${assetType}`);
  });
  it.each([["people", "character"], ["scenes", "scene"]] as const)("registers individual %s sections with their exact name and bounded body", async (kind, assetType) => {
    vi.mocked(scriptCreationApi.listEntities).mockResolvedValue([]);
    const source = { ...doc, kind, revision: { ...doc.revision, blocks: [
      { id: "root", markdown: "# 全部设计" }, { id: "intro", markdown: "文档介绍" },
      { id: "first", markdown: "## 前厅（白天）" }, { id: "body", markdown: "第一条描述" },
      { id: "detail", markdown: "### 细节" }, { id: "detail-body", markdown: "细节描述" },
      { id: "second", markdown: "## 后院" }, { id: "second-body", markdown: "第二条描述" },
      { id: "end", markdown: "# 附录" },
    ] } };
    const user = userEvent.setup();
    render(<EntityPanel project="demo" document={source} documents={[source]} allSaved />);
    await user.click(await screen.findByRole("button", { name: "登记 / 关联 后院" }));
    expect(scriptCreationApi.listEntityAssets).toHaveBeenCalledWith("demo", assetType);
    expect(screen.getByLabelText("正文条目")).toHaveValue("second");
    expect(screen.getByLabelText("条目名称")).toHaveValue("后院");
    expect(screen.getByLabelText("新资产名称")).toHaveValue("后院");
    expect(screen.getByLabelText("文字资产描述")).toHaveValue("## 后院\n\n第二条描述");
    expect(screen.queryByRole("option", { name: "全部设计" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "细节" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("正文条目"), "first");
    expect(screen.getByLabelText("条目名称")).toHaveValue("前厅（白天）");
    expect(screen.getByLabelText("新资产名称")).toHaveValue("前厅（白天）");
    const description = "## 前厅（白天）\n\n第一条描述\n\n### 细节\n\n细节描述";
    expect(screen.getByLabelText("文字资产描述")).toHaveValue(description);
    await user.click(screen.getByRole("button", { name: "保存关联" }));
    await waitFor(() => expect(scriptCreationApi.putEntity).toHaveBeenCalledWith("demo", expect.objectContaining({ block_id: "first", name: "前厅（白天）", create_text: { name: "前厅（白天）", description } })));
  });
  it("keeps legacy root links while offering unregistered sections and requires explicit asset selection", async () => {
    const source = { ...doc, revision: { ...doc.revision, blocks: [{ id: "root", markdown: "# 人物" }, { id: "alice", markdown: "## Alice" }, { id: "body", markdown: "Description" }] } };
    vi.mocked(scriptCreationApi.listEntities).mockResolvedValue([{ ...ent, block_id: "root" }]);
    const user = userEvent.setup();
    render(<EntityPanel project="demo" document={source} documents={[source]} allSaved />);
    await user.click(await screen.findByRole("button", { name: "关联 / 确认 Alice" }));
    expect(screen.getByLabelText("正文条目")).toHaveValue("root");
    expect(screen.getByLabelText("正文条目")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.click(screen.getByRole("button", { name: "登记设计条目" }));
    expect(screen.getByLabelText("正文条目")).toHaveValue("alice");
    await user.click(screen.getByLabelText("新建文字资产记录"));
    expect(screen.getByLabelText("选择现有资产")).toHaveValue("");
    expect(screen.getByRole("button", { name: "保存关联" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("选择现有资产"), "uuid");
    await user.click(screen.getByRole("button", { name: "保存关联" }));
    await waitFor(() => expect(scriptCreationApi.putEntity).toHaveBeenCalledWith("demo", expect.objectContaining({ block_id: "alice", asset_id: "uuid" })));
    expect(vi.mocked(scriptCreationApi.putEntity).mock.calls[0][1]).not.toHaveProperty("entity_id");
  });
  it("creates a real asset for an unlinked entry and includes its section body", async () => {
    vi.mocked(scriptCreationApi.listEntities).mockResolvedValue([{ ...ent, asset_id: null, asset_name: null }]);
    vi.mocked(scriptCreationApi.listEntityAssets).mockResolvedValue([]);
    const source = { ...doc, revision: { ...doc.revision, blocks: [{ id: "b1", markdown: "## Alice" }, { id: "body", markdown: "A careful detective." }, { id: "b2", markdown: "## Bob" }] } };
    const user = userEvent.setup();
    render(<EntityPanel project="demo" document={source} documents={[source]} allSaved />);
    await user.click(await screen.findByRole("button", { name: "关联 / 确认 Alice" }));
    expect(screen.getByLabelText("新资产名称")).toHaveValue("Alice");
    expect(screen.getByLabelText("文字资产描述")).toHaveValue("## Alice\n\nA careful detective.");
    await user.click(screen.getByLabelText("新建文字资产记录"));
    expect(screen.getByRole("button", { name: "保存关联" })).toBeDisabled();
    await user.click(screen.getByLabelText("新建文字资产记录"));
    await user.click(screen.getByRole("button", { name: "保存关联" }));
    await waitFor(() => expect(scriptCreationApi.putEntity).toHaveBeenCalledWith("demo", expect.objectContaining({ create_text: { name: "Alice", description: "## Alice\n\nA careful detective." } })));
  });
  it("blocks linking dirty documents and shows missing versus changed design", async () => {
    vi.mocked(scriptCreationApi.listEntities).mockResolvedValue([{ ...ent, stale: true, asset_missing: true }]);
    render(<EntityPanel project="demo" document={doc} documents={[doc]} allSaved={false} />);
    expect(await screen.findByText("资产已删除或不可用")).toBeInTheDocument();
    expect(screen.getByText("设计已更新，确认关联版本")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关联 / 确认 Alice" })).toBeDisabled();
    expect(scriptCreationApi.putEntity).not.toHaveBeenCalled();
  });
  it("requires explicit selection and submits the stable uuid and saved revision", async () => {
    const user = userEvent.setup();
    render(<EntityPanel project="demo" document={doc} documents={[doc]} allSaved />);
    await user.click(await screen.findByRole("button", { name: "关联 / 确认 Alice" }));
    await user.selectOptions(screen.getByLabelText("选择现有资产"), "uuid");
    await user.click(screen.getByRole("button", { name: "保存关联" }));
    await waitFor(() => expect(scriptCreationApi.putEntity).toHaveBeenCalledWith("demo", expect.objectContaining({ entity_id: "e1", asset_id: "uuid", base_revision_id: "r1" })));
  });
  it("offers retry after loading fails", async () => {
    vi.mocked(scriptCreationApi.listEntities).mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup();
    render(<EntityPanel project="demo" document={doc} documents={[doc]} allSaved />);
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("button", { name: "关联 / 确认 Alice" })).toBeInTheDocument();
  });
});


it("shows guidance for outline and episode pages without registering wrong kinds", async () => {
  for (const kind of ["outline", "episode_script"] as const) {
    const view = render(<EntityPanel project="demo" document={{ ...doc, kind }} documents={[doc]} allSaved />);
    expect(await screen.findByText("在文档树中选择人物、场景或道具设计。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "登记设计条目" })).not.toBeInTheDocument();
    view.unmount();
  }
});

it("retries a text creation with the same mutation and distinguishes planned from written", async () => {
  vi.mocked(scriptCreationApi.putEntity).mockRejectedValueOnce(new Error("lost response")).mockResolvedValue(ent);
  const episode = { ...doc, id: "episode", title: "第1集", kind: "episode_script", episode_number: 1, current_revision_id: "episode-r1" } as ScriptDocument;
  const user = userEvent.setup();
  render(<EntityPanel project="demo" document={doc} documents={[doc, episode]} allSaved />);
  await user.click(await screen.findByRole("button", { name: "关联 / 确认 Alice" }));
  await user.click(screen.getByLabelText("新建文字资产记录"));
  await user.type(screen.getByLabelText("新资产名称"), "New Alice");
  await user.type(screen.getByLabelText("文字资产描述"), "Text only");
  await user.click(screen.getByRole("button", { name: "添加出场记录" }));
  await user.selectOptions(screen.getByLabelText("出场位置"), "episode");
  await user.click(screen.getByRole("button", { name: "添加出场记录" }));
  await user.click(screen.getByRole("button", { name: "保存关联" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("lost response");
  await user.click(screen.getByRole("button", { name: "保存关联" }));
  await waitFor(() => expect(scriptCreationApi.putEntity).toHaveBeenCalledTimes(2));
  const [first, retry] = vi.mocked(scriptCreationApi.putEntity).mock.calls;
  expect(first).toEqual(retry);
  expect(first[1]).toMatchObject({ create_text: { name: "New Alice", description: "Text only" }, appearances: [
    { status: "planned", episode_number: 1 }, { status: "written", document_id: "episode", revision_id: "episode-r1" },
  ] });
});


it("labels missing relation targets and prevents selecting deleted entries", async () => {
  const prop: NarrativeEntity = { ...ent, entity_id: "prop", document_id: "props", name: "钥匙", asset_type: "prop", entry_missing: true };
  vi.mocked(scriptCreationApi.listEntities).mockResolvedValue([{ ...ent, relations: [{ kind: "holding", entity_id: "prop", missing: true, stale: true }] }, prop]);
  const user = userEvent.setup();
  render(<EntityPanel project="demo" document={doc} documents={[doc]} allSaved />);
  expect(await screen.findByText("持有道具：条目缺失（钥匙）")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "关联 / 确认 Alice" }));
  const checkbox = screen.getByRole("checkbox", { name: "持有 · 钥匙（条目缺失）" });
  expect(checkbox).toBeEnabled();
  expect(checkbox).toBeChecked();
  await user.click(checkbox);
  expect(checkbox).not.toBeChecked();
  expect(checkbox).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "保存关联" }));
  await waitFor(() => expect(scriptCreationApi.putEntity).toHaveBeenCalledWith("demo", expect.objectContaining({ relations: [] })));
});
