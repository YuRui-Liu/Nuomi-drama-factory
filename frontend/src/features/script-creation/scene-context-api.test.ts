import { expect, it, vi } from "vitest";
import { apiCall } from "@/api/client";
import { sceneContextApi } from "./scene-context-api";
vi.mock("@/api/client", () => ({ apiCall: vi.fn() }));
it("reads target links and posts only explicit source association fields", async () => {
  await sceneContextApi.list("p", "target/id");
  expect(apiCall).toHaveBeenCalledWith("projects/p/script-creation/scene-context-links?target_asset_id=target%2Fid");
  const body = { source_asset_id: "source", target_asset_ids: ["target/id"], document_id: "doc", base_revision_id: "r1", client_mutation_id: "mutation" };
  await sceneContextApi.associate("p", body);
  expect(apiCall).toHaveBeenLastCalledWith("projects/p/script-creation/scene-context-links", { method: "post", retry: { limit: 0 }, json: body });
});
