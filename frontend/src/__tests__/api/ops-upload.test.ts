// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.hoisted(() => vi.fn());
const json = vi.hoisted(() => vi.fn());

vi.mock("@/api/client", () => ({
  apiCall: vi.fn(),
  apiClient: request,
}));

import { uploadFreezoneImage, uploadFreezoneVideo } from "@/api/ops";

describe("freezone uploads", () => {
  beforeEach(() => {
    request.mockReset();
    json.mockReset();
    json.mockResolvedValue({
      ok: true,
      data: { url: "/static/demo/upload.png", filename: "upload.png", size: 3 },
    });
    request.mockReturnValue({ json });
  });

  it("disables the client timeout by default for image uploads", async () => {
    await uploadFreezoneImage("demo", new Blob(["abc"]), "upload.png");

    expect(request).toHaveBeenCalledOnce();
    expect(request.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      timeout: false,
    });
    expect(request.mock.calls[0]?.[1]).not.toHaveProperty("retry");
  });

  it("keeps an explicit numeric timeout override", async () => {
    await uploadFreezoneImage("demo", new Blob(["abc"]), "upload.png", {
      timeoutMs: 45_000,
    });

    expect(request.mock.calls[0]?.[1]).toMatchObject({ timeout: 45_000 });
  });

  it("uses the same unbounded default for video uploads", async () => {
    await uploadFreezoneVideo("demo", new Blob(["abc"]), "upload.mp4");

    expect(request.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      timeout: false,
    });
  });
});
