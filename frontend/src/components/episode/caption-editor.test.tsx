import { render, screen, fireEvent, act } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CaptionEditor } from "./caption-editor";
const api = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ api }));
it("shows draft, explicit manual confirmation and same-event playback", async () => {
  const data = { revision: "unsaved", track: { duration: 4, audio_fingerprint: "a", timing_basis: "actual", source_durations: [4], events: [{ dialogue_id: "d1", speaker: "甲", text: "你好", start: 1, end: 2, source: "estimated", review_status: "needs_review", audio_fingerprint: "a" }] } };
  api.get.mockReturnValue({ json: async () => ({ data }) });
  api.put.mockReturnValue({ json: async () => ({ data }) });
  render(<CaptionEditor project="demo" episode={1} currentTime={1.5} previewReady onSeek={vi.fn()} />);
  expect(await screen.findByText("1 · 估算草稿 · 待复核")).toBeInTheDocument();
  expect(screen.getByLabelText("当前字幕")).toHaveTextContent("你好");
  fireEvent.click(screen.getByRole("button", { name: "确认此条校时" }));
  fireEvent.click(screen.getByRole("button", { name: "保存字幕" }));
  expect(api.put).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ json: expect.objectContaining({ track: expect.objectContaining({ events: [expect.objectContaining({ source: "manual", review_status: "approved" })] }) }) }));
});

function draft(text: string) {
  return { data: { revision: text, track: { duration: 4, audio_fingerprint: "a", timing_basis: "actual", source_durations: [4], events: [{ dialogue_id: "d1", speaker: "甲", text, start: 1, end: 2, source: "estimated", review_status: "needs_review", audio_fingerprint: "a" }] } } };
}

it.each([true, false])("shows alignment errors after response consumption (parsed=%s)", async (parsed) => {
  api.get.mockReturnValue({ json: async () => draft("原对白") });
  const response = new Response(JSON.stringify({detail: "本地模型缺失"}), {status: 409});
  await response.json();
  api.post.mockReturnValue({json: async () => { throw Object.assign(new Error("请求失败，请重试"), {
    response, ...(parsed ? {data: {detail: "本地模型缺失"}} : {})
  }); }});
  render(<CaptionEditor project="demo" episode={1} currentTime={0} previewReady={false} onSeek={vi.fn()} />);
  await screen.findByDisplayValue("原对白");
  fireEvent.click(screen.getByRole("button", {name: "从原声对齐"}));
  expect(await screen.findByRole("alert")).toHaveTextContent(parsed ? "本地模型缺失" : "请求失败，请重试");
  expect(screen.getByRole("button", {name: "从原声对齐"})).toBeEnabled();
});

it("aligns only on explicit action and displays retained alignment issues", async () => {
  api.get.mockReturnValue({ json: async () => draft("原对白") });
  api.post.mockClear();
  const aligned = draft("原对白");
  api.post.mockReturnValue({ json: async () => ({ data: { ...aligned.data, track: { ...aligned.data.track,
    alignment_issues: [{kind: "unmatched_script", message: "保留估算时间，请试听复核"}] } } }) });
  render(<CaptionEditor project="demo" episode={1} currentTime={0} previewReady={false} onSeek={vi.fn()} />);
  await screen.findByDisplayValue("原对白");
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "从原声对齐" }));
  expect(await screen.findByText("保留估算时间，请试听复核")).toBeInTheDocument();
  expect(api.post).toHaveBeenCalledWith(expect.stringMatching(/captions\/align$/), expect.objectContaining({json: {revision: "原对白"}}));
  expect(screen.getByRole("button", {name: "确认此条校时"})).toBeDisabled();
});

it("blocks audition and approval without a ready preview and protects dirty edits", async () => {
  api.get.mockReturnValue({ json: async () => draft("本集对白") });
  render(<CaptionEditor project="demo" episode={1} currentTime={0} previewReady={false} onSeek={vi.fn()} />);
  await screen.findByDisplayValue("本集对白");
  expect(screen.getByRole("button", { name: "试听此条" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "确认此条校时" })).toBeDisabled();
  expect(screen.getByText(/先生成并加载测试预览/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("第1条字幕"), { target: { value: "修改" } });
  expect(screen.getByRole("button", { name: "重新载入" })).toBeDisabled();
});

it("ignores an old episode response arriving after the new episode", async () => {
  let resolveOld!: (value: ReturnType<typeof draft>) => void;
  api.get.mockReturnValueOnce({ json: () => new Promise(resolve => { resolveOld = resolve; }) })
    .mockReturnValueOnce({ json: async () => draft("第二集") });
  const { rerender } = render(<CaptionEditor project="demo" episode={1} currentTime={0} previewReady onSeek={vi.fn()} />);
  rerender(<CaptionEditor project="demo" episode={2} currentTime={0} previewReady onSeek={vi.fn()} />);
  await screen.findByDisplayValue("第二集");
  await act(async () => resolveOld(draft("第一集")));
  expect(screen.getByDisplayValue("第二集")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("第一集")).not.toBeInTheDocument();
});

it("preserves edits after a conflict until explicit discard confirmation", async () => {
  api.get.mockReturnValueOnce({ json: async () => draft("原对白") })
    .mockReturnValueOnce({ json: async () => draft("远端新版") });
  api.put.mockReturnValue({ json: async () => { throw { response: new Response(JSON.stringify({ detail: "CAPTION_REVISION_CHANGED" }), { status: 409 }) }; } });
  render(<CaptionEditor project="demo" episode={1} currentTime={0} previewReady onSeek={vi.fn()} />);
  await screen.findByDisplayValue("原对白");
  fireEvent.change(screen.getByLabelText("第1条字幕"), { target: { value: "本地修改" } });
  fireEvent.click(screen.getByRole("button", { name: "保存字幕" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "放弃修改并重新载入" }));
  expect(screen.getByDisplayValue("本地修改")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保留修改" }));
  expect(screen.getByDisplayValue("本地修改")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "放弃修改并重新载入" }));
  fireEvent.click(screen.getByRole("button", { name: "确认放弃并载入" }));
  expect(await screen.findByDisplayValue("远端新版")).toBeInTheDocument();
});
