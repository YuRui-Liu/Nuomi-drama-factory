import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

const mutateAsync = vi.fn().mockResolvedValue({ ok: true });
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/queries/model-gateway", () => ({
  useTextRuntimeConfig: () => ({ data: { data: { source: "default", provider: "deepseek", baseUrl: "https://api.deepseek.com", model: "deepseek-v4-flash", configured: false, apiKeyConfigured: false, apiKeyPreview: "" } }, isLoading: false }),
  useSaveTextRuntimeConfig: () => ({ mutateAsync, isPending: false }),
  useTaskRuntimeConfig: () => ({ data: { data: { roles: [{ id: "screenplay_semantic_repair", label: "导演拆解修复", route: { runtime: "codex", model: "gpt-5.6-sol", reasoning_effort: "medium", skill_id: null, skill_version: null, fallback: "stop" } }] } }, isLoading: false }),
}));

import { TextRuntimePanel } from "@/components/settings/text-runtime-panel";

it("uses provider presets without overwriting a manually edited base URL", async () => {
  const user = userEvent.setup();
  render(<TextRuntimePanel />);
  await waitFor(() => expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.deepseek.com"));
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://custom.example/v1" } });
  await user.click(screen.getByRole("combobox", { name: "供应商" }));
  expect(screen.queryByText("DramaClawAPI")).not.toBeInTheDocument();
  await user.click(screen.getByText("NuomiDrama API"));
  expect(screen.getByLabelText("Base URL")).toHaveValue("https://custom.example/v1");
  expect(screen.getByLabelText("模型")).toHaveValue("DC-scene-builder-LLM");
  fireEvent.click(screen.getByRole("button", { name: "保存普通文本模型" }));
  await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({ provider: "dramaclaw", baseUrl: "https://custom.example/v1", model: "DC-scene-builder-LLM" }));
});

it("shows the dedicated director semantic repair task route", () => {
  render(<TextRuntimePanel />);
  expect(screen.getByText("导演拆解修复")).toBeInTheDocument();
  expect(screen.getByText("screenplay_semantic_repair")).toBeInTheDocument();
  expect(screen.getByText("默认并发 3 · 最多修复 2 轮 · 完成后仍需人工激活")).toBeInTheDocument();
});
