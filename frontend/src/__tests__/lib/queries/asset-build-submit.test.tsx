// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const postMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock },
}));

import { useBuildCharacters } from "@/lib/queries/characters";
import { useBuildScenes } from "@/lib/queries/scenes";

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>
  );
}

function taskResponse(taskType: string) {
  return Promise.resolve(
    new Response(
      JSON.stringify({
        ok: true,
        task_type: taskType,
        task_id: "task-1",
        message: "queued",
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    ),
  );
}

beforeEach(() => {
  postMock.mockReset();
});

describe("asset build submission", () => {
  it.each([
    ["characters", useBuildCharacters, "build_characters"],
    ["scenes", useBuildScenes, "build_scenes"],
  ] as const)("extends the generic 30 second timeout for %s without making it infinite", async (path, useBuild, taskType) => {
    postMock.mockImplementation(() => taskResponse(taskType));
    const { result } = renderHook(() => useBuild("demo"), { wrapper });

    await result.current.mutateAsync();

    expect(postMock).toHaveBeenCalledWith(
      `api/v1/projects/demo/${path}/build`,
      expect.objectContaining({ timeout: 120_000 }),
    );
  });
});
