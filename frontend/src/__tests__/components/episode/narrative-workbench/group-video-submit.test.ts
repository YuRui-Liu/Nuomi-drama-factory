import { describe, expect, it } from "vitest";

import { groupVideoFailures } from "@/components/episode/narrative-workbench/group-video-results";
import { narrativeGroupVideoPayload } from "@/lib/queries/narrative-groups";

describe("group video submission results", () => {
  it("treats HTTP 200 ok:false as a failed Beat while retaining successful tasks", () => {
    const failures = groupVideoFailures([11, 12], [
      { status: "fulfilled", value: { ok: true } },
      { status: "fulfilled", value: { ok: false, error: "missing first frame" } },
    ]);

    expect(failures).toEqual([{ beatNum: 12, error: "missing first frame" }]);
  });
});

describe("group video reference revision payload", () => {
  it("sends saved reference revision only when provided", () => {
    const base = { model: "runninghub:minimax-h3-ref", mode: "auto" as const, revision: 2, aspectRatio: "16:9" as const };
    expect(narrativeGroupVideoPayload({ ...base, referenceRevision: 7 })).toMatchObject({ reference_revision: 7 });
    expect(narrativeGroupVideoPayload({ ...base, model: "runninghub:minimax-h3" })).not.toHaveProperty("reference_revision");
  });
});
