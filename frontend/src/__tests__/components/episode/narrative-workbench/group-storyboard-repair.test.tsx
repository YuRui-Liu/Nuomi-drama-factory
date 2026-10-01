import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import { GroupBeatInspector } from "@/components/episode/narrative-workbench/group-beat-inspector";
import { useNarrativeGroups, type NarrativeGroup } from "@/lib/queries/narrative-groups";
import { queryKeys } from "@/lib/query-keys";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { get, post } }));
const original = "原始 prompt\n  保留空格";
const data = { shot_id: "shot-1", stage: "render", source_revision: 2, source_asset: "opaque:abc", original_prompt: original, prompt: original, feedback: "", repair_status: "idle", repair_error: "" };
const group: NarrativeGroup = {
  id: "ng-1", ordinal: 1, beat_ids: ["shot-1", "shot-2"], layout: { rows: 1, columns: 2, capacity: 2 },
  stages: { sketch: { status: "pending", revision: 0 }, video: { status: "pending", revision: 0 }, render: { status: "completed", revision: 2, cell_assets: [{ cell: 0, beat_id: "shot-1", url: "/one.png" }, { cell: 1, beat_id: "shot-2", url: "/two.png" }] } },
  cell_to_beat: [{ cell: 0, beat_id: "shot-1" }, { cell: 1, beat_id: "shot-2" }], errors: [],
};
function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={qc}><GroupBeatInspector project="demo" episode={1} group={group} /></QueryClientProvider>);
  fireEvent.click(screen.getAllByRole("button", { name: "重新生成分镜图" })[0]);
  return qc;
}
beforeEach(() => {
  vi.resetAllMocks();
  get.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ok: true, data }))));
  post.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ok: true, data: { task_id: "t1" } }))));
});
it("waits for fresh context before initializing a reopened cached dialog", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData([...queryKeys.narrativeGroups("demo", 1), "ng-1", "render", "cells", "shot-1", "repair"], data);
  let resolveContext!: (response: Response) => void;
  get.mockImplementation(() => new Promise<Response>((resolve) => { resolveContext = resolve; }));
  render(<QueryClientProvider client={qc}><GroupBeatInspector project="demo" episode={1} group={group} /></QueryClientProvider>);
  fireEvent.click(screen.getAllByRole("button", { name: "重新生成分镜图" })[0]);
  expect(screen.queryByLabelText("提示词")).toBeNull();
  resolveContext(new Response(JSON.stringify({ ok: true, data: { ...data, source_revision: 8, source_asset: "fresh", prompt: "fresh prompt" } })));
  expect(await screen.findByLabelText("提示词")).toHaveValue("fresh prompt");
  fireEvent.click(screen.getByRole("button", { name: "提交生成" }));
  await waitFor(() => expect(post).toHaveBeenCalledOnce());
  expect(post.mock.calls[0][1].json).toMatchObject({ source_revision: 8, source_asset: "fresh" });
});
it("updates the dialog preview with the latest cell while preserving its draft", async () => {
  const qc = new QueryClient();
  const view = render(<QueryClientProvider client={qc}><GroupBeatInspector project="demo" episode={1} group={group} /></QueryClientProvider>);
  fireEvent.click(screen.getAllByRole("button", { name: "重新生成分镜图" })[0]);
  await screen.findByLabelText("提示词");
  fireEvent.change(screen.getByLabelText("提示词"), { target: { value: "keep" } });
  const changed = { ...group, stages: { ...group.stages, render: { ...group.stages.render, cell_assets: [{ cell: 0, beat_id: "shot-1", url: "/latest.png" }] } } };
  view.rerender(<QueryClientProvider client={qc}><GroupBeatInspector project="demo" episode={1} group={changed} /></QueryClientProvider>);
  expect(screen.getByAltText("当前分镜图")).toHaveAttribute("src", "/latest.png");
  expect(screen.getByLabelText("提示词")).toHaveValue("keep");
});
it("shows exact original prompt and cancels without a write", async () => {
  setup();
  expect(await screen.findByLabelText("原始提示词")).toHaveValue(original);
  expect(screen.getByLabelText("原始提示词")).toHaveAttribute("readonly");
  fireEvent.change(screen.getByLabelText("提示词"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(post).not.toHaveBeenCalled();
});
it("submits only the selected shot and preserves draft across refresh and errors", async () => {
  const qc = setup();
  await screen.findByLabelText("提示词");
  fireEvent.change(screen.getByLabelText("提示词"), { target: { value: "edited" } });
  fireEvent.change(screen.getByLabelText("修改意见"), { target: { value: "更亮" } });
  await qc.invalidateQueries();
  expect(screen.getByLabelText("提示词")).toHaveValue("edited");
  post.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ error: "修复失败" }), { status: 500 })));
  fireEvent.click(screen.getByRole("button", { name: "提交生成" }));
  await waitFor(() => expect(post).toHaveBeenCalledOnce());
  expect(post.mock.calls[0][0]).toBe("api/v1/projects/demo/episodes/1/narrative-groups/ng-1/render/cells/shot-1/repair");
  expect(post.mock.calls[0][1].json).toEqual({ source_revision: 2, source_asset: "opaque:abc", prompt: "edited", feedback: "更亮" });
  expect(await screen.findByRole("alert")).toHaveTextContent("修复失败");
  expect(screen.getByLabelText("提示词")).toHaveValue("edited");
  expect(screen.getByAltText("Beat shot-2 渲染格位")).toHaveAttribute("src", "/two.png");
  post.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ok: true, data: { task_id: "retry" } }))));
  fireEvent.click(screen.getByRole("button", { name: "提交生成" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1][1].json.prompt).toBe("edited");
});
it("allows another repair after reopening a completed repair and marks missing original explicitly", async () => {
  get.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ok: true, data: { ...data, original_prompt: null, repair_status: "completed" } }))));
  setup();
  expect(await screen.findByLabelText("原始提示词")).toHaveValue("原始提示词未记录");
  expect(screen.getByText("此版本未保存原始提示词，以下以当前分镜描述作为修改起点")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "提交生成" })).toBeEnabled();
});
it("continues updating the cell after closing a pending repair dialog", async () => {
  let stage: "idle" | "running" | "completed" = "idle";
  get.mockImplementation((path: string) => {
    if (path.endsWith("/repair")) return Promise.resolve(new Response(JSON.stringify({ ok: true, data })));
    const current = { ...group, stages: { ...group.stages, render: { ...group.stages.render, provider_parameters: { cell_repair: { status: stage } }, cell_assets: [
      { cell: 0, beat_id: "shot-1", url: stage === "completed" ? "/repaired.png" : "/one.png" },
      { cell: 1, beat_id: "shot-2", url: "/two.png" },
    ] } } };
    return { json: async () => ({ ok: true, data: [current] }) };
  });
  post.mockImplementation(() => {
    stage = "running";
    return Promise.resolve(new Response(JSON.stringify({ ok: true, data: { task_id: "task" } })));
  });
  function ObservedInspector() {
    const query = useNarrativeGroups("demo", 1);
    return query.data?.ok ? <GroupBeatInspector project="demo" episode={1} group={query.data.data[0]} /> : null;
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><ObservedInspector /></QueryClientProvider>);
  fireEvent.click((await screen.findAllByRole("button", { name: "重新生成分镜图" }))[0]);
  await screen.findByLabelText("提示词");
  fireEvent.click(screen.getByRole("button", { name: "提交生成" }));
  await screen.findByText("正在提交或等待生成…");
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  stage = "completed";
  await waitFor(() => expect(screen.getByAltText("Beat shot-1 渲染格位")).toHaveAttribute("src", "/repaired.png"), { timeout: 3500 });
  expect(screen.getByAltText("Beat shot-2 渲染格位")).toHaveAttribute("src", "/two.png");
});
it("disables duplicate submission while the queued repair preserves editable draft", async () => {
  setup();
  await screen.findByLabelText("提示词");
  fireEvent.click(screen.getByRole("button", { name: "提交生成" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "提交生成" })).toBeDisabled());
  fireEvent.change(screen.getByLabelText("提示词"), { target: { value: "next draft" } });
  expect(screen.getByLabelText("提示词")).toHaveValue("next draft");
  expect(post).toHaveBeenCalledOnce();
});
it("polls a queued repair to completion without resetting typed changes", async () => {
  const qc = setup();
  await screen.findByLabelText("提示词");
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  get.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ok: true, data: { ...data, repair_status: "completed", source_revision: 3 } }))));
  fireEvent.click(screen.getByRole("button", { name: "提交生成" }));
  fireEvent.change(screen.getByLabelText("修改意见"), { target: { value: "keep draft" } });
  await screen.findByText("分镜图生成完成，可在版本历史中查看。", {}, { timeout: 3500 });
  expect(screen.getByLabelText("修改意见")).toHaveValue("keep draft");
  expect(invalidate.mock.calls.some(([options]) => options?.queryKey?.includes("revisions"))).toBe(true);
});
