// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CharacterStateVersions } from "@/components/assets/character-state-versions";
import enTranslation from "../../../../public/locales/en/translation.json";
import zhTranslation from "../../../../public/locales/zh/translation.json";

const adoptMock = vi.hoisted(() => vi.fn());
const fixtureState = vi.hoisted(() => ({ currentVersionId: "state-v1" }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number; defaultValue?: string }) => {
      const translations: Record<string, string> = {
        "characters.stateVersions.title": "人物状态三视图",
        "characters.stateVersions.description": "正面、侧面、背面保持同一人物与服装；生成后先作为候选，可手动采用。",
        "characters.stateVersions.v2Title": "人物 Identity Sheet v2",
        "characters.stateVersions.v2Description": "3/4 脸部母版锁定身份；无面部正面全身保留完整头身并表达体型与服装正面；背面全身表达背部轮廓与服装结构。",
        "characters.stateVersions.isolationHint": "正面全身保留完整头部轮廓，仅隔离可识别五官，并非裁切或图片缺损。",
        "characters.stateVersions.issueSeparator": "；",
        "characters.stateVersions.status.current": "当前采用",
        "characters.stateVersions.status.qcFailed": "QC 未通过",
        "characters.stateVersions.status.candidate": "候选版本",
        "characters.stateVersions.status.superseded": "历史版本",
        "characters.stateVersions.panels.front": "正面",
        "characters.stateVersions.panels.side": "侧面",
        "characters.stateVersions.panels.back": "背面",
        "characters.stateVersions.panels.portrait": "3/4脸部母版",
        "characters.stateVersions.panels.headlessFront": "无面部正面全身",
        "characters.stateVersions.panels.fullBack": "背面全身",
        "characters.stateVersions.qcIssues.portrait_too_small": "肖像区域过小",
        "characters.stateVersions.qcIssues.front_face_detected": "正面身体仍检测到脸部",
        "characters.stateVersions.qcIssues.back_face_visible": "背面人物发生回头",
        "characters.stateVersions.qcIssues.state_inconsistent": "正背面角色状态不一致",
        "characters.stateVersions.qcIssues.non_neutral_presentation": "背景或光线不是中性展示",
        "characters.stateVersions.qcIssues.style_mismatch": "风格与项目设置不一致",
        "characters.stateVersions.qcIssues.dead_eyes": "眼神缺乏生命感",
        "characters.stateVersions.qcIssues.unnatural_skin_texture": "皮肤纹理不自然",
        "characters.stateVersions.qcIssues.plastic_material": "材质呈现塑料感",
        "characters.stateVersions.qcIssues.qc_unavailable": "QC 暂不可用",
        "characters.stateVersions.adopt": "采用此版本",
        "characters.stateVersions.adoptReason": "人物身份卡手动采用",
      };
      if (key === "characters.stateVersions.versionCount") return `${options?.count ?? 0} 个版本`;
      return translations[key] ?? options?.defaultValue ?? key;
    },
  }),
}));

vi.mock("@/lib/queries/production-assets", () => ({
  useProductionAssetSlot: () => ({
    isLoading: false,
    data: {
      ok: true,
      data: {
        slot: {
          slot_id: "character:林默:state:linmo-duty",
          asset_kind: "character_state",
          current_version_id: fixtureState.currentVersionId,
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
            soft_issues: ["portrait_too_small", "front_face_detected"],
            technical_error: null,
            generation_metadata: {
              layout_version: "identity_sheet_v2",
              quality_report: {
                passed: false,
                checks: {},
                issues: [
                  "back_face_visible",
                  "state_inconsistent",
                  "non_neutral_presentation",
                  "style_mismatch",
                  "dead_eyes",
                  "unnatural_skin_texture",
                  "plastic_material",
                  "qc_unavailable",
                ],
                style_family: "2.5d",
              },
            },
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
  beforeEach(() => {
    adoptMock.mockReset();
    fixtureState.currentVersionId = "state-v1";
  });

  it("describes the complete faceless front body in both locales", () => {
    expect(zhTranslation.characters.stateVersions.panels.headlessFront).toBe("无面部正面全身");
    expect(zhTranslation.characters.stateVersions.isolationHint).toContain("保留完整头部轮廓");
    expect(enTranslation.characters.stateVersions.panels.headlessFront).toBe("Faceless front full body");
    expect(enTranslation.characters.stateVersions.isolationHint).toContain("complete head outline");
  });

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

  it("shows the v2 identity-isolation layout and translated QC reasons", () => {
    fixtureState.currentVersionId = "state-v3";
    render(
      <CharacterStateVersions
        project="demo"
        characterName="林默"
        identityId="linmo-duty"
      />,
    );

    expect(screen.getByText("3/4脸部母版")).toBeInTheDocument();
    expect(screen.getByText("人物 Identity Sheet v2")).toBeInTheDocument();
    expect(
      screen.getByText("3/4 脸部母版锁定身份；无面部正面全身保留完整头身并表达体型与服装正面；背面全身表达背部轮廓与服装结构。"),
    ).toBeInTheDocument();
    expect(screen.getByText("无面部正面全身")).toBeInTheDocument();
    expect(screen.getByText("背面全身")).toBeInTheDocument();
    expect(
      screen.getByText("正面全身保留完整头部轮廓，仅隔离可识别五官，并非裁切或图片缺损。"),
    ).toBeInTheDocument();

    for (const issue of [
      "肖像区域过小",
      "正面身体仍检测到脸部",
      "背面人物发生回头",
      "正背面角色状态不一致",
      "背景或光线不是中性展示",
      "风格与项目设置不一致",
      "眼神缺乏生命感",
      "皮肤纹理不自然",
      "材质呈现塑料感",
      "QC 暂不可用",
    ]) {
      expect(screen.getByText(new RegExp(issue))).toBeInTheDocument();
    }
  });
});
