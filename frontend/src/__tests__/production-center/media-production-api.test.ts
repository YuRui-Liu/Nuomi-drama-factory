// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ apiCall: vi.fn() }));

vi.mock("@/api/client", () => ({ apiCall: mocks.apiCall }));

import {
  createProductionRun,
  previewProductionRun,
  type ProductionRunDraft,
} from "@/api/media-production";

const draft: ProductionRunDraft = {
  node_types: {
    image: {
      capability: "image.single",
      implementation: "image-primary",
      workflow_version: { id: "image", version: 1 },
      unit_cost: 0.5,
    },
  },
  scope: {
    audit_policy: "balanced",
    nodes: [{ id: "cover", node_type: "image" }],
  },
  snapshot: { budget: 5 },
  existing_artifacts: {},
  high_cost: false,
};

describe("media production API", () => {
  beforeEach(() => mocks.apiCall.mockReset());

  it("previews a draft without sending a confirmation token", async () => {
    mocks.apiCall.mockResolvedValue({
      audit_policy: "balanced",
      nodes: [],
      counts: { create: 1, reuse: 0, invalidate: 0, skip: 0 },
      estimated_cost: 0.5,
      snapshot_token: "preview-token",
    });

    await previewProductionRun("project/demo", draft);

    expect(mocks.apiCall).toHaveBeenCalledWith(
      "projects/project%2Fdemo/production/runs/preview",
      { method: "post", json: draft },
    );
    expect(draft).not.toHaveProperty("snapshot_token");
  });

  it("starts only from a preview confirmation and sends its snapshot token", async () => {
    mocks.apiCall.mockResolvedValue({ run: { id: "run-1" }, nodes: [], edges: [] });

    await createProductionRun("demo", {
      draft,
      snapshotToken: "preview-token",
    });

    expect(mocks.apiCall).toHaveBeenCalledWith(
      "projects/demo/production/runs",
      {
        method: "post",
        json: { ...draft, snapshot_token: "preview-token" },
      },
    );
  });

  it("rejects starting without a preview snapshot token", async () => {
    await expect(
      createProductionRun("demo", { draft, snapshotToken: "  " }),
    ).rejects.toThrow("preview snapshot token");
    expect(mocks.apiCall).not.toHaveBeenCalled();
  });
});
