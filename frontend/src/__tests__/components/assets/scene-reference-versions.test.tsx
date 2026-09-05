// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SceneReferenceVersions } from "@/components/assets/scene-reference-versions";

const mocks = vi.hoisted(() => ({ slot: vi.fn(), adopt: vi.fn() }));

vi.mock("@/lib/queries/production-assets", () => ({
  useProductionAssetSlot: (...args: unknown[]) => {
    mocks.slot(...args);
    return {
      isLoading: false,
      data: {
        ok: true,
        data: {
          slot: { current_version_id: "scene-v1" },
          versions: [
            { version_id: "scene-v1", asset_path: "assets/scenes/客厅/v1.png", adoption_status: "provisional", qc_passed: true, soft_issues: [], technical_error: null, generation_metadata: { anchor_view: "front" } },
            { version_id: "scene-v2", asset_path: "assets/scenes/客厅/v2.png", adoption_status: "candidate", qc_passed: true, soft_issues: [], technical_error: null, generation_metadata: { anchor_view: "reverse" } },
            { version_id: "scene-v3", asset_path: "assets/scenes/客厅/v3.png", adoption_status: "candidate", qc_passed: false, soft_issues: [], technical_error: "空间锚点漂移", generation_metadata: {} },
          ],
          read_only: false,
          read_only_reason: null,
        },
      },
    };
  },
  useAdoptProductionAssetVersion: () => ({ mutateAsync: mocks.adopt, isPending: false }),
}));

describe("SceneReferenceVersions", () => {
  beforeEach(() => {
    mocks.slot.mockClear();
    mocks.adopt.mockReset();
  });

  it("builds state slots and adopts a QC-passed candidate", async () => {
    const user = userEvent.setup();
    render(<SceneReferenceVersions project="demo" sceneName="客厅-夜" baseSceneId="客厅" kind="master" legacyAssetPath="assets/scenes/客厅-夜/master.png" />);

    expect(mocks.slot).toHaveBeenCalledWith("demo", "scene:客厅:state:客厅-夜:master", "scene_state", "assets/scenes/客厅-夜/master.png");
    expect(screen.getByText("主参考图版本")).toBeInTheDocument();
    expect(screen.getAllByText("状态资产").length).toBeGreaterThan(0);
    expect(screen.getByText("QC 未通过")).toBeInTheDocument();

    const buttons = screen.getAllByRole("button", { name: "采用此版本" });
    expect(buttons[0]).toBeEnabled();
    expect(buttons[1]).toBeDisabled();
    await user.click(buttons[0]);
    expect(mocks.adopt).toHaveBeenCalledWith({ versionId: "scene-v2", reason: "场景主参考图手动采用" });
  });

  it("builds a base-scene slot when no base id exists", () => {
    render(<SceneReferenceVersions project="demo" sceneName="客厅" kind="spatial_layout" />);
    expect(mocks.slot).toHaveBeenCalledWith("demo", "scene:客厅:base:spatial_layout", "scene_base", undefined);
    expect(screen.getByText("空间布局图版本")).toBeInTheDocument();
  });
});
