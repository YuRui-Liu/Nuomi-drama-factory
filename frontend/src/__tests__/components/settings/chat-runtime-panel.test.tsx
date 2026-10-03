import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";

const mutateAsync = vi.fn();
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/queries/chat-runtime", () => ({
  useChatRuntimeConfig: () => ({ data: { data: { backend: "hermes", model: "", reasoningEffort: null } }, isLoading: false }),
  useSaveChatRuntimeConfig: () => ({ mutateAsync, isPending: false }),
}));
import { ChatRuntimePanel } from "@/components/settings/chat-runtime-panel";

beforeEach(() => { vi.clearAllMocks(); mutateAsync.mockResolvedValue({ ok: true }); });

it("offers every chat backend and saves the selected Agent independently", async () => {
  const user = userEvent.setup();
  render(<ChatRuntimePanel />);
  await user.click(screen.getByRole("combobox", { name: "聊天 Agent 运行时" }));
  for (const name of ["Codex", "WorkBuddy", "DeepSeek Harness"]) expect(screen.getByText(name)).toBeInTheDocument();
  await user.click(screen.getByText("Codex"));
  expect(screen.getByLabelText("聊天模型")).toHaveValue("gpt-5.6-sol");
  fireEvent.click(screen.getByRole("button", { name: "保存聊天 Agent" }));
  await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({ backend: "codex", model: "gpt-5.6-sol", reasoningEffort: null }));
  expect(toast.success).toHaveBeenCalledWith("聊天 Agent 已保存，下一轮对话生效");
});

it("reports failure without displaying success", async () => {
  mutateAsync.mockResolvedValue({ ok: false, error: "保存被拒绝" });
  render(<ChatRuntimePanel />);
  fireEvent.click(screen.getByRole("button", { name: "保存聊天 Agent" }));
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("保存被拒绝"));
  expect(toast.success).not.toHaveBeenCalled();
});

it("hides unsupported Hermes reasoning and clears a CLI effort when saving Hermes", async () => {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  render(<ChatRuntimePanel />);
  expect(screen.queryByLabelText("聊天推理强度（可选）")).not.toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "聊天 Agent 运行时" }));
  await user.click(screen.getByText("Codex"));
  await user.click(screen.getByRole("combobox", { name: "聊天推理强度（可选）" }));
  await user.click(screen.getByText("high"));
  await user.click(screen.getByRole("combobox", { name: "聊天 Agent 运行时" }));
  await user.click(screen.getByText("Hermes"));
  fireEvent.click(screen.getByRole("button", { name: "保存聊天 Agent" }));
  await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({ backend: "hermes", model: "", reasoningEffort: null }));
});
