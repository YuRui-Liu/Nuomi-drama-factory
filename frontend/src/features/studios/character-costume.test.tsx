import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CharacterCostume } from "./character-costume";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
vi.mock("@/lib/queries/characters", () => ({
  useCharacterIdentities: () => ({ data: { data: [] } }),
  useCreateIdentity: () => ({ mutateAsync: vi.fn() }),
  useGenerateIdentityImageAsync: () => ({ mutateAsync: vi.fn() }),
  useUploadCostumeImage: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("@/lib/queries/tasks", () => ({ useTasks: () => ({ data: { data: [] } }) }));
vi.mock("@/components/assets/character-state-versions", () => ({ CharacterStateVersions: () => <div>版本</div> }));
it("requires a saved costume identity before generation", () => {
  render(<QueryClientProvider client={new QueryClient()}><CharacterCostume project="p" name="角色" /></QueryClientProvider>);
  expect(screen.getByRole("button", { name: "生成服装造型候选" })).toBeDisabled();
  expect(screen.getByText(/基础人物身份保持不变/)).toBeInTheDocument();
});
