import { describe, expect, it } from "vitest";
import { versionNumbers, unresolvedIssues, reviewStatusLabel, assetKindLabel } from "@/components/episode/director-review/review-presentation";

describe("director review presentation", () => {
  it("numbers by creation time and ID independently of response order", () => {
    const revisions = [{ revision_id: "z", created_at: "2026-01-02" }, { revision_id: "b", created_at: "2026-01-01" }, { revision_id: "a", created_at: "2026-01-01" }];
    expect(versionNumbers(revisions)).toEqual({ a: 1, b: 2, z: 3 });
    expect(revisions[0].revision_id).toBe("z");
  });
  it("keeps disputed and unknown issues unresolved and missing feedback unknown", () => {
    expect(unresolvedIssues(undefined)).toBeUndefined();
    expect(unresolvedIssues({ issues: [{ status: "resolved" }, { status: "disputed" }, { status: "unknown" }] })).toHaveLength(2);
    expect(reviewStatusLabel(undefined)).toBe("等待独立 QC");
    expect(assetKindLabel("character_identity")).toBe("人物形象");
    expect(assetKindLabel("future_kind")).toBe("future_kind");
  });
});
