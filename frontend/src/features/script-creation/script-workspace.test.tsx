import { act, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import ky, { type Options } from "ky";
import { beforeEach } from "vitest";
import { describe, expect, it, vi } from "vitest";
vi.mock("@/lib/api", () => ({ api: ky.create({ baseUrl: "http://localhost:3000/" }) }));
vi.mock("@/api/client", () => ({
  apiCall: async (path: string, options?: Options) => {
    const response = await ky("http://localhost:3000/api/v1/" + path, { ...options, retry: { limit: 0 } });
    const envelope = await response.json() as { data: unknown };
    return envelope.data;
  },
}));

import { server } from "@/__mocks__/msw/server";
import { ScriptWorkspace } from "./script-workspace";
import type { ScriptDocument, ScriptDocumentKind } from "./types";

const base = "/api/v1/projects/demo/script-creation";
function doc(id: string, kind: ScriptDocumentKind, markdown: string, episodeNumber = 1): ScriptDocument {
  return { id, kind, title: id, episode_number: kind === "episode_script" ? episodeNumber : null,
    current_revision_id: "r1", adopted_revision_id: null, source_origin: null, created_at: "", updated_at: "",
    revision: { id: "r1", document_id: id, parent_revision_id: null, markdown,
      blocks: [{ id: "b1", markdown }], client_mutation_id: "create", created_at: "", restored_from_revision_id: null } };
}

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ScriptWorkspace project="demo" /></QueryClientProvider>);
}

beforeEach(() => {
  localStorage.clear();
  server.use(http.get("/api/v1/projects/demo/script-creation/generations", () => HttpResponse.json({ ok: true, data: [] })));
  server.use(http.get("/api/v1/projects/demo/episode-imports", () =>
    HttpResponse.json({ ok: true, data: { items: [], imports: [], stale: [], project_revision: 0, migration_status: "", confirmation_required: false } })));
});

describe("ScriptWorkspace", () => {
  it("keeps a rapid unsaved edit recoverable after leaving, even when the exit save fails", async () => {
    const user = userEvent.setup();
    const outline = doc("one", "outline", "# 故事大纲");
    let attempts = 0;
    server.use(
      http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: [outline] })),
      http.put(base + "/documents/one", () => { attempts++; return HttpResponse.json({ detail: "offline" }, { status: 500 }); }),
    );
    const view = renderWorkspace();
    await user.click(await screen.findByRole("button", { name: "故事大纲" }));
    await user.click(screen.getByRole("button", { name: "编辑 Markdown" }));
    await user.type(screen.getByRole("textbox", { name: "文档 Markdown" }), " 新内容");
    view.unmount();
    await waitFor(() => expect(attempts).toBe(1));
    renderWorkspace();
    await user.click(await screen.findByRole("button", { name: "故事大纲" }));
    await user.click(screen.getByRole("button", { name: "编辑 Markdown" }));
    expect(screen.getByRole("textbox", { name: "文档 Markdown" })).toHaveValue("# 故事大纲 新内容");
    expect(screen.getByText(/待保存|保存失败|版本冲突/)).toBeInTheDocument();
  });

  it("does not discard a retained draft when the initial document list fails", async () => {
    localStorage.setItem("script-creation-drafts:demo", JSON.stringify({ one: { markdown: "本地草稿", revisionId: "r1" } }));
    server.use(http.get(base + "/documents", () => HttpResponse.json({ detail: "offline" }, { status: 500 })));
    const view = renderWorkspace();
    expect(await screen.findByRole("button", { name: "重试加载" })).toBeInTheDocument();
    view.unmount();
    expect(localStorage.getItem("script-creation-drafts:demo")).toContain("本地草稿");
  });

  it("keeps a recovered draft in conflict when the server has a newer revision", async () => {
    const serverDoc = doc("one", "outline", "服务器内容");
    serverDoc.current_revision_id = "r2";
    serverDoc.revision.id = "r2";
    localStorage.setItem("script-creation-drafts:demo", JSON.stringify({ one: { markdown: "本地草稿", revisionId: "r1" } }));
    server.use(http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: [serverDoc] })));
    renderWorkspace();
    await screen.findByRole("button", { name: "故事大纲" });
    expect(screen.getByText("版本冲突")).toBeInTheDocument();
    expect(screen.getByText("本地草稿")).toBeInTheDocument();
  });

  it("ignores a late project A document list after switching to project B", async () => {
    let releaseA!: () => void;
    const waitingA = new Promise<void>((resolve) => { releaseA = resolve; });
    server.use(
      http.get(base + "/documents", async () => { await waitingA; return HttpResponse.json({ ok: true, data: [doc("a", "outline", "# A") ] }); }),
      http.get("/api/v1/projects/other/script-creation/documents", () => HttpResponse.json({ ok: true, data: [doc("b", "people", "# B")] })),
      http.get("/api/v1/projects/other/episode-imports", () => HttpResponse.json({ ok: true, data: { items: [], imports: [], stale: [], project_revision: 0, migration_status: "", confirmation_required: false } })),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrap = (project: string) => <QueryClientProvider client={client}><ScriptWorkspace project={project} /></QueryClientProvider>;
    const view = render(wrap("demo"));
    view.rerender(wrap("other"));
    expect(await screen.findByRole("button", { name: "人物小传" })).toBeInTheDocument();
    await act(async () => { releaseA(); await waitingA; });
    expect(screen.getByRole("button", { name: "人物小传" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "故事大纲" })).not.toBeInTheDocument();
  });

  it("does not duplicate a document after its create response is lost during a batch", async () => {
    const user = userEvent.setup();
    const docs = [doc("brief", "brief", "# 创作简报")];
    let lost = false;
    server.use(
      http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: docs })),
      http.post(base + "/documents", async ({ request }) => {
        const body = await request.json() as { kind: ScriptDocumentKind; markdown: string; episode_number?: number };
        if (body.kind === "outline" && !lost) {
          lost = true;
          docs.push(doc("outline", body.kind, body.markdown));
          return HttpResponse.json({ detail: "response lost" }, { status: 500 });
        }
        const created = doc(String(docs.length + 1), body.kind, body.markdown, body.episode_number);
        docs.push(created);
        return HttpResponse.json({ ok: true, data: created });
      }),
    );
    renderWorkspace();
    await user.click(await screen.findByRole("button", { name: "创建空白文档" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/response lost|500/);
    await user.click(screen.getByRole("button", { name: "创建空白文档" }));
    await waitFor(() => expect(docs).toHaveLength(7));
    expect(docs.filter((entry) => entry.kind === "outline")).toHaveLength(1);
  });
  it("creates blank editable documents through the real document API", async () => {
    const user = userEvent.setup();
    const docs: ScriptDocument[] = [];
    server.use(
      http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: docs })),
      http.post(base + "/documents", async ({ request }) => {
        const body = await request.json() as { kind: ScriptDocumentKind; markdown: string; episode_number?: number };
        const created = doc(String(docs.length + 1), body.kind, body.markdown, body.episode_number);
        docs.push(created);
        return HttpResponse.json({ ok: true, data: created });
      }),
    );
    renderWorkspace();
    await user.click(await screen.findByRole("button", { name: "从创作预设开始" }));
    await user.click(screen.getByRole("button", { name: "保存创作设定" }));
    await waitFor(() => expect(docs.length).toBe(1));
    await user.click(screen.getByRole("button", { name: "创建空白文档" }));
    await waitFor(() => expect(docs.length).toBe(7));
    expect(docs.some((entry) => entry.kind === "episode_script")).toBe(true);
    expect(docs.map((entry) => entry.revision.markdown).join("\n")).not.toMatch(/林川|沈青|讨薪/);
    expect(await screen.findByRole("button", { name: "人物小传" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "新建第 2 集" }));
    await waitFor(() => expect(docs.some((entry) => entry.episode_number === 2)).toBe(true));
    const nextMarkdown = docs.find((entry) => entry.episode_number === 2)?.revision.markdown;
    expect(nextMarkdown).toContain("\n\n## 2-1");
    expect(nextMarkdown).toContain("本集目标：");
    expect(nextMarkdown).toContain("场景名称 · 日/夜 · 内/外");
    expect(nextMarkdown).toContain("必要语气提示");
    expect(nextMarkdown).toContain("结尾钩子：");
    await user.click(screen.getByRole("button", { name: "第 2 集" }));
    expect(screen.getByRole("button", { name: "2-1｜场景名称 · 日/夜 · 内/外" })).toBeInTheDocument();
  });

  it("recovers the empty workspace after retrying a failed list request", async () => {
    const user = userEvent.setup();
    let requests = 0;
    server.use(http.get(base + "/documents", () => {
      requests++;
      return requests === 1 ? HttpResponse.json({ detail: "offline" }, { status: 500 })
        : HttpResponse.json({ ok: true, data: [] });
    }));
    renderWorkspace();
    await user.click(await screen.findByRole("button", { name: "重试加载" }));
    expect(await screen.findByRole("button", { name: "从创作预设开始" })).toBeInTheDocument();
  });

  it("shows a save failure and retains text for retry", async () => {
    const user = userEvent.setup();
    const outline = doc("one", "outline", "# 故事大纲\n\n## 故事简述");
    let failed = false;
    server.use(
      http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: [outline] })),
      http.put(base + "/documents/one", async ({ request }) => {
        if (!failed) { failed = true; return HttpResponse.json({ detail: "offline" }, { status: 500 }); }
        const body = await request.json() as { markdown: string };
        return HttpResponse.json({ ok: true, data: doc("one", "outline", body.markdown) });
      }),
    );
    renderWorkspace();
    await user.click(await screen.findByRole("button", { name: "故事大纲" }));
    await user.click(screen.getByRole("button", { name: "编辑 Markdown" }));
    const editor = screen.getByRole("textbox", { name: "文档 Markdown" });
    await user.type(editor, " 修改{enter}{enter}## 新情节");
    expect(await screen.findByRole("button", { name: "新情节" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "立即保存" }));
    expect(await screen.findByText("保存失败")).toBeInTheDocument();
    expect(editor).toHaveValue("# 故事大纲\n\n## 故事简述 修改\n\n## 新情节");
    await user.click(screen.getByRole("button", { name: "重试保存" }));
    await waitFor(() => expect(screen.getByText("已保存")).toBeInTheDocument());
  });
});

describe("ScriptWorkspace saved revision wiring", () => {
  it("uses the saved head and saved background revisions when submitting a rewrite", async () => {
    const user = userEvent.setup();
    const script = doc("script", "episode_script", "旧句");
    const people = doc("people", "people", "旧人物");
    let submitted: { base_revision_id: string; context_revisions: Record<string, string> } | null = null;
    server.use(
      http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: [script, people] })),
      http.put(base + "/documents/people", async ({ request }) => {
        const body = await request.json() as { markdown: string };
        const saved = doc("people", "people", body.markdown);
        saved.current_revision_id = "people-r2";
        saved.revision.id = "people-r2";
        return HttpResponse.json({ ok: true, data: saved });
      }),
      http.put(base + "/documents/script", async ({ request }) => {
        const body = await request.json() as { markdown: string };
        const saved = doc("script", "episode_script", body.markdown);
        saved.current_revision_id = "script-r2";
        saved.revision.id = "script-r2";
        return HttpResponse.json({ ok: true, data: saved });
      }),
      http.get(base + "/documents/script/proposals", () => HttpResponse.json({ ok: true, data: [] })),
      http.post(base + "/rewrites", async ({ request }) => {
        submitted = await request.json() as typeof submitted;
        return HttpResponse.json({ ok: true, data: { id: "job", status: "failed", error: "model unavailable" } });
      }),
    );
    renderWorkspace();
    await screen.findByRole("button", { name: "第 1 集" });
    await user.click(screen.getByRole("button", { name: "人物小传" }));
    await user.click(screen.getByRole("button", { name: "编辑 Markdown" }));
    await user.type(screen.getByRole("textbox", { name: "文档 Markdown" }), "新");
    await user.click(screen.getByRole("button", { name: "立即保存" }));
    await waitFor(() => expect(screen.getByText("已保存")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "第 1 集" }));
    await user.click(screen.getByRole("button", { name: "编辑 Markdown" }));
    await user.type(screen.getByRole("textbox", { name: "文档 Markdown" }), "新");
    await user.click(screen.getByRole("button", { name: "立即保存" }));
    await waitFor(() => expect(screen.getByText("已保存")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "候选审阅" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "改稿范围" }), "episode");
    await user.click(screen.getByRole("button", { name: "生成改稿候选" }));
    await waitFor(() => expect(submitted).not.toBeNull());
    expect(submitted!.base_revision_id).toBe("script-r2");
    expect(submitted!.context_revisions.people).toBe("people-r2");
  });

  it("restores from the saved head even when the document list still has an older head", async () => {
    const user = userEvent.setup();
    const script = doc("script", "episode_script", "旧句");
    let restoredBase = "";
    server.use(
      http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: [script] })),
      http.put(base + "/documents/script", async ({ request }) => {
        const body = await request.json() as { markdown: string };
        const saved = doc("script", "episode_script", body.markdown);
        saved.current_revision_id = "r2";
        saved.revision.id = "r2";
        return HttpResponse.json({ ok: true, data: saved });
      }),
      http.get(base + "/documents/script/revisions", () => HttpResponse.json({ ok: true, data: [
        script.revision, { ...script.revision, id: "r2", markdown: "旧句新" },
      ] })),
      http.post(base + "/documents/script/restore", async ({ request }) => {
        restoredBase = (await request.json() as { base_revision_id: string }).base_revision_id;
        return HttpResponse.json({ ok: true, data: script });
      }),
    );
    renderWorkspace();
    await screen.findByRole("button", { name: "第 1 集" });
    await user.click(screen.getByRole("button", { name: "编辑 Markdown" }));
    await user.type(screen.getByRole("textbox", { name: "文档 Markdown" }), "新");
    await user.click(screen.getByRole("button", { name: "立即保存" }));
    await waitFor(() => expect(screen.getByText("已保存")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "版本历史" }));
    await screen.findByRole("button", { name: /r1/ });
    await user.click(screen.getByRole("button", { name: /r1/ }));
    await user.click(screen.getByRole("button", { name: "恢复所选版本" }));
    await waitFor(() => expect(restoredBase).toBe("r2"));
  });
});

describe("ScriptWorkspace compact layout", () => {
  it("opens document tree and AI creation actions from compact header controls", async () => {
    const user = userEvent.setup();
    server.use(http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: [doc("brief", "brief", "创作设定") ] })));
    renderWorkspace();
    await screen.findByRole("button", { name: "创作简报" });
    await user.click(screen.getByRole("button", { name: "打开 AI 创作" }));
    expect(screen.getByRole("complementary", { name: "AI 协作" })).toHaveClass("fixed");
    expect(screen.getByRole("button", { name: "生成故事框架与首集" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开文档目录" }));
    expect(screen.getByRole("complementary", { name: "创作文档树" })).toHaveClass("fixed");
    await user.click(screen.getByRole("button", { name: "创作简报" }));
    expect(screen.getByRole("complementary", { name: "创作文档树" })).not.toHaveClass("fixed");
  });
});


it("opens the consistency panel and checks the saved current episode", async () => {
  const user = userEvent.setup();
  const episode = doc("episode", "episode_script", "甲持有钥匙。", 2);
  const calls: unknown[] = [];
  server.use(
    http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: [episode] })),
    http.get(base + "/documents/episode/proposals", () => HttpResponse.json({ ok: true, data: [] })),
    http.get(base + "/consistency-runs", () => HttpResponse.json({ ok: true, data: [] })),
    http.post(base + "/consistency-runs", async ({ request }) => {
      calls.push(await request.json());
      return HttpResponse.json({ ok: true, data: { id: "check", episode_document_id: "episode",
        context_revisions: { episode: "r1" }, mode: "actual", proposal_id: null,
        hypothetical_document_id: null, status: "pending", task_id: "task", error: null, issues: [] } });
    }),
    http.get(base + "/consistency-runs/check", () => HttpResponse.json({ ok: true, data: {
      id: "check", episode_document_id: "episode", context_revisions: { episode: "r1" }, mode: "actual",
      proposal_id: null, hypothetical_document_id: null, status: "completed", task_id: "task", error: null, issues: [],
    } })),
  );
  renderWorkspace();
  await user.click(await screen.findByRole("button", { name: "关联检查" }));
  await user.click(screen.getByRole("button", { name: "检查当前集" }));
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0]).toMatchObject({ episode_document_id: "episode", context_revisions: { episode: "r1" }, proposal_id: null });
});


describe("ScriptWorkspace handoff gate", () => {
  it("disables episode handoff while any document has an unsaved draft", async () => {
    const user = userEvent.setup();
    const episode = doc("episode", "episode_script", "# 正文\n\n## 1-1 内景");
    const people = doc("people", "people", "# 人物");
    server.use(http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: [episode, people] })));
    renderWorkspace();
    await user.click(await screen.findByRole("button", { name: "人物小传" }));
    await user.click(screen.getByRole("button", { name: "编辑 Markdown" }));
    await user.type(screen.getByRole("textbox", { name: "文档 Markdown" }), " 新内容");
    await user.click(screen.getByRole("button", { name: "第 1 集" }));
    expect(screen.getByRole("button", { name: "确认本集并交接制作" })).toBeDisabled();
    expect(screen.queryByRole("dialog", { name: "确认本集并交接制作" })).not.toBeInTheDocument();
  });
});


describe("ScriptWorkspace adopted revision", () => {
  it("shows the adopted revision after a handoff that keeps the current draft revision", async () => {
    const user = userEvent.setup();
    let episode = doc("episode", "episode_script", "# 正文\n\n## 1-1 内景");
    const handoff = { id: "handoff", status: "prepared", document_id: "episode", revision_id: "r1",
      episode_number: 1, expected_source_project_revision: 0, source_revision: null,
      source_hash: null, task_id: null, task_result: null, error: null, created_at: "", updated_at: "",
      snapshot: { document_id: "episode", revision_id: "r1", episode_number: 1, title: "第 1 集",
        markdown: episode.revision.markdown, reference_revisions: {}, references: [], entities: [],
        fact_acknowledgement: { mode: "unchecked", reason: "人工核对", run_id: null, known_fact_issues: {} },
        update_scope: { mode: "none", scene_ids: [] }, previous_source: null, previous_stage_revisions: {} },
      diff: { text: [], scenes: [], dialogue: [], references: [], entity_references: [], reused_scenes: [],
        affected_nonupdated_scene_ids: [], available_scene_ids: [], needs_reparse: false, inferred_impacts: [] } };
    server.use(
      http.get(base + "/documents", () => HttpResponse.json({ ok: true, data: [episode] })),
      http.get(base + "/entities", () => HttpResponse.json({ ok: true, data: [] })),
      http.get(base + "/consistency-runs", () => HttpResponse.json({ ok: true, data: [] })),
      http.get(base + "/handoffs", () => HttpResponse.json({ ok: true, data: [] })),
      http.post(base + "/handoffs/prepare", () => HttpResponse.json({ ok: true, data: handoff })),
      http.post(base + "/handoffs/handoff/confirm", () => {
        episode = { ...episode, adopted_revision_id: "r1" };
        return HttpResponse.json({ ok: true, data: { ...handoff, status: "completed", source_revision: "source-r1" } }, { status: 202 });
      }),
    );
    renderWorkspace();
    await user.click(await screen.findByRole("button", { name: "确认本集并交接制作" }));
    await screen.findByText("选择交接范围");
    await user.type(screen.getByRole("textbox", { name: "未运行关联检查的说明" }), "人工核对");
    await user.click(screen.getByRole("button", { name: "预览交接差异" }));
    const dialog = screen.getByRole("dialog", { name: "确认本集并交接制作" });
    await user.click(await within(dialog).findByRole("button", { name: "确认本集并交接制作" }));
    await user.click(screen.getByRole("button", { name: "关闭交接窗口" }));
    await waitFor(() => expect(screen.getByText(/已交接 r1/)).toBeInTheDocument());
  });
});
