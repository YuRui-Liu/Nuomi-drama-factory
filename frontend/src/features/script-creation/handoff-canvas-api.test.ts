import { expect, it, vi } from "vitest";
import { apiCall } from "@/api/client";
import { scriptCreationApi } from "./api";

vi.mock("@/api/client", () => ({ apiCall: vi.fn() }));

it("opens the confirmed handoff canvas without sending client content", async () => {
  const result = { episode_id: "ep-1", canvas_id: "canvas-1", status: "applied", operation_id: "op-1" };
  vi.mocked(apiCall).mockResolvedValue(result as never);

  await expect(scriptCreationApi.openHandoffCanvas("demo/project", "handoff/1")).resolves.toEqual(result);
  expect(apiCall).toHaveBeenCalledWith(
    "projects/demo%2Fproject/script-creation/handoffs/handoff%2F1/canvas",
    { method: "post", retry: { limit: 0 } },
  );
});
