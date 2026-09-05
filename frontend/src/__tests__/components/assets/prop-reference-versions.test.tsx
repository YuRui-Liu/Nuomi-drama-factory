// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PropReferenceVersions } from "@/components/assets/prop-reference-versions";

const mocks = vi.hoisted(() => ({ slot: vi.fn(), adopt: vi.fn() }));

vi.mock("@/lib/queries/production-assets", () => ({
  useProductionAssetSlot: (...args: unknown[]) => {
    mocks.slot(...args);
    return {
      isLoading: false,
      data: {
        ok: true,
        data: {
          slot: { current_version_id: "prop-v1" },
          versions: [
            { version_id: "prop-v1", asset_path: "assets/props/钥匙/v1.png", adoption_status: "provisional", qc_passed: true, soft_issues: [], technical_error: null, generation_metadata: { panel_layout: ["front", "side", "back"] } },
            { version_id: "prop-v2", asset_path: "assets/props/钥匙/v2.png", adoption_status: "candidate", qc_passed: true, soft_issues: ["边缘轻微漂移"], technical_error: null, generation_metadata: { panel_layout: ["front", "side", "back"] } },
            { version_id: "prop-v3", asset_path: "assets/props/钥匙/v3.png", adoption_status: "candidate", qc_passed: false, soft_issues: [], technical_error: "背面缺失", generation_metadata: { panel_layout: ["front", "side", "back"] } },
          ],
          read_only: false,
          read_only_reason: null,
        },
      },
    };
  },
  useAdoptProductionAssetVersion: () => ({ mutateAsync: mocks.adopt, isPending: false }),
}));

describe("PropReferenceVersions", () => {
  beforeEach(() => {
    mocks.slot.mockClear();
    mocks.adopt.mockReset();
  });

  it("uses the prop slot and only adopts QC-passed candidates", async () => {
    const user = userEvent.setup();
    render(<PropReferenceVersions project="demo" propName="钥匙" legacyAssetPath="assets/props/钥匙/reference.png" />);

    expect(mocks.slot).toHaveBeenCalledWith("demo", "prop:钥匙:reference", "prop_reference", "assets/props/钥匙/reference.png");
    expect(screen.getByText("道具参考图版本")).toBeInTheDocument();
    expect(screen.getAllByText("正面").length).toBeGreaterThan(0);
    expect(screen.getByText("当前采用")).toBeInTheDocument();
    expect(screen.getByText("背面缺失")).toBeInTheDocument();

    const buttons = screen.getAllByRole("button", { name: "采用此版本" });
    expect(buttons[0]).toBeEnabled();
    expect(buttons[1]).toBeDisabled();
    await user.click(buttons[0]);
    expect(mocks.adopt).toHaveBeenCalledWith({ versionId: "prop-v2", reason: "道具参考图手动采用" });
  });
});
