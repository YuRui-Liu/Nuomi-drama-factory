// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ViewportLazyImage } from "@/components/viewport-lazy-image";

describe("ViewportLazyImage", () => {
  let callback!: IntersectionObserverCallback;
  let options: IntersectionObserverInit | undefined;
  const observe = vi.fn();
  const disconnect = vi.fn();

  beforeEach(() => {
    observe.mockClear();
    disconnect.mockClear();
    class MockIntersectionObserver {
      root = null;
      rootMargin = "320px";
      thresholds = [0.01];
      observe = observe;
      disconnect = disconnect;
      unobserve = vi.fn();
      takeRecords = vi.fn(() => []);

      constructor(
        nextCallback: IntersectionObserverCallback,
        nextOptions?: IntersectionObserverInit,
      ) {
        callback = nextCallback;
        options = nextOptions;
      }
    }
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("keeps src off the DOM until entering the 320px preload boundary", () => {
    render(<ViewportLazyImage src="/thumb.webp" alt="林昭" />);

    const image = screen.getByRole("img", { name: "林昭" });
    expect(image).not.toHaveAttribute("src");
    expect(observe).toHaveBeenCalledWith(image);
    expect(options).toEqual({
      root: null,
      rootMargin: "320px",
      threshold: 0.01,
    });

    act(() => {
      callback(
        [{ isIntersecting: true, intersectionRatio: 0.01 } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    });

    expect(image).toHaveAttribute("src", "/thumb.webp");
    expect(disconnect).toHaveBeenCalled();

    act(() => {
      callback(
        [{ isIntersecting: false, intersectionRatio: 0 } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    });
    expect(image).toHaveAttribute("src", "/thumb.webp");
  });

  it("loads eager images immediately without creating an observer", () => {
    render(
      <ViewportLazyImage src="/first-thumb.webp" alt="首图" loading="eager" />,
    );

    expect(screen.getByRole("img", { name: "首图" })).toHaveAttribute(
      "src",
      "/first-thumb.webp",
    );
    expect(observe).not.toHaveBeenCalled();
  });

  it("falls back to native lazy loading when observers are unavailable", () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    render(<ViewportLazyImage src="/thumb.webp" alt="林昭" />);
    expect(screen.getByRole("img", { name: "林昭" })).toHaveAttribute(
      "src",
      "/thumb.webp",
    );
  });
});
