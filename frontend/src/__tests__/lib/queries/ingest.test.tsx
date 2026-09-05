// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { BillingRuleNotConfiguredError } from "@/lib/api-errors";
import {
  useStartIngest,
  useSwitchKnowledgePipeline,
  useUploadNovel,
} from "@/lib/queries/ingest";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("ingest query error contract", () => {
  it("rejects upload responses that return ok:false with a backend error", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/ingest/upload", () =>
        HttpResponse.json({ ok: false, error: "解析章节失败: 文件编码不支持" }),
      ),
    );

    const { result } = renderHook(() => useUploadNovel("demo"), { wrapper });
    result.current.mutate(new File(["bad"], "broken.txt", { type: "text/plain" }));

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe("解析章节失败: 文件编码不支持");
  });

  it("rejects start responses that return ok:false with a backend error", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/ingest/start", () =>
        HttpResponse.json({ ok: false, error: "File 'missing.txt' not found in uploads/" }),
      ),
    );

    const { result } = renderHook(() => useStartIngest("demo"), { wrapper });
    result.current.mutate({ filename: "missing.txt", rebuild: true });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe("File 'missing.txt' not found in uploads/");
  });

  it("maps start 409 billing rule responses to the billing rule error", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/ingest/start", () =>
        HttpResponse.json(
          {
            ok: false,
            error: "计费规则未配置，请联系管理员设置积分规则",
            data: {
              error_code: "BILLING_RULE_NOT_CONFIGURED",
              billing_kind: "feature",
              billing_key: "ingest_fast",
            },
          },
          { status: 409 },
        ),
      ),
    );

    const { result } = renderHook(() => useStartIngest("demo"), { wrapper });
    result.current.mutate({ filename: "novel.txt", rebuild: true });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(BillingRuleNotConfiguredError);
    expect(result.current.error?.message).toBe("计费规则未配置，请联系管理员设置积分规则");
  });

  it("sends the explicitly selected structured pipeline when starting ingest", async () => {
    let requestBody: unknown;
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/ingest/start", async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json({ ok: true, data: { task_id: "ingest-1" } });
      }),
    );

    const { result } = renderHook(() => useStartIngest("demo"), { wrapper });
    result.current.mutate({
      filename: "novel.txt",
      rebuild: true,
      knowledge_pipeline: "structured_v1",
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(requestBody).toEqual({
      filename: "novel.txt",
      rebuild: true,
      knowledge_pipeline: "structured_v1",
    });
  });

  it("surfaces the backend rejection reason when switching knowledge pipeline", async () => {
    server.use(
      http.patch("http://localhost:3000/api/v1/projects/demo/knowledge-pipeline", () =>
        HttpResponse.json(
          {
            detail: {
              code: "KNOWLEDGE_PIPELINE_LOCKED",
              message: "已有正式资产，不能切换知识图谱",
              formal_asset_count: 3,
            },
          },
          { status: 409 },
        ),
      ),
    );

    const { result } = renderHook(() => useSwitchKnowledgePipeline("demo"), { wrapper });
    result.current.mutate();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe("已有正式资产，不能切换知识图谱");
  });
});
