// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CharacterStateVersions } from "@/components/assets/character-state-versions";

const adoptMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/queries/production-assets", () => ({
  useProductionAssetSlot: () => ({
    isLoading: false,
    data: {
      ok: true,
      data: {
        slot: {
          slot_id: "character:林默:state:linmo-duty",
          asset_kind: "character_state",
          current_version_id: "state-v1",
          version_ids: ["state-v1", "state-v2", "state-v3"],
        },
        current_version: {
          version_id: "state-v1",
          asset_path: "assets/characters/林默/identities/linmo-duty.png",
          adoption_status: "provisional",
          qc_passed: true,
          soft_issues: [],
          technical_error: null,
          generation_metadata: { panel_layout: ["front", "side", "back"] },
        },
        versions: [
          {
            version_id: "state-v1",
            asset_path: "assets/characters/林默/identities/linmo-duty.png",
            adoption_status: "provisional",
            qc_passed: true,
            soft_issues: [],
            technical_error: null,
            generation_metadata: { panel_layout: ["front", "side", "back"] },
          },
          {
            version_id: "state-v2",
            asset_path: "assets/characters/林默/identities/versions/state-v2.png",
            adoption_status: "candidate",
            qc_passed: true,
            soft_issues: ["侧面服装褶皱轻微漂移"],
            technical_error: null,
            generation_metadata: { panel_layout: ["front", "side", "back"] },
          },
          {
            version_id: "state-v3",
            asset_path: "assets/characters/林默/identities/versions/state-v3.png",
            adoption_status: "candidate",
            qc_passed: false,
            soft_issues: [],
            technical_error: "人物背面缺失",
            generation_metadata: { panel_layout: ["front", "side", "back"] },
          },
        ],
        read_only: false,
        read_only_reason: null,
      },
    },
  }),
  useAdoptProductionAssetVersion: () => ({
    mutateAsync: adoptMock,
    isPending: false,
  }),
}));

describe("CharacterStateVersions", () => {
  beforeEach(() => adoptMock.mockReset());

  it("shows front-side-back candidates and only allows QC-passed adoption", async () => {
    const user = userEvent.setup();
    render(
      <CharacterStateVersions
        project="demo"
        characterName="林默"
        identityId="linmo-duty"
        legacyAssetPath="assets/characters/林默/identities/linmo-duty.png"
      />,
    );

    expect(screen.getByText("人物状态三视图")).toBeInTheDocument();
    expect(screen.getAllByText("正面").length).toBeGreaterThan(0);
    expect(screen.getAllByText("侧面").length).toBeGreaterThan(0);
    expect(screen.getAllByText("背面").length).toBeGreaterThan(0);
    expect(screen.getByText("当前采用")).toBeInTheDocument();
    expect(screen.getByText("侧面服装褶皱轻微漂移")).toBeInTheDocument();

    const adoptButtons = screen.getAllByRole("button", { name: "采用此版本" });
    expect(adoptButtons).toHaveLength(2);
    expect(adoptButtons[0]).toBeEnabled();
    expect(adoptButtons[1]).toBeDisabled();
    await user.click(adoptButtons[0]);
    expect(adoptMock).toHaveBeenCalledWith({
      versionId: "state-v2",
      reason: "人物身份卡手动采用",
    });
  });
});
