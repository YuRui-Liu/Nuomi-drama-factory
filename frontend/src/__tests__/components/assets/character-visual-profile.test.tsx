// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CharacterVisualProfile } from "@/components/assets/character-visual-profile";

const facts = [
  {
    id: "fact-1",
    field: "hair",
    value: "齐肩黑发",
    evidence: "她拨开齐肩黑发",
    sourceSpan: "E001 · 12-18 行",
    assertion: "explicit" as const,
    trust: "trusted" as const,
  },
  {
    id: "legacy-1",
    field: "face_prompt",
    value: "sharp eyes",
    evidence: "旧项目字段，无来源",
    sourceSpan: "legacy",
    assertion: "inferred" as const,
    trust: "legacy_untrusted" as const,
  },
];

const proposals = [
  {
    proposalId: "proposal-a",
    title: "冷峻纪实",
    rationale: "突出长期值夜留下的疲态。",
    recommended: true,
    identityAnchors: ["左眉断痕", "窄长眼型"],
    asymmetryDetail: "左侧嘴角略低",
    qualityIssues: [],
  },
  {
    proposalId: "proposal-b",
    title: "都市锐利",
    rationale: "强化主持人的职业压迫感。",
    recommended: false,
    identityAnchors: ["齐肩黑发"],
    asymmetryDetail: "",
    qualityIssues: ["发型与剧本证据冲突"],
  },
  {
    proposalId: "proposal-c",
    title: "克制写实",
    rationale: "保留普通人的可信度。",
    recommended: false,
    identityAnchors: ["清瘦体态"],
    asymmetryDetail: "右眼略小",
    qualityIssues: [],
  },
];

describe("CharacterVisualProfile", () => {
  it("separates narrative facts, proposal, identity and outfits without leaking legacy prompt text", () => {
    render(
      <CharacterVisualProfile
        biography="地下广播站主持人，冷静克制。"
        facts={facts}
        visualProposal="建议强化眼下疲态。"
        visualIdentity={{ face: "窄脸", hair: "齐肩黑发", body: "清瘦" }}
        outfitsAndStates={["常服：旧黑夹克"]}
        promptSnapshot="cinematic portrait, narrow face"
      />,
    );
    for (const name of ["人物小传", "剧本明确事实", "视觉提案", "视觉身份设定", "服装与状态"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("不可信旧数据")).toBeInTheDocument();
    expect(screen.queryByText("sharp eyes")).not.toBeInTheDocument();
    expect(screen.queryByText("系统编译提示词")).not.toBeInTheDocument();
  });

  it("reveals the readonly compiled prompt only from advanced diagnostics", async () => {
    const user = userEvent.setup();
    render(
      <CharacterVisualProfile
        biography="地下广播站主持人。"
        facts={facts.slice(0, 1)}
        visualProposal="建议保留写实疲态。"
        visualIdentity={{ face: "窄脸" }}
        outfitsAndStates={[]}
        promptSnapshot="cinematic portrait, narrow face"
      />,
    );
    await user.click(screen.getByRole("button", { name: "高级诊断" }));
    expect(screen.getByText("系统编译提示词")).toBeInTheDocument();
    expect(screen.getByText("cinematic portrait, narrow face")).toBeInTheDocument();
    expect(screen.getByText("只读快照，用于复盘本次生成输入。")).toBeInTheDocument();
  });

  it("renders three proposal cards with recommendation, anchors and quality differences", () => {
    render(
      <CharacterVisualProfile
        biography="地下广播站主持人。"
        facts={facts.slice(0, 1)}
        visualProposal=""
        proposals={proposals}
        selectedProposalId="proposal-b"
        visualIdentity={{}}
        outfitsAndStates={[]}
      />,
    );

    expect(screen.getAllByRole("button", { name: /冷峻纪实|都市锐利|克制写实/ })).toHaveLength(3);
    expect(screen.getByText("推荐")).toBeInTheDocument();
    expect(screen.getByText("左眉断痕、窄长眼型")).toBeInTheDocument();
    expect(screen.getByText("左侧嘴角略低")).toBeInTheDocument();
    expect(screen.getByText("发型与剧本证据冲突")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /都市锐利/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("selects a proposal and confirms a draft VisualBible", async () => {
    const user = userEvent.setup();
    const onSelectProposal = vi.fn();
    const onConfirmVisualBible = vi.fn();
    render(
      <CharacterVisualProfile
        biography="地下广播站主持人。"
        facts={[]}
        visualProposal=""
        proposals={proposals}
        selectedProposalId="proposal-a"
        onSelectProposal={onSelectProposal}
        visualBibleStatus="draft"
        onConfirmVisualBible={onConfirmVisualBible}
        visualIdentity={{ face: "窄脸" }}
        outfitsAndStates={[]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /都市锐利/ }));
    expect(onSelectProposal).toHaveBeenCalledWith("proposal-b");

    await user.click(screen.getByRole("button", { name: "确认 VisualBible" }));
    expect(onConfirmVisualBible).toHaveBeenCalledTimes(1);
  });

  it("shows a confirmed VisualBible as locked without another confirm action", () => {
    render(
      <CharacterVisualProfile
        biography="地下广播站主持人。"
        facts={[]}
        visualProposal=""
        proposals={proposals}
        selectedProposalId="proposal-a"
        visualBibleStatus="confirmed"
        visualIdentity={{ face: "窄脸" }}
        outfitsAndStates={[]}
      />,
    );

    expect(screen.getByText("VisualBible 已确认")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认 VisualBible" })).not.toBeInTheDocument();
  });
});
