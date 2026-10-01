import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeGraphVisualization } from "@/components/ingest/KnowledgeGraphVisualization";
import type { KnowledgeGraphSnapshot } from "@/lib/queries/ingest";

const graph: KnowledgeGraphSnapshot = {
  revision: "v1", editable: true, edit_semantics: "graph_annotations",
  nodes: [{ id: "a", label: "白尾", type: "Character", degree: 1, properties: { description: "少年" } },
    { id: "b", label: "雨巷", type: "Scene", degree: 1, properties: {} }],
  edges: [{ id: "e", source: "a", target: "b", relation: "出现于", properties: {} }],
  total_nodes: 2, total_edges: 1, truncated: false,
};

describe("knowledge graph interactions", () => {
  it("zooms and drags nodes without panning the entire graph", () => {
    const { container } = render(<KnowledgeGraphVisualization graph={graph} />);
    const svg = screen.getByLabelText("知识图谱画布");
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({ width: 1000, height: 520, x: 0, y: 0, top: 0, left: 0, bottom: 520, right: 1000, toJSON: () => ({}) });
    const node = screen.getByRole("button", { name: "白尾, Character" });
    const start = node.getAttribute("transform");
    const view = container.querySelector("svg > g");
    const viewStart = view?.getAttribute("transform");
    fireEvent.pointerDown(node, { pointerId: 1, button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 160, clientY: 130 });
    fireEvent.pointerUp(svg, { pointerId: 1 });
    expect(node.getAttribute("transform")).not.toBe(start);
    const before = start!.match(/translate\(([^ ]+) ([^)]+)\)/)!;
    const after = node.getAttribute("transform")!.match(/translate\(([^ ]+) ([^)]+)\)/)!;
    expect(Number(after[1]) - Number(before[1])).toBeCloseTo(60);
    expect(Number(after[2]) - Number(before[2])).toBeCloseTo(30);
    expect(view?.getAttribute("transform")).toBe(viewStart);
    fireEvent.wheel(svg, { deltaY: -100 });
    expect(view?.getAttribute("transform")).not.toBe(viewStart);
  });
  it("rejects invalid properties and retains the form", async () => {
    const save = vi.fn();
    render(<KnowledgeGraphVisualization graph={graph} onSave={save} />);
    await userEvent.click(screen.getByRole("button", { name: "白尾, Character" }));
    await userEvent.click(screen.getByRole("button", { name: "编辑节点" }));
    fireEvent.change(screen.getByLabelText("节点属性（JSON）"), { target: { value: "[]" } });
    await userEvent.click(screen.getByRole("button", { name: "保存节点" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("属性必须是有效的 JSON 对象");
    expect(save).not.toHaveBeenCalled();
  });
  it("edits a node using the original revision and preserves failed drafts", async () => {
    const save = vi.fn().mockRejectedValue(new Error("版本已变化，请刷新"));
    render(<KnowledgeGraphVisualization graph={graph} onSave={save} />);
    await userEvent.click(screen.getByRole("button", { name: "白尾, Character" }));
    await userEvent.click(screen.getByRole("button", { name: "编辑节点" }));
    const label = screen.getByLabelText("节点名称");
    await userEvent.clear(label); await userEvent.type(label, "白尾少年");
    await userEvent.click(screen.getByRole("button", { name: "保存节点" }));
    expect(save).toHaveBeenCalledWith({ revision: "v1", node_updates: [{ id: "a", label: "白尾少年", properties: { description: "少年" } }] });
    expect(await screen.findByRole("alert")).toHaveTextContent("版本已变化，请刷新");
    expect(label).toHaveValue("白尾少年");
  });
  it("saves edited relationship names and properties", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(<KnowledgeGraphVisualization graph={graph} onSave={save} />);
    await userEvent.click(screen.getByRole("button", { name: "白尾, Character" }));
    await userEvent.click(screen.getByRole("button", { name: "编辑关系 出现于" }));
    await userEvent.clear(screen.getByLabelText("关系名称"));
    await userEvent.type(screen.getByLabelText("关系名称"), "居住于");
    fireEvent.change(screen.getByLabelText("关系属性（JSON）"), { target: { value: '{"来源":"第一章"}' } });
    await userEvent.click(screen.getByRole("button", { name: "保存关系" }));
    expect(save).toHaveBeenCalledWith({ revision: "v1", edge_updates: [{ id: "e", relation: "居住于", source: "a", target: "b", properties: { 来源: "第一章" } }] });
    await waitFor(() => expect(screen.queryByLabelText("关系名称")).not.toBeInTheDocument());
  });
  it("shows empty graph help rather than hiding the section", () => {
    render(<KnowledgeGraphVisualization graph={{ ...graph, nodes: [], edges: [], total_nodes: 0, total_edges: 0 }} />);
    expect(screen.getByText("暂无知识图谱节点")).toBeInTheDocument();
  });
  it("can search and expand nodes without allowing read-only edits", async () => {
    render(<KnowledgeGraphVisualization graph={{ ...graph, editable: false }} />);
    await userEvent.type(screen.getByLabelText("搜索图谱节点"), "白尾");
    await userEvent.click(screen.getByRole("button", { name: "定位 白尾" }));
    await userEvent.click(screen.getByRole("button", { name: "展开相邻节点" }));
    expect(screen.getByText("出现于")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑节点" })).not.toBeInTheDocument();
  });
});
