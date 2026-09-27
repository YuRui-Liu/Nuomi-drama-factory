import { render, screen, waitFor } from "@testing-library/react";
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
  server.use(http.get("/api/v1/projects/demo/episode-imports", () =>
    HttpResponse.json({ ok: true, data: { items: [], imports: [], stale: [], project_revision: 0, migration_status: "", confirmation_required: false } })));
});

describe("ScriptWorkspace", () => {
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
    expect(docs.find((entry) => entry.episode_number === 2)?.revision.markdown).toContain("\n\n## 2-1");
    await user.click(screen.getByRole("button", { name: "第 2 集" }));
    expect(screen.getByRole("button", { name: "2-1｜地点 · 时间 · 内 / 外" })).toBeInTheDocument();
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
