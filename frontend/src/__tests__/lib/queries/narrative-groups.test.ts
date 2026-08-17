import { describe, expect, it } from "vitest";

import {
  narrativeGroupActionPath,
  narrativeGroupActionPayload,
  narrativeGroupRevisionPath,
  narrativeGroupRollbackPath,
} from "@/lib/queries/narrative-groups";
import { queryKeys } from "@/lib/query-keys";

describe("narrative group query contract", () => {
  it("builds a scoped stage action without client credentials", () => {
    expect(narrativeGroupActionPath("demo project", 2, "ng-01", "render", "regenerate"))
      .toBe("api/v1/projects/demo%20project/episodes/2/narrative-groups/ng-01/render/regenerate");
    expect(narrativeGroupActionPayload({ revision: 3, apiKey: "must-not-leak" } as never))
      .toEqual({ revision: 3 });
  });

  it("builds revision history and rollback endpoints with canonical stages", () => {
    expect(narrativeGroupRevisionPath("demo", 2, "ng-01", "sketch"))
      .toBe("api/v1/projects/demo/episodes/2/narrative-groups/ng-01/sketch/revisions");
    expect(narrativeGroupRollbackPath("demo", 2, "ng-01", "render", 4))
      .toBe("api/v1/projects/demo/episodes/2/narrative-groups/ng-01/render/revisions/4/rollback");
  });

  it("uses a stable cache key beneath the episode", () => {
    expect(queryKeys.narrativeGroups("demo", 2)).toEqual([
      "projects", "demo", "episodes", 2, "narrative-groups",
    ]);
  });
});
