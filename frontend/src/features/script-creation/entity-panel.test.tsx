import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EntityPanel } from "./entity-panel";
import { scriptCreationApi } from "./api";
import type { NarrativeEntity, ScriptDocument } from "./types";
vi.mock("./api", () => ({ scriptCreationApi: { listEntities: vi.fn(), listEntityAssets: vi.fn(), putEntity: vi.fn() } }));
const doc = { id: "people", kind: "people", title: "人物", current_revision_id: "r1", revision: { blocks: [{ id: "b1", markdown: "## Alice" }] } } as ScriptDocument;
const ent: NarrativeEntity = { entity_id: "e1", document_id: "people", block_id: "b1", name: "Alice", asset_type: "character", confirmed_revision: "r1", asset_id: "uuid", selected_revision: "r1", relations: [], appearances: [], asset_missing: false, entry_missing: false, stale: false, asset_name: "Alice" };
beforeEach(() => { vi.clearAllMocks(); vi.mocked(scriptCreationApi.listEntities).mockResolvedValue([ent]); vi.mocked(scriptCreationApi.listEntityAssets).mockResolvedValue([{ asset_id: "uuid", asset_type: "character", name: "Alice", description: "" }]); vi.mocked(scriptCreationApi.putEntity).mockResolvedValue(ent); });
describe("stable narrative assets", () => {
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
