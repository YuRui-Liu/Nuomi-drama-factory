import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { SceneContextSource } from "./scene-context-source";
import { scriptCreationApi } from "@/features/script-creation/api";
import { sceneContextApi } from "@/features/script-creation/scene-context-api";
vi.mock("@/features/script-creation/api", () => ({ scriptCreationApi: { listEntityAssets: vi.fn(), list: vi.fn(), listEntities: vi.fn(), listAssetExtractions: vi.fn(), get: vi.fn() } }));
vi.mock("@/features/script-creation/scene-context-api", () => ({ sceneContextApi: { list: vi.fn(), associate: vi.fn() } }));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(scriptCreationApi.listEntityAssets).mockResolvedValue([{ asset_id: "parent", asset_type: "scene", name: "折湾站", description: "作者设定" }, { asset_id: "target", asset_type: "scene", name: "控制室", description: "" }, { asset_id: "unlinked", asset_type: "scene", name: "折湾站院外", description: "" }]);
  vi.mocked(scriptCreationApi.list).mockResolvedValue([{ id: "doc", kind: "scenes", title: "场景表", current_revision_id: "r1" }] as never);
  vi.mocked(scriptCreationApi.listEntities).mockResolvedValue([]);
  vi.mocked(scriptCreationApi.listAssetExtractions).mockResolvedValue([{ status: "committed", source_revision_id: "r1", result: [{ asset_id: "parent" }] }] as never);
  vi.mocked(scriptCreationApi.get).mockResolvedValue({ current_revision_id: "r1" } as never);
  vi.mocked(sceneContextApi.list).mockResolvedValue([]);
  vi.mocked(sceneContextApi.associate).mockResolvedValue({});
});
it("only offers confirmed source assets and explicitly associates the selected source without creating a scene", async () => {
  render(<SceneContextSource project="p" name="控制室" />);
  const select = await screen.findByRole("combobox", { name: "来源场景" });
  expect(screen.getAllByRole("option")).toHaveLength(2);
  expect(screen.queryByRole("option", { name: /折湾站院外/ })).not.toBeInTheDocument();
  expect(sceneContextApi.associate).not.toHaveBeenCalled();
  await userEvent.setup().selectOptions(select, "parent/doc");
  await userEvent.setup().click(screen.getByRole("button", { name: "确认复用来源设定" }));
  await waitFor(() => expect(sceneContextApi.associate).toHaveBeenCalledWith("p", { source_asset_id: "parent", target_asset_ids: ["target"], document_id: "doc", base_revision_id: "r1", client_mutation_id: expect.any(String) }));
  expect(await screen.findByText("已关联「折湾站」的创作设定。")).toBeInTheDocument();
});
it("blocks stale sources before submitting", async () => {
  vi.mocked(scriptCreationApi.get).mockResolvedValue({ current_revision_id: "r2" } as never);
  render(<SceneContextSource project="p" name="控制室" />);
  await userEvent.setup().selectOptions(await screen.findByRole("combobox", { name: "来源场景" }), "parent/doc");
  await userEvent.setup().click(screen.getByRole("button", { name: "确认复用来源设定" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("已过期");
  expect(sceneContextApi.associate).not.toHaveBeenCalled();
});
it("supports confirmed legacy links while excluding outdated extraction results", async () => {
  vi.mocked(scriptCreationApi.listAssetExtractions).mockResolvedValue([{ status: "committed", source_revision_id: "r0", result: [{ asset_id: "unlinked" }] }] as never);
  vi.mocked(scriptCreationApi.listEntities).mockResolvedValue([{ asset_type: "scene", asset_id: "parent", document_id: "doc", selected_revision: "r1", confirmed_revision: "r1", asset_missing: false, entry_missing: false }] as never);
  render(<SceneContextSource project="p" name="控制室" />);
  await screen.findByRole("combobox", { name: "来源场景" });
  expect(screen.getByRole("option", { name: "折湾站 · 场景表" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /折湾站院外/ })).not.toBeInTheDocument();
});
it("displays stale existing associations and server failures", async () => {
  vi.mocked(sceneContextApi.list).mockResolvedValue([{ source_asset_id: "parent", source_name: "折湾站", document_id: "doc", source_revision_id: "r0", stale: true }] as never);
  vi.mocked(sceneContextApi.associate).mockRejectedValue(new Error("来源没有已确认的创作来源"));
  render(<SceneContextSource project="p" name="控制室" />);
  await userEvent.setup().selectOptions(await screen.findByRole("combobox", { name: "来源场景" }), "parent/doc");
  expect(screen.getByText(/来源版本 r0/)).toHaveTextContent("已过期");
  await userEvent.setup().click(screen.getByRole("button", { name: "确认复用来源设定" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("来源没有已确认的创作来源");
});
