import { describe, expect, it, vi } from "vitest";
import { apiCall } from "@/api/client";
import { scriptCreationApi } from "./api";
vi.mock("@/api/client", () => ({ apiCall: vi.fn().mockResolvedValue({}) }));
describe("asset extraction API contract", () => {
  it("posts free revalidation without a request body or generation endpoint", async () => {
    await scriptCreationApi.revalidateAssetExtraction("demo", "run");
    expect(apiCall).toHaveBeenLastCalledWith("projects/demo/script-creation/asset-extractions/run/revalidate", { method: "post", retry: { limit: 0 } });
  });
  it.each(["character", "scene", "prop"] as const)("includes %s in generic extraction and reads exact document history", async (assetType) => {
    const body = { document_id: "doc", base_revision_id: "rev", client_mutation_id: "mutation" };
    await scriptCreationApi.startAssetExtraction("demo", assetType, body);
    expect(apiCall).toHaveBeenLastCalledWith("projects/demo/script-creation/asset-extractions", expect.objectContaining({ method: "post", json: { ...body, asset_type: assetType }, retry: { limit: 0 } }));
    await scriptCreationApi.listAssetExtractions("demo", assetType, "doc");
    expect(apiCall).toHaveBeenLastCalledWith(`projects/demo/script-creation/asset-extractions?asset_type=${assetType}&document_id=doc`);
    await scriptCreationApi.confirmAssetExtraction("demo", assetType, "run", { base_revision_id: "rev", candidate_ids: ["one"], client_mutation_id: "confirm" });
    expect(apiCall).toHaveBeenLastCalledWith("projects/demo/script-creation/asset-extractions/run/confirm", expect.objectContaining({ json: { base_revision_id: "rev", candidate_ids: ["one"], client_mutation_id: "confirm" } }));
  });
});
