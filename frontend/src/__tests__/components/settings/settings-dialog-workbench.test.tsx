import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { useSettingsSnapshot } from "@/components/settings/settings-draft-context";

const state = vi.hoisted(() => ({
  gateway: { isPending: true, isError: false, refetch: vi.fn() },
  runtime: { isPending: false, isError: false, data: { ready: true }, refetch: vi.fn() },
  providers: { isPending: false, isError: false, data: [{ provider_type: "grsai", credential_configured: true }], refetch: vi.fn() },
}));
vi.mock("@/lib/queries/model-gateway", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/queries/model-gateway")>(),
  useModelGatewayConfig: () => state.gateway,
  useSaveMediaRelayConfig: () => ({ isPending: false }),
}));
vi.mock("@/lib/queries/knowledge-runtime", () => ({
  useKnowledgeRuntimeStatus: () => state.runtime,
  useMediaProviderAccounts: () => state.providers,
}));
vi.mock("@/components/settings/knowledge-runtime-section", () => ({ KnowledgeRuntimeSection: () => {
  const [value, setValue] = useState("original");
  const { markSaved } = useSettingsSnapshot("test-runtime", value);
  return <div>媒体配置表单<input aria-label="待保存服务地址" value={value} onChange={(e) => setValue(e.target.value)} /><button onClick={() => markSaved(value)}>保存测试设置</button></div>;
} }));
import { SettingsDialog } from "@/components/settings/settings-dialog";
afterEach(cleanup);

it("guards closing dirty forms, allows continued editing, and clears the guard after save", () => {
  const close = vi.fn();
  render(<SettingsDialog open onOpenChange={close} />);
  fireEvent.change(screen.getByLabelText("待保存服务地址"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("button", { name: "settings.close" }));
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByText("还有未保存的设置")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));
  expect(screen.getByLabelText("待保存服务地址")).toHaveValue("draft");
  fireEvent.click(screen.getByRole("button", { name: "保存测试设置" }));
  fireEvent.click(screen.getByRole("button", { name: "settings.close" }));
  expect(close).toHaveBeenCalledWith(false);
});

it("preserves edited runtime fields while visiting another settings category", () => {
  render(<SettingsDialog open onOpenChange={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("待保存服务地址"), { target: { value: "draft-address" } });
  fireEvent.click(screen.getByRole("button", { name: /媒体存储：/ }));
  fireEvent.click(screen.getByRole("button", { name: "运行时与媒体" }));
  expect(screen.getByLabelText("待保存服务地址")).toHaveValue("draft-address");
});

it("uses independent request states and opens storage from its check entry", () => {
  render(<SettingsDialog open onOpenChange={vi.fn()} />);
  expect(screen.getByText("运行时：已配置")).toBeInTheDocument();
  expect(screen.getByText(/待配置 RunningHub/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /媒体存储：正在检查/ }));
  expect(screen.getByText("媒体存储 · 正在检查配置")).toBeInTheDocument();
});

it("offers an actual retry when the runtime request fails", () => {
  state.runtime.isError = true;
  render(<SettingsDialog open onOpenChange={vi.fn()} />);
  expect(screen.getByText("运行时：读取失败")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新检查运行时与媒体" }));
  expect(state.runtime.refetch).toHaveBeenCalledOnce();
  expect(state.providers.refetch).toHaveBeenCalledOnce();
});
