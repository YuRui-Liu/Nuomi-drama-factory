import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
vi.mock("@/lib/runtime-config", () => ({ isTeamRuntime: () => true }));
const chat = vi.hoisted(() => ({
  historyReady: true, messages: [], connecting: false, connected: true,
  busy: false, deletedIds: new Set(), pinnedIds: new Set(), approvals: [],
  models: [], relayInstances: [], settings: { showToolEvents: true },
  title: "Team assistant", streamText: "", send: vi.fn().mockResolvedValue(true),
  appendNotification: vi.fn(),
}));
vi.mock("@/features/superchat/use-superchat", () => ({ useSuperChat: () => chat }));
vi.mock("@tanstack/react-router", () => ({ useParams: () => ({ project: "team-project" }) }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/task-center/event-bus-context", () => ({ useEventBus: () => ({ on: () => () => {} }) }));
vi.mock("@/features/superchat/ai-avatar", () => ({ useAiAvatarUrl: () => null }));
import { SuperChatPanel } from "@/features/superchat/superchat-panel";
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("allows team users to compose and send through their project chat session", async () => {
  const originalScrollTo = HTMLElement.prototype.scrollTo;
  HTMLElement.prototype.scrollTo = vi.fn();
  render(<SuperChatPanel />);
  const composer = screen.getByRole("textbox");
  fireEvent.change(composer, { target: { value: "帮我检查剧本" } });
  fireEvent.keyDown(composer, { key: "Enter", code: "Enter" });
  await waitFor(() => expect(chat.send).toHaveBeenCalled());
  expect(screen.queryByText(/团队版暂不支持 AI 助手/)).not.toBeInTheDocument();
  cleanup();
  HTMLElement.prototype.scrollTo = originalScrollTo;
});
