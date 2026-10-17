// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Options } from "ky";

// This browser API client uses a relative production prefix. Give this test a
// same-origin absolute base so undici-backed fetch in jsdom can reach MSW.
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  const { default: ky, HTTPError } = await import("ky");
  const testClient = ky.create({ baseUrl: "http://localhost:3000/api/v1/" });
  async function apiCall<T>(path: string, options?: Options): Promise<T> {
    try {
      const envelope = await testClient(path, options).json<{
        ok: boolean;
        data?: T;
        error?: string;
      }>();
      if (!envelope.ok) {
        throw new actual.ApiError(envelope.error ?? "API returned ok=false", 200, envelope);
      }
      return envelope.data as T;
    } catch (error) {
      if (error instanceof actual.ApiError) throw error;
      if (error instanceof HTTPError) {
        throw new actual.ApiError(error.message, error.response.status);
      }
      throw error;
    }
  }
  return { ...actual, apiCall };
});

import { server } from "@/__mocks__/msw/server";
import { useAgentDiscussion, type AgentDiscussionOwner } from "@/features/agent-runtime/useAgentDiscussion";
import { useAuthStore } from "@/stores/auth-store";

const owner: AgentDiscussionOwner = {
  project: "project-a",
  canvas: "canvas-a",
  conversation: "conversation-a",
};

const scopeKey = (project: string, canvas: string, conversation: string) =>
  `${project}:${canvas}:${conversation}`;

function message(
  requestId: string,
  role: "user" | "assistant",
  value: string,
  status: "pending" | "completed" | "submission_unknown" = "completed",
) {
  return {
    request_id: requestId,
    role,
    status,
    message: value,
    created_at: "2026-10-17T00:00:00Z",
  };
}

function setupApi() {
  const histories = new Map<string, ReturnType<typeof message>[]>();
  const posts: Array<{ project: string; body: Record<string, unknown> }> = [];
  const gets: string[] = [];
  const unexpectedApiRequests: string[] = [];
  server.use(
  http.get("*/api/v1/projects/:project/creation/discussions", ({ params, request }) => {
      const url = new URL(request.url);
      const project = String(params.project);
      const canvas = url.searchParams.get("canvas_id") ?? "";
      const conversation = url.searchParams.get("conversation_id") ?? "";
      gets.push(scopeKey(project, canvas, conversation));
      return HttpResponse.json({
        ok: true,
        data: {
          availability: { available: true, code: "available" },
          messages: histories.get(scopeKey(project, canvas, conversation)) ?? [],
        },
      });
    }),
    http.post("*/api/v1/projects/:project/creation/discussions", async ({ params, request }) => {
      const project = String(params.project);
      const body = (await request.json()) as Record<string, unknown>;
      posts.push({ project, body });
      const key = scopeKey(
        project,
        String(body.canvas_id),
        String(body.conversation_id),
      );
      histories.set(key, [
        message(String(body.request_id), "user", String(body.message)),
        message(String(body.request_id), "assistant", "回复"),
      ]);
      return HttpResponse.json({
        ok: true,
        data: {
          request_id: body.request_id,
          status: "completed",
          created_at: "2026-10-17T00:00:00Z",
          updated_at: "2026-10-17T00:00:01Z",
          message: "回复",
        },
      });
    }),
    http.all("*/api/v1/*", ({ request }) => {
      unexpectedApiRequests.push(`${request.method} ${new URL(request.url).pathname}`);
      return HttpResponse.json({ ok: false, error: "Unexpected API request in discussion test" }, { status: 418 });
    }),
  );
  return { histories, posts, gets, unexpectedApiRequests };
}

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function renderDiscussion(
  initialOwner: AgentDiscussionOwner = owner,
  active = true,
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  return renderHook(
    ({ currentOwner, currentActive }) => useAgentDiscussion(currentOwner, currentActive),
    {
      initialProps: { currentOwner: initialOwner, currentActive: active },
      wrapper: wrapper(client),
    },
  );
}

describe("useAgentDiscussion", () => {
  let originalWindowStorage: Storage | undefined;

  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ username: "alice", role: "writer" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    if (originalWindowStorage) {
      Object.defineProperty(window, "localStorage", {
        value: originalWindowStorage,
        configurable: true,
        writable: true,
      });
      originalWindowStorage = undefined;
    }
  });

  it("loads and sends a read-only conversation using only its owner scope", async () => {
    const api = setupApi();
    const { result } = renderDiscussion();
    await waitFor(() => {
      if (result.current.error) throw new Error(result.current.error);
      expect(result.current.availability?.available).toBe(true);
    });

    let sent = false;
    await act(async () => {
      sent = await result.current.send("  讨论一下开场  ");
    });

    expect(sent).toBe(true);
    expect(api.posts).toHaveLength(1);
    expect(api.posts[0].project).toBe(owner.project);
    expect(api.posts[0].body).toEqual({
      request_id: expect.any(String),
      canvas_id: owner.canvas,
      conversation_id: owner.conversation,
      message: "讨论一下开场",
    });
    expect(api.gets).toContain(scopeKey(owner.project, owner.canvas, owner.conversation));
    expect(api.unexpectedApiRequests).toEqual([]);
    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages.map((item) => item.message)).toEqual([
      "讨论一下开场",
      "回复",
    ]);
  });

  it("keeps A and B histories isolated when switching owners and returning", async () => {
    const api = setupApi();
    api.histories.set(scopeKey(owner.project, owner.canvas, owner.conversation), [
      message("a1", "user", "A 的历史"),
    ]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = renderDiscussion(owner, true, client);
    await waitFor(() => expect(view.result.current.messages[0]?.message).toBe("A 的历史"));

    const ownerB = { ...owner, canvas: "canvas-b", conversation: "conversation-b" };
    api.histories.set(scopeKey(owner.project, ownerB.canvas, ownerB.conversation), [
      message("b1", "user", "B 的历史"),
    ]);
    view.rerender({ currentOwner: ownerB, currentActive: true });
    await waitFor(() => expect(view.result.current.messages[0]?.message).toBe("B 的历史"));

    view.rerender({ currentOwner: owner, currentActive: true });
    await waitFor(() => expect(view.result.current.messages[0]?.message).toBe("A 的历史"));
    expect(view.result.current.messages.some((item) => item.message === "B 的历史")).toBe(false);
  });

  it("does not report an old owner's delayed send as successful after switching", async () => {
    setupApi();
    let release!: () => void;
    let entered!: () => void;
    const requestEntered = new Promise<void>((resolve) => { entered = resolve; });
    const responseGate = new Promise<void>((resolve) => { release = resolve; });
    server.use(
      http.post("*/api/v1/projects/:project/creation/discussions", async () => {
        entered();
        await responseGate;
        return HttpResponse.json({
          ok: true,
          data: {
            request_id: "delayed",
            status: "completed",
            created_at: "2026-10-17T00:00:00Z",
            updated_at: "2026-10-17T00:00:01Z",
          },
        });
      }),
    );
    const view = renderDiscussion();
    await waitFor(() => expect(view.result.current.availability?.available).toBe(true));
    let sent!: Promise<boolean>;
    act(() => { sent = view.result.current.send("A 的问题"); });
    await requestEntered;

    view.rerender({
      currentOwner: { ...owner, canvas: "canvas-b", conversation: "conversation-b" },
      currentActive: true,
    });
    release();
    await act(async () => expect(await sent).toBe(false));
  });

  it("preserves a newer pending request when an unmounted request completes", async () => {
    const api = setupApi();
    let releaseOldRequest!: () => void;
    let oldRequestEntered!: () => void;
    const requestEntered = new Promise<void>((resolve) => { oldRequestEntered = resolve; });
    const oldResponseGate = new Promise<void>((resolve) => { releaseOldRequest = resolve; });
    server.use(
      http.post("*/api/v1/projects/:project/creation/discussions", async ({ params, request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        api.posts.push({ project: String(params.project), body });
        if (body.message === "卸载前的请求") {
          oldRequestEntered();
          await oldResponseGate;
          return HttpResponse.json({
            ok: true,
            data: {
              request_id: body.request_id,
              status: "completed",
              created_at: "2026-10-17T00:00:00Z",
              updated_at: "2026-10-17T00:00:01Z",
            },
          });
        }
        return HttpResponse.json({ ok: false, error: "submission uncertain" }, { status: 503 });
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const first = renderDiscussion(owner, true, client);
    await waitFor(() => expect(first.result.current.availability?.available).toBe(true));
    let oldSend!: Promise<boolean>;
    act(() => { oldSend = first.result.current.send("卸载前的请求"); });
    await requestEntered;
    first.unmount();

    const second = renderDiscussion(owner, true, client);
    await waitFor(() => expect(second.result.current.availability?.available).toBe(true));
    await act(async () => expect(await second.result.current.send("新挂载的未确认请求")).toBe(false));
    const newerRequestId = second.result.current.pendingRequests.find(
      (item) => item.message === "新挂载的未确认请求",
    )?.requestId;
    expect(newerRequestId).toEqual(expect.any(String));

    releaseOldRequest();
    await act(async () => expect(await oldSend).toBe(false));

    const key = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
      .find((candidate) => candidate?.startsWith("supertale:agent-discussion:intent:"));
    expect(key).toBeTruthy();
    const persisted = JSON.parse(localStorage.getItem(key!) ?? "[]") as Array<{
      requestId: string;
      message: string;
    }>;
    expect(persisted).toContainEqual({
      requestId: newerRequestId,
      status: "submission_unknown",
      message: "新挂载的未确认请求",
    });

    second.unmount();
    const third = renderDiscussion(owner, true, client);
    await waitFor(() => expect(third.result.current.pendingRequests.some(
      (item) => item.requestId === newerRequestId,
    )).toBe(true));
    await waitFor(() => expect(third.result.current.availability?.available).toBe(true));
    await act(async () => expect(await third.result.current.send("新挂载的未确认请求")).toBe(false));
    expect(api.posts).toHaveLength(2);
  });

  it("retains an uncertain request id across refresh, another send, and remount", async () => {
    const api = setupApi();
    let postCalls = 0;
    let responseMode: "unknown" | "forbidden" | "completed" = "unknown";
    const postedBodies: Record<string, unknown>[] = [];
    server.use(
      http.post("*/api/v1/projects/:project/creation/discussions", async ({ request }) => {
        postCalls += 1;
        const body = (await request.json()) as Record<string, unknown>;
        postedBodies.push(body);
        if (responseMode === "unknown") {
          return HttpResponse.json({ ok: false, error: "temporary failure" }, { status: 503 });
        }
        if (responseMode === "forbidden") {
          api.histories.set(scopeKey(owner.project, owner.canvas, owner.conversation), [
            message(String(body.request_id), "user", String(body.message), "submission_unknown"),
          ]);
          return HttpResponse.json({ ok: false, error: "session changed" }, { status: 403 });
        }
        return HttpResponse.json({
          ok: true,
          data: {
            request_id: body.request_id,
            status: "completed",
            created_at: "2026-10-17T00:00:00Z",
            updated_at: "2026-10-17T00:00:01Z",
            message: "新问题的回复",
          },
        });
      }),
    );
    const first = renderDiscussion();
    await waitFor(() => expect(first.result.current.availability?.available).toBe(true));
    await act(async () => expect(await first.result.current.send("  未确认的问题  ")).toBe(false));
    await waitFor(() => expect(first.result.current.pendingRequest?.status).toBe("submission_unknown"));
    const requestId = first.result.current.pendingRequest?.requestId;
    expect(requestId).toEqual(expect.any(String));
    expect(postCalls).toBe(1);

    await act(async () => expect(await first.result.current.send("未确认的问题")).toBe(false));
    expect(postCalls).toBe(1);
    expect(first.result.current.pendingRequest?.requestId).toBe(requestId);
    first.unmount();

    const remounted = renderDiscussion();
    await waitFor(() => expect(remounted.result.current.pendingRequest?.requestId).toBe(requestId));
    await waitFor(() => expect(remounted.result.current.availability?.available).toBe(true));
    expect(remounted.result.current.pendingRequest?.message).toBe("未确认的问题");
    await act(async () => expect(await remounted.result.current.send("  未确认的问题  ")).toBe(false));
    expect(postCalls).toBe(1);
    expect(remounted.result.current.pendingRequest?.requestId).toBe(requestId);

    responseMode = "forbidden";
    await act(async () => expect(await remounted.result.current.send("权限变化时提交的问题")).toBe(false));
    expect(postCalls).toBe(2);
    const forbiddenRequestId = remounted.result.current.pendingRequests.find(
      (item) => item.message === "权限变化时提交的问题",
    )?.requestId;
    expect(forbiddenRequestId).toEqual(expect.any(String));
    await act(async () => expect(await remounted.result.current.send("权限变化时提交的问题")).toBe(false));
    expect(postCalls).toBe(2);

    responseMode = "completed";
    await act(async () => expect(await remounted.result.current.send("明确改写的新问题")).toBe(true));
    expect(postCalls).toBe(3);
    expect(postedBodies[0].request_id).toBe(requestId);
    expect(postedBodies[1].request_id).toBe(forbiddenRequestId);
    expect(postedBodies[2].request_id).not.toBe(requestId);
    expect(postedBodies[2].request_id).not.toBe(forbiddenRequestId);
    expect(remounted.result.current.pendingRequests).toContainEqual({
      requestId,
      status: "submission_unknown",
      message: "未确认的问题",
    });
    expect(remounted.result.current.pendingRequests).toContainEqual({
      requestId: forbiddenRequestId,
      status: "submission_unknown",
      message: "权限变化时提交的问题",
    });
  });

  it("coalesces synchronous duplicate sends into one POST", async () => {
    const api = setupApi();
    const { result } = renderDiscussion();
    await waitFor(() => expect(result.current.availability?.available).toBe(true));

    await act(async () => {
      const first = result.current.send("同一条问题");
      const second = result.current.send("同一条问题");
      expect(await second).toBe(false);
      expect(await first).toBe(true);
    });
    expect(api.posts).toHaveLength(1);
  });

  it("does not post while the discussion route is unavailable", async () => {
    const api = setupApi();
    server.use(
      http.get("*/api/v1/projects/:project/creation/discussions", () =>
        HttpResponse.json({
          ok: true,
          data: { availability: { available: false, code: "runtime_unavailable" }, messages: [] },
        })),
    );
    const { result } = renderDiscussion();
    await waitFor(() => expect(result.current.availability?.available).toBe(false));
    await act(async () => expect(await result.current.send("稍后再讨论")).toBe(false));
    expect(api.posts).toHaveLength(0);
  });

  it("does not post or become busy when pending intent storage fails", async () => {
    const api = setupApi();
    const { result } = renderDiscussion();
    await waitFor(() => expect(result.current.availability?.available).toBe(true));

    originalWindowStorage = window.localStorage;
    let intentWrites = 0;
    const failingStorage: Storage = {
      get length() { return originalWindowStorage?.length ?? 0; },
      key: (index) => originalWindowStorage?.key(index) ?? null,
      getItem: (key) => originalWindowStorage?.getItem(key) ?? null,
      setItem: (key, value) => {
        if (key.startsWith("supertale:agent-discussion:intent:")) {
          intentWrites += 1;
          throw new DOMException("Storage quota exceeded", "QuotaExceededError");
        }
        originalWindowStorage?.setItem(key, value);
      },
      removeItem: (key) => originalWindowStorage?.removeItem(key),
      clear: () => originalWindowStorage?.clear(),
    };
    vi.stubGlobal("localStorage", failingStorage);
    Object.defineProperty(window, "localStorage", {
      value: failingStorage,
      configurable: true,
      writable: true,
    });

    let sent = true;
    await act(async () => { sent = await result.current.send("本地存储不可用时保留的草稿"); });
    expect(sent).toBe(false);
    expect(intentWrites).toBe(1);
    expect(api.posts).toHaveLength(0);
    expect(result.current.busy).toBe(false);
    expect(result.current.error).toMatch(/存储|保存/);
    expect(result.current.pendingRequests).toEqual([]);
  });
});
