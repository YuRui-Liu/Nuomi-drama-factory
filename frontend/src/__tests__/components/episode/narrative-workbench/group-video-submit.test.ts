import { describe, expect, it } from "vitest";

import { groupVideoFailures } from "@/components/episode/narrative-workbench/group-video-results";

describe("group video submission results", () => {
  it("treats HTTP 200 ok:false as a failed Beat while retaining successful tasks", () => {
    const failures = groupVideoFailures([11, 12], [
      { status: "fulfilled", value: { ok: true } },
      { status: "fulfilled", value: { ok: false, error: "missing first frame" } },
    ]);

    expect(failures).toEqual([{ beatNum: 12, error: "missing first frame" }]);
  });
});
