import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", () => ({ api: { post: mocks.post } }));

import {
  assetImportConfirmPath,
  assetImportPreviewPath,
  confirmAssetImport,
  previewAssetImport,
  assetImportDispositionKey,
  assetImportResultToastValues,
} from "@/lib/queries/asset-imports";
import { renderAssetImportValue } from "@/components/assets/asset-import-dialog";

describe("asset import paths", () => {
  beforeEach(() => mocks.post.mockReset());

  it("scopes preview and confirmation to project and asset type", () => {
    expect(assetImportPreviewPath("demo", "scene")).toBe(
      "api/v1/projects/demo/asset-imports/scene/preview",
    );
    expect(assetImportConfirmPath("demo", "scene", "import-1")).toBe(
      "api/v1/projects/demo/asset-imports/scene/import-1/confirm",
    );
  });

  it("keeps the backend skip disposition distinct", () => {
    expect(assetImportDispositionKey("skip")).toBe("skip");
  });

  it("formats the server's final confirmation counts for the success toast", () => {
    expect(assetImportResultToastValues({
      import_id: "import-1",
      created_count: 2,
      supplemented_count: 3,
      skipped_count: 4,
      warning_count: 1,
    })).toEqual({ created: 2, supplemented: 3, skipped: 4, warnings: 1 });
  });

  it("renders field values without leaking object coercion", () => {
    expect(renderAssetImportValue(null)).toBe("—");
    expect(renderAssetImportValue(0)).toBe("0");
    expect(renderAssetImportValue(false)).toBe("false");
    expect(renderAssetImportValue(["甲", "乙"])).toBe("甲、乙");
    expect(renderAssetImportValue({ role: "主角" })).toBe('{"role":"主角"}');
  });

  it("posts multipart input and parses the backend's bare preview model", async () => {
    const payload = { import_id: "import-1", asset_type: "scene", diffs: [] };
    const json = vi.fn().mockResolvedValue(payload);
    mocks.post.mockReturnValue({ json });
    const file = new File(["# 场景"], "scenes.md", { type: "text/markdown" });

    await expect(previewAssetImport("demo", "scene", file)).resolves.toBe(payload);
    const [, options] = mocks.post.mock.calls[0];
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("file")).toBe(file);
  });

  it("parses the backend's bare confirmation model", async () => {
    const payload = { import_id: "import-1", asset_type: "scene", diffs: [] };
    mocks.post.mockReturnValue({ json: vi.fn().mockResolvedValue(payload) });

    await expect(confirmAssetImport("demo", "scene", "import-1")).resolves.toBe(payload);
  });
});
