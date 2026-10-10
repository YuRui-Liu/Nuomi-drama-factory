import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CreativeReviewPanel } from "@/components/episode/director-review/creative-review-panel";
import { ActivationBar } from "@/components/episode/director-review/activation-bar";
import type { DirectorPlanRevision } from "@/lib/queries/director-plans";

const revision = {
  revision_id: "r2", status: "review_required", creative_contract_version: 1,
  creative_review_status: "needs_revision", validation_report: { passed: true },
  groups: [{ id: "g", shots: [{ id: "s1", creative: {
    optics: { purpose: "让观众辨认门口的人", focal_length_mm: 35 },
    performance: { purpose: "先听到坏消息再停笔" },
  } }] }],
  creative_feedback: {
    issues: [{ issue: { issue_id: "i1", description: "反打视线方向错误", acceptance_criteria: "双方目光对应" }, status: "persists" }],
    author_rounds: [{ round_id: "a1", responses: [{ issue_id: "i1", status: "proposed_change", explanation: "把反打视线改向门口" }] }],
    qc_rounds: [{ round_id: "q2", dispositions: [{ issue_id: "i1", status: "persists", explanation: "人物朝向仍与对手位置不符" }] }],
  },
} as unknown as DirectorPlanRevision;

describe("导演创作复查", () => {
  it("separates author changes from subsequent QC verdicts", () => {
    render(<CreativeReviewPanel revision={revision} />);
    expect(screen.getByText("焦段与焦点")).toBeInTheDocument();
    expect(screen.getByText("让观众辨认门口的人")).toBeInTheDocument();
    expect(screen.getByText("把反打视线改向门口")).toBeInTheDocument();
    expect(screen.getByText("人物朝向仍与对手位置不符")).toBeInTheDocument();
  });
  it("blocks activation when structure passes but independent QC does not", () => {
    render(<ActivationBar revision={revision} revisions={[revision]} onActivate={vi.fn()} onAbandon={vi.fn()} />);
    expect(screen.getByRole("button", { name: "激活此版本" })).toBeDisabled();
  });
  it("labels legacy plans without inventing a creative pass", () => {
    render(<CreativeReviewPanel revision={{ ...revision, creative_contract_version: undefined }} />);
    expect(screen.getByText("旧版本：尚未按创作标准审查")).toBeInTheDocument();
  });
});
