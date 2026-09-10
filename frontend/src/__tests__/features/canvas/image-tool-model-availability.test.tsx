// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { imageModelState } = vi.hoisted(() => ({
  imageModelState: {
    models: [] as Array<Record<string, unknown>>,
    isLoading: false,
    isFallback: false,
    error: null as Error | null,
  },
}));

vi.mock("@/features/canvas/hooks/useFreezoneImageModels", () => ({
  useFreezoneImageModels: () => imageModelState,
}));

vi.mock("@/lib/queries/generation-credit-cost", () => ({
  useGenerationCreditCost: () => ({ data: undefined }),
}));

import { LightEditorPanel } from "@/features/canvas/ui/LightEditorPanel";
import { MultiAngleEditorPanel } from "@/features/canvas/ui/MultiAngleEditorPanel";

describe("image tool model availability", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    imageModelState.models = [];
    imageModelState.isLoading = false;
    imageModelState.error = null;
  });

  it("shows a disabled loading state while LightEditorPanel waits for models", () => {
    imageModelState.isLoading = true;

    render(
      <LightEditorPanel
        imageSource="https://example.test/source.png"
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const submit = screen.getByRole("button", {
      name: "characters.imageSource.loading",
    });
    expect(submit).toBeDisabled();
    expect(submit).toHaveClass("cursor-not-allowed");
  });

  it("shows a disabled unavailable state when MultiAngleEditorPanel model loading fails", () => {
    imageModelState.error = new Error("catalog offline");

    render(
      <MultiAngleEditorPanel
        imageSource="https://example.test/source.png"
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const submit = screen.getByRole("button", {
      name: "characters.imageSource.unavailable",
    });
    expect(submit).toBeDisabled();
    expect(submit).toHaveClass("cursor-not-allowed");
  });
});
