import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssetExtractionDialog, PropExtractionDialog } from "./prop-extraction-dialog";
import { scriptCreationApi } from "./api";
import type { PropExtractionRun, ScriptDocument } from "./types";
vi.mock("./api", () => ({ scriptCreationApi: { list: vi.fn(), get: vi.fn(), listPropExtractions: vi.fn(), getPropExtraction: vi.fn(), startPropExtraction: vi.fn(), confirmPropExtraction: vi.fn(), listAssetExtractions: vi.fn(), getAssetExtraction: vi.fn(), startAssetExtraction: vi.fn(), confirmAssetExtraction: vi.fn(), revalidateAssetExtraction: vi.fn() } }));
const document = { id: "props-doc", kind: "props", title: "创作道具表", current_revision_id: "r1", revision: { blocks: [{ id: "combo", markdown: "## 药箱、药瓶与空水桶" }] } } as ScriptDocument;
const ready: PropExtractionRun = { id: "run", document_id: document.id, source_revision_id: "r1", status: "ready", error: null, candidates: [
  { id: "box", name: "药箱", description: "木药箱", visual_prompt: "旧木药箱，单一物件", prop_type: "object", source_block_id: "combo", evidence: "药箱、药瓶与空水桶", existing_asset_id: null, existing_name: null, action: "create", warnings: [] },
  { id: "bottle", name: "药瓶", description: "瓶", visual_prompt: "", prop_type: "object", source_block_id: "combo", evidence: "药箱、药瓶与空水桶", existing_asset_id: "bottle-uuid", existing_name: "药瓶", action: "reuse", warnings: ["缺少明确外观描述，入库后需补充"] },
] };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(scriptCreationApi.list).mockResolvedValue([document]);
  vi.mocked(scriptCreationApi.get).mockResolvedValue(document);
  vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([]);
  vi.mocked(scriptCreationApi.startPropExtraction).mockResolvedValue({ ...ready, status: "pending", candidates: [] });
  vi.mocked(scriptCreationApi.getPropExtraction).mockResolvedValue(ready);
  vi.mocked(scriptCreationApi.confirmPropExtraction).mockResolvedValue({ ...ready, status: "committed", result: [{ candidate_id: "box", name: "药箱", asset_id: "new-box", action: "created", source_document_id: document.id, source_revision_id: "r1", source_block_id: "combo" }] });
});
describe("creation prop extraction", () => {
  it("confirms only matched existing scenes by default", async () => {
    vi.mocked(scriptCreationApi.listAssetExtractions).mockResolvedValue([ready]);
    vi.mocked(scriptCreationApi.confirmAssetExtraction).mockResolvedValue({ ...ready, status: "committed", result: [] });
    render(<AssetExtractionDialog project="demo" assetType="scene" document={{ ...document, kind: "scenes" }} onClose={vi.fn()} />);
    const confirm = await screen.findByRole("button", { name: "确认入库 1 项（新建 0 · 复用 1）" });
    expect(screen.getByRole("checkbox", { name: "选择 药箱" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "选择 药瓶" })).toBeChecked();
    await userEvent.setup().click(confirm);
    await waitFor(() => expect(scriptCreationApi.confirmAssetExtraction).toHaveBeenCalledWith("demo", "scene", "run", expect.objectContaining({ candidate_ids: ["bottle"] })));
  });
  it("requires explicit prop selection and reports create versus reuse counts", async () => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([ready]);
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "确认入库 0 项（新建 0 · 复用 0）" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "选择 药箱" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "选择 药瓶" })).not.toBeChecked();
    expect(screen.getByText(/零件和背景陈设无需单独建库/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("checkbox", { name: "选择 药瓶" }));
    await userEvent.setup().click(screen.getByRole("button", { name: "确认入库 1 项（新建 0 · 复用 1）" }));
    await waitFor(() => expect(scriptCreationApi.confirmPropExtraction).toHaveBeenCalledWith("demo", "run", expect.objectContaining({ candidate_ids: ["bottle"] })));
  });
  it.each([true, false])("keeps exclusion summaries out of candidate cards with structured reasons=%s", async (structured) => {
    const summary = "以下条目未通过来源校验，已排除：旧城地图";
    const description = "完整道具业务背景与外观。".repeat(80);
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([{ ...ready,
      rejected_candidates: structured ? [{ name: "旧城地图", source_block_id: "wrong", evidence: "原文", reason: "名称不在来源中" }] : [],
      candidates: ready.candidates.map((item) => ({ ...item, description, warnings: [...item.warnings, summary] })),
    }]);
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    const checkbox = await screen.findByRole("checkbox", { name: "选择 药箱" });
    const card = checkbox.closest("section")!;
    expect(card.textContent).not.toContain(summary);
    expect(screen.queryAllByText(summary)).toHaveLength(structured ? 0 : 1);
    if (structured) expect(screen.getByRole("region", { name: "未导入条目" })).toHaveTextContent("旧城地图");
    expect(screen.getByText("缺少明确外观描述，入库后需补充")).toBeInTheDocument();
    expect(card.querySelector("p.line-clamp-3")).toHaveTextContent(description);
    expect(card.querySelector("details")).toHaveTextContent(description);
  });
  it.each(["failed", "ready"] as const)("revalidates saved %s results without calling a model or importing", async (status) => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([{ ...ready, status, raw_output: { props: [] }, rejected_candidates: [{ name: "组合说明", source_block_id: "combo", evidence: "原文片段", reason: "资产名称或来源证据无法核验" }] }]);
    vi.mocked(scriptCreationApi.revalidateAssetExtraction).mockResolvedValue(ready);
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    expect(await screen.findByText("已排除 1 项，未加入入库候选：")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "重新校验已有结果（不调用模型）" }));
    await waitFor(() => expect(scriptCreationApi.revalidateAssetExtraction).toHaveBeenCalledWith("demo", "run"));
    expect(await screen.findByRole("button", { name: /确认入库 0 项/ })).toBeDisabled();
    expect(scriptCreationApi.startPropExtraction).not.toHaveBeenCalled();
    expect(scriptCreationApi.startAssetExtraction).not.toHaveBeenCalled();
    expect(scriptCreationApi.confirmPropExtraction).not.toHaveBeenCalled();
  });
  it.each([undefined, null, {}])("does not offer dead free-recovery actions without saved raw output", async (raw_output) => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([{ ...ready, status: "failed", raw_output, error: "来源校验失败" }]);
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    expect(await screen.findByText(/本次任务未保存原始模型结果，无法免费恢复/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新校验已有结果（不调用模型）" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新提取（调用模型）" })).toBeEnabled();
  });
  it("refuses free revalidation when the persisted source changed", async () => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([{ ...ready, status: "failed", raw_output: { props: [] } }]);
    vi.mocked(scriptCreationApi.get).mockResolvedValue({ ...document, current_revision_id: "r2" });
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "重新校验已有结果（不调用模型）" }));
    expect(await screen.findByText(/不能重新校验旧结果/)).toBeInTheDocument();
    expect(scriptCreationApi.revalidateAssetExtraction).not.toHaveBeenCalled();
  });
  it("shows a compact rejected-item summary and expandable evidence for all excluded items", async () => {
    const rejected_candidates = Array.from({ length: 5 }, (_, index) => ({ name: `未导入${index}`, source_block_id: "combo", evidence: `排除原文${index}`, reason: `来源无法核验（详情${index}）` }));
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([{ ...ready, rejected_candidates }]);
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    expect(await screen.findByText("已排除 5 项，未加入入库候选：")).toBeInTheDocument();
    expect(screen.queryByText("未导入3：来源无法核验")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByText("查看全部排除原因与原文"));
    expect(screen.getByText("来源无法核验（详情4）")).toBeVisible();
    expect(screen.getByText(/排除原文4/)).toBeVisible();
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  });
  it("distinguishes missing source appearance from an existing character's confirmed styling", async () => {
    vi.mocked(scriptCreationApi.listAssetExtractions).mockResolvedValue([{ ...ready, candidates: [{ ...ready.candidates[1], name: "岑砚", existing_name: "岑砚", fields: {} }] }]);
    render(<AssetExtractionDialog project="demo" assetType="character" document={{ ...document, kind: "people" }} onClose={vi.fn()} />);
    expect(await screen.findByText(/源表未明确描述（不代表已有造型不可用）/)).toBeInTheDocument();
    expect(screen.getAllByText(/保留已有造型，可在角色造型室查看或继续设计/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/入库后需补充才能生图/)).not.toBeInTheDocument();
    expect(screen.queryByText(/不能直接出图/)).not.toBeInTheDocument();
  });
  it("preserves complete character context in expandable source sections", async () => {
    const source = { ...document, kind: "people" as const, revision: { ...document.revision, blocks: [
      { id: "combo", markdown: "## 岑砚" }, { id: "bio", markdown: "身份：乡村医生。\n外貌：消瘦。\n声线：低沉克制。\n人物小传：曾在城里行医。" }, { id: "other", markdown: "## 岑禾" },
    ] } };
    vi.mocked(scriptCreationApi.listAssetExtractions).mockResolvedValue([{ ...ready, candidates: [{ ...ready.candidates[0], fields: { face_prompt: "消瘦" } }] }]);
    render(<AssetExtractionDialog project="demo" assetType="character" document={source} onClose={vi.fn()} />);
    const detail = (await screen.findByText("完整人物设定与来源条目")).closest("details")!;
    expect(detail).toHaveTextContent("身份：乡村医生。");
    expect(detail).toHaveTextContent("声线：低沉克制。");
    expect(detail).toHaveTextContent("人物小传：曾在城里行医。");
    expect(detail).not.toHaveTextContent("岑禾");
  });
  it("does not offer task submission when generic endpoints are unavailable", async () => {
    vi.mocked(scriptCreationApi.listAssetExtractions).mockRejectedValueOnce(new Error("接口尚未就绪"));
    render(<AssetExtractionDialog project="demo" assetType="scene" document={{ ...document, kind: "scenes" }} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("接口尚未就绪");
    expect(screen.getByRole("button", { name: "提取独立场景" })).toBeDisabled();
    expect(scriptCreationApi.startAssetExtraction).not.toHaveBeenCalled();
  });
  it.each([["character", "people", "人物", "face_prompt"], ["scene", "scenes", "场景", "environment_prompt"]] as const)("extracts saved %s designs through the generic API with generation fields and partial confirmation", async (assetType, kind, label, promptField) => {
    const source = { ...document, id: `${kind}-doc`, kind, title: `创作${label}表` };
    const extracted = { ...ready, document_id: source.id, asset_type: assetType, candidates: ready.candidates.map((item) => ({ ...item, fields: { [promptField]: "逐字来源的外观信息", gender: "female", scene_type: "interior" } })) };
    vi.mocked(scriptCreationApi.list).mockResolvedValue([document, source]);
    vi.mocked(scriptCreationApi.get).mockResolvedValue(source);
    vi.mocked(scriptCreationApi.listAssetExtractions).mockResolvedValue([]);
    vi.mocked(scriptCreationApi.startAssetExtraction).mockResolvedValue({ ...extracted, status: "running", candidates: [] });
    vi.mocked(scriptCreationApi.getAssetExtraction).mockResolvedValue(extracted);
    vi.mocked(scriptCreationApi.confirmAssetExtraction).mockResolvedValue({ ...extracted, status: "committed", result: [] });
    const user = userEvent.setup();
    render(<AssetExtractionDialog project="demo" assetType={assetType} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: `提取独立${label}` })).toBeEnabled());
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("combobox", { name: `来源${label}表` })).toHaveValue(source.id);
    await user.click(screen.getByRole("button", { name: `提取独立${label}` }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "选择 药瓶" })).toBeChecked(), { timeout: 2500 });
    if (assetType === "scene") {
      expect(screen.getByRole("checkbox", { name: "选择 药箱" })).not.toBeChecked();
      expect(screen.getByText(/同一空间的昼夜、天气、光线或损坏状态请使用变体/)).toBeInTheDocument();
      await user.click(screen.getByRole("checkbox", { name: "选择 药箱" }));
    }
    expect(screen.getAllByText("逐字来源的外观信息", { exact: false })).toHaveLength(2);
    expect(scriptCreationApi.startAssetExtraction).toHaveBeenCalledWith("demo", assetType, expect.objectContaining({ document_id: source.id, base_revision_id: "r1" }));
    await user.click(screen.getByRole("checkbox", { name: "选择 药瓶" }));
    await user.click(screen.getByRole("button", { name: /确认入库 1 项/ }));
    await waitFor(() => expect(scriptCreationApi.confirmAssetExtraction).toHaveBeenCalledWith("demo", assetType, "run", expect.objectContaining({ candidate_ids: ["box"] })));
    expect(scriptCreationApi.confirmPropExtraction).not.toHaveBeenCalled();
  });
  it.each(["character", "scene"] as const)("blocks stale %s previews", async (assetType) => {
    const source = { ...document, kind: assetType === "character" ? "people" as const : "scenes" as const, current_revision_id: "r2" };
    vi.mocked(scriptCreationApi.listAssetExtractions).mockResolvedValue([ready]);
    render(<AssetExtractionDialog project="demo" assetType={assetType} document={source} onClose={vi.fn()} />);
    expect(await screen.findByRole("button", { name: new RegExp(`确认入库 ${assetType === "scene" ? 1 : 2} 项`) })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("已过期");
  });
  it("reads saved props without copied text and polls asynchronous extraction into individual objects", async () => {
    const user = userEvent.setup();
    render(<PropExtractionDialog project="demo" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "提取独立道具" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "提取独立道具" }));
    expect(await screen.findByText(/正在提取独立道具/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "选择 药箱" })).not.toBeChecked(), { timeout: 2500 });
    expect(scriptCreationApi.startPropExtraction).toHaveBeenCalledWith("demo", { document_id: "props-doc", base_revision_id: "r1", client_mutation_id: expect.any(String) });
    expect(scriptCreationApi.getPropExtraction).toHaveBeenCalledWith("demo", "run");
    expect(screen.getByText(/复用已有：药瓶（保留已有内容）/)).toBeInTheDocument();
    expect(screen.getByText(/缺少外观描述，入库后需补充才能生图/)).toBeInTheDocument();
    expect(screen.getByText(/提取完成不代表可直接出图/)).toBeInTheDocument();
    expect(screen.getAllByText(/来源条目 combo/)).toHaveLength(2);
    expect(scriptCreationApi.confirmPropExtraction).not.toHaveBeenCalled();
  });
  it("supports select all, clear all and confirming only selected candidates", async () => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([ready]);
    const imported = vi.fn(), user = userEvent.setup();
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} onImported={imported} />);
    await user.click(await screen.findByRole("button", { name: "取消全选" }));
    expect(screen.getByRole("button", { name: /确认入库 0 项/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "全选" }));
    await user.click(screen.getByRole("checkbox", { name: "选择 药瓶" }));
    await user.click(screen.getByRole("button", { name: /确认入库 1 项/ }));
    await waitFor(() => expect(scriptCreationApi.confirmPropExtraction).toHaveBeenCalledWith("demo", "run", { base_revision_id: "r1", candidate_ids: ["box"], client_mutation_id: expect.any(String) }));
    expect(imported).toHaveBeenCalledOnce();
    expect(await screen.findByText(/已入库：新建 1 项，复用 0 项/)).toBeInTheDocument();
  });
  it("shows runtime failures without fabricating entries", async () => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([{ ...ready, status: "failed", candidates: [], error: "模型路由不可用" }]);
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("模型路由不可用");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新提取（调用模型）" })).toBeEnabled();
  });
  it("keeps the confirmation mutation id when retrying an uncertain network result", async () => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([ready]);
    vi.mocked(scriptCreationApi.confirmPropExtraction).mockRejectedValueOnce(new Error("网络响应中断"));
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "全选" }));
    await user.click(screen.getByRole("button", { name: /确认入库 2 项/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("网络响应中断");
    await user.click(screen.getByRole("button", { name: /确认入库 2 项/ }));
    await waitFor(() => expect(scriptCreationApi.confirmPropExtraction).toHaveBeenCalledTimes(2));
    const calls = vi.mocked(scriptCreationApi.confirmPropExtraction).mock.calls;
    expect(calls[1]).toEqual(calls[0]);
  });
  it("marks incomplete new candidates as unable to generate images", async () => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([{ ...ready, candidates: [{ ...ready.candidates[0], visual_prompt: "" }] }]);
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    expect(await screen.findByText("待补充生图信息 · 不能直接出图")).toBeInTheDocument();
  });
  it("rechecks the persisted revision and refuses stale confirmation", async () => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([ready]);
    vi.mocked(scriptCreationApi.get).mockResolvedValue({ ...document, current_revision_id: "r2" });
    render(<PropExtractionDialog project="demo" document={document} onClose={vi.fn()} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "全选" }));
    await userEvent.setup().click(screen.getByRole("button", { name: /确认入库 2 项/ }));
    expect(await screen.findByText("来源道具表已过期，请重新提取后确认。")).toBeInTheDocument();
    expect(scriptCreationApi.confirmPropExtraction).not.toHaveBeenCalled();
  });
  it("blocks writes while creation documents contain unsaved edits", async () => {
    vi.mocked(scriptCreationApi.listPropExtractions).mockResolvedValue([ready]);
    render(<PropExtractionDialog project="demo" document={document} allSaved={false} onClose={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /确认入库 0 项/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新提取（调用模型）" })).toBeDisabled();
  });
});
