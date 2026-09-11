// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { renderHook, waitFor } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const { fetchFreezoneImageModels } = vi.hoisted(() => ({
  fetchFreezoneImageModels: vi.fn(),
}));

vi.mock("@/api/ops", () => ({
  fetchFreezoneImageModels,
}));

import { useFreezoneImageModels } from "@/features/canvas/hooks/useFreezoneImageModels";

describe("useFreezoneImageModels", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeAll(() => {
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterAll(() => {
    warnSpy.mockRestore();
  });

  beforeEach(() => {
    fetchFreezoneImageModels.mockReset();
  });

  it("does not expose hardcoded models while a project catalog is loading", () => {
    fetchFreezoneImageModels.mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() =>
      useFreezoneImageModels("loading-project"),
    );

    expect(result.current.models).toEqual([]);
    expect(result.current.isLoading).toBe(true);
  });

  it("keeps a successful empty project catalog empty", async () => {
    fetchFreezoneImageModels.mockResolvedValue([]);

    const { result } = renderHook(() =>
      useFreezoneImageModels("empty-project"),
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.models).toEqual([]);
    expect(result.current.isFallback).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("keeps the catalog empty when loading fails", async () => {
    fetchFreezoneImageModels.mockRejectedValue(new Error("catalog offline"));

    const { result } = renderHook(() =>
      useFreezoneImageModels("failed-project"),
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.models).toEqual([]);
    expect(result.current.isFallback).toBe(false);
    expect(result.current.error?.message).toBe("catalog offline");
  });
});
