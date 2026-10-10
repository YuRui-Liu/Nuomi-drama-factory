import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
vi.mock("@/lib/queries/model-gateway", () => ({ useTaskRuntimeConfig: () => ({ data: { data: { roles: [{ id: "script_creation", route: { runtime: "codex", model: "test-model" } }] } } }) }));
import { GenerationPreflight } from "./generation-preflight";
describe("GenerationPreflight", () => {
  it("shows configured creation route without claiming unknown cost is free", () => {
    render(<GenerationPreflight project="demo" />);
    expect(screen.getByText(/test-model/)).toBeTruthy();
    expect(screen.getByText(/codex/)).toBeTruthy();
    expect(screen.getByText(/计费单位与金额暂未知/)).toBeTruthy();
  });
});
