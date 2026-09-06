// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useGithubStars } from "@/hooks/use-github-stars";

const STORAGE_KEY = "dramaclaw.login.githubStars";
const REPO = "YuRui-Liu/Nuomi-drama-factory";

function mockFetchOk(count: number) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ stargazers_count: count }),
  });
}

describe("useGithubStars", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("ignores a legacy unscoped numeric cache when the request is rate-limited", async () => {
    window.localStorage.setItem(STORAGE_KEY, "1234");
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 403 });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useGithubStars(REPO));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });

  it("updates the value and persists it to localStorage on a successful fetch", async () => {
    const fetchMock = mockFetchOk(1500);
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useGithubStars(REPO));

    await waitFor(() => expect(result.current).toBe(1500));
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.github.com/repos/YuRui-Liu/Nuomi-drama-factory",
      expect.objectContaining({
        headers: { Accept: "application/vnd.github+json" },
      }),
    );
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null")).toEqual({
      repo: REPO,
      count: 1500,
    });
  });

  it("keeps a matching repository cache when the request is rate-limited", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ repo: REPO, count: 1234 }));
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 403, json: () => Promise.resolve(null) });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useGithubStars(REPO));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current).toBe(1234);
  });

  it("ignores a cache scoped to a different repository", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ repo: "another-owner/another-repo", count: 9876 }),
    );
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useGithubStars(REPO));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });

  it.each([-1, "1234", null])("ignores an invalid repository-scoped count: %s", async (count) => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ repo: REPO, count }));
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useGithubStars(REPO));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });

  it("does not keep repo A stars after rerendering for repo B", async () => {
    const repoA = "owner-a/repo-a";
    const repoB = "owner-b/repo-b";
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ repo: repoA, count: 42 }));
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(
      ({ repo }) => useGithubStars(repo),
      { initialProps: { repo: repoA } },
    );
    expect(result.current).toBe(42);

    rerender({ repo: repoB });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(result.current).toBeNull();
  });

  it.each([-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY])(
    "does not display or cache an invalid API count: %s",
    async (count) => {
      vi.stubGlobal("fetch", mockFetchOk(count));

      const { result } = renderHook(() => useGithubStars(REPO));

      await waitFor(() => expect(fetch).toHaveBeenCalled());
      expect(result.current).toBeNull();
      expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    },
  );

  it("stays null and does not throw when the fetch rejects with no stored value", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useGithubStars(REPO));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
