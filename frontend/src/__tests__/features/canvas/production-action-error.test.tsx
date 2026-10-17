import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { productionActionErrorFallback } from "@/features/canvas/production/productionActionError";
import { useProductionAction } from "@/features/canvas/production/useProductionAction";

function backendFailure(code: string, message?: string) {
  return Object.assign(
    new Error("HTTPError: Request failed with status code 409 for https://api.example.test/prepare"),
    {
      status: 409,
      body: { detail: { code, ...(message ? { message } : {}) } },
    },
  );
}

describe("useProductionAction error presentation", () => {
  it("explains when the handoff preview became stale", async () => {
    const { result } = renderHook(() => useProductionAction());

    await act(async () => {
      await result.current.perform("screenplay-handoff", async () => {
        throw backendFailure("DIRECTOR_HANDOFF_PREVIEW_STALE");
      });
    });

    expect(result.current.error).toBe("剧本来源已在预览后更新，请重新打开交接预览并确认后重试。");
  });

  it("reads a known handoff code from a raw ky response body", async () => {
    const { result } = renderHook(() => useProductionAction());
    const response = new Response(JSON.stringify({ detail: { code: "ADOPTED_SCRIPT_REVISION_REQUIRED" } }), {
      status: 409,
      headers: { "content-type": "application/json" },
    });

    await act(async () => {
      await result.current.perform("screenplay-handoff", async () => {
        throw Object.assign(new Error(`HTTPError: Request failed with status code 409 for ${response.url || "https://api.example.test/prepare"}`), {
          name: "HTTPError",
          response,
        });
      });
    });

    expect(result.current.error).toBe("当前剧本版本尚未采用或已变化，请先采用最新保存版本，再重新预览交接。");
  });

  it("explains why a consistency review is required before handoff", async () => {
    const { result } = renderHook(() => useProductionAction());

    await act(async () => {
      await result.current.perform("screenplay-handoff", async () => {
        throw backendFailure("DIRECTOR_WORKSPACE_NEEDS_REVIEW");
      });
    });

    expect(result.current.error).toBe("提交导演前需要完成当前版本的一致性检查，并处理未确认的事实问题。");
  });

  it("translates a known handoff conflict reason", async () => {
    const { result } = renderHook(() => useProductionAction());

    await act(async () => {
      await result.current.perform("screenplay-handoff", async () => {
        throw backendFailure("DIRECTOR_WORKSPACE_CONFLICT", "script revision changed");
      });
    });

    expect(result.current.error).toBe("剧本版本已变化，请重新打开交接预览并确认后重试。");
  });

  it("uses a friendly endpoint-free message for an unknown HTTP failure", async () => {
    const { result } = renderHook(() => useProductionAction());

    await act(async () => {
      await result.current.perform("screenplay-handoff", async () => {
        throw Object.assign(new Error("HTTPError: Request failed with status code 409 for https://api.example.test/private"), {
          status: 409,
        });
      });
    });

    expect(result.current.error).toBe("当前操作存在冲突，输入已保留。请刷新相关状态后重试。");
    expect(result.current.error).not.toContain("https://");
  });

  it("provides an endpoint-free synchronous fallback for raw ky query errors", () => {
    const error = new Error("HTTPError: Request failed with status code 409 for https://api.example.test/private");

    expect(productionActionErrorFallback(error)).toBe("当前操作存在冲突，输入已保留。请刷新相关状态后重试。");
  });

  it("translates a strict-review backend reason even when no response body is attached", async () => {
    const { result } = renderHook(() => useProductionAction());

    await act(async () => {
      await result.current.perform("screenplay-handoff", async () => {
        throw new Error("a current completed actual consistency check is required");
      });
    });

    expect(result.current.error).toBe("提交导演前需要完成当前版本的一致性检查。");
  });

  it("preserves deliberate local validation messages and the retry mutation identity", async () => {
    const { result } = renderHook(() => useProductionAction());
    const mutationIds: string[] = [];
    const action = vi.fn(async (mutation: string) => {
      mutationIds.push(mutation);
      if (mutationIds.length === 1) throw new Error("请先保存剧本再提交");
    });

    await act(async () => {
      await result.current.perform("screenplay-handoff", action);
    });
    expect(action).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBe("请先保存剧本再提交");

    await act(async () => {
      await result.current.perform("screenplay-handoff", action);
    });

    expect(action).toHaveBeenCalledTimes(2);
    expect(mutationIds[1]).toBe(mutationIds[0]);
    expect(result.current.error).toBe("");
  });
});
