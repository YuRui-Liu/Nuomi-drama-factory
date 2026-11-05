// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement, Fragment, type ComponentProps, type ComponentType, type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigateMock = vi.hoisted(() => vi.fn());
const routeState = vi.hoisted(() => ({ project: "missing-project" as string | undefined }));
const projectSummariesState = vi.hoisted(() => ({
  data: [] as Array<{ id: string; name: string }> | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  isSuccess: true,
  refetch: vi.fn(),
}));
const authState = vi.hoisted(() => ({
  username: "dev-user" as string | null,
  validateSession: vi.fn<() => Promise<boolean>>(),
  refreshAvatar: vi.fn<() => Promise<void>>(),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: unknown) => ({ options }),
  Outlet: () => createElement("div", { "data-testid": "outlet" }),
  redirect: (options: unknown) => ({ options }),
  useNavigate: () => navigateMock,
  useParams: () => routeState,
  useRouterState: ({ select }: { select: (state: { location: { pathname: string } }) => unknown }) =>
    select({ location: { pathname: routeState.project ? `/projects/${routeState.project}/episodes` : "/" } }),
}));

vi.mock("@/lib/runtime-config", () => ({
  authRequired: () => true,
}));

const useAuthStoreMock = Object.assign(
  (selector: (state: typeof authState) => unknown) => selector(authState),
  { getState: () => authState },
);

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: useAuthStoreMock,
}));

vi.mock("@/components/layout/header", () => ({ Header: () => null }));
vi.mock("@/hooks/use-reduced-motion", () => ({ useReducedMotion: () => true }));
vi.mock("@/stores/app-store", () => ({
  useAppStore: {
    getState: () => ({ clampDimensionsToViewport: vi.fn() }),
  },
}));
vi.mock("@/lib/queries/projects", () => ({
  useAllProjectSummaries: () => projectSummariesState,
}));
vi.mock("@/stores/region-store", () => ({
  useRegionStore: {
    getState: () => ({ sanitizeAgainstConfig: vi.fn() }),
  },
}));
vi.mock("@/lib/region-tab-sync", () => ({ initRegionTabSync: vi.fn() }));
vi.mock("@/lib/observability", () => ({ initObservability: vi.fn() }));
vi.mock("@/task-center/provider", () => ({
  TaskCenterProvider: ({ children }: PropsWithChildren) => createElement(Fragment, null, children),
}));
vi.mock("@/components/task-center/status-bar", () => ({ TaskStatusBar: () => null }));
vi.mock("@/components/task-center/panel", () => ({ TaskPanel: () => null }));
vi.mock("@/features/companion/MyBuddyCompanion", () => ({ MyBuddyCompanion: () => null }));
vi.mock("@/features/rewards/AccessoryUnlockPrompt", () => ({ AccessoryUnlockPrompt: () => null }));
vi.mock("@/features/version-update/VersionUpdateDialog", () => ({ VersionUpdateDialog: () => null }));
vi.mock("@/features/piko-mini-game/PikoInspirationStation", () => ({ PikoInspirationStation: () => null }));
vi.mock("framer-motion", () => ({
  motion: {
    div: ({ children, ...props }: ComponentProps<"div">) => createElement("div", props, children),
  },
}));

vi.mock("@/lib/cluster-config", () => ({
  clusterConfig: { mode: "none" },
}));

vi.mock("@/lib/region-cookie", () => ({
  getRegionCookie: () => "region-a",
}));

async function renderAppLayout() {
  const { Route } = await import("@/routes/_app");
  const Component = Route.options.component as ComponentType;
  render(createElement(Component));
}

describe("_app project URL guard", () => {
  beforeEach(() => {
    vi.resetModules();
    navigateMock.mockReset();
    authState.username = "dev-user";
    authState.validateSession.mockResolvedValue(true);
    routeState.project = "missing-project";
    projectSummariesState.data = [];
    projectSummariesState.isLoading = false;
    projectSummariesState.isFetching = false;
    projectSummariesState.isError = false;
    projectSummariesState.isSuccess = true;
    projectSummariesState.refetch.mockReset();
  });

  it("redirects home when loaded summaries do not contain the URL project", async () => {
    await renderAppLayout();

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith({ to: "/", replace: true }));
  });

  it("does not redirect when loaded summaries contain the URL project", async () => {
    routeState.project = "project-a";
    projectSummariesState.data = [{ id: "project-a", name: "Project A" }];

    await renderAppLayout();
    await waitFor(() => expect(authState.validateSession).toHaveBeenCalled());

    expect(navigateMock).not.toHaveBeenCalledWith({ to: "/", replace: true });
  });

  it("does not redirect while project summaries are still loading", async () => {
    projectSummariesState.isLoading = true;
    projectSummariesState.isFetching = true;
    projectSummariesState.isSuccess = false;

    await renderAppLayout();

    expect(navigateMock).not.toHaveBeenCalledWith({ to: "/", replace: true });
  });

  it("preserves the project URL and offers retry when summaries fail", async () => {
    projectSummariesState.data = [];
    projectSummariesState.isLoading = false;
    projectSummariesState.isError = true;
    projectSummariesState.isSuccess = false;

    await renderAppLayout();

    expect(await screen.findByText("暂时无法确认项目状态")).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalledWith({ to: "/", replace: true });
    act(() => fireEvent.click(screen.getByRole("button", { name: "重试" })));
    expect(projectSummariesState.refetch).toHaveBeenCalledTimes(1);
  });

  it("waits for a pending refresh before treating cached summaries as authoritative", async () => {
    projectSummariesState.data = [];
    projectSummariesState.isLoading = false;
    projectSummariesState.isFetching = true;
    projectSummariesState.isError = false;
    projectSummariesState.isSuccess = true;

    await renderAppLayout();

    expect(navigateMock).not.toHaveBeenCalledWith({ to: "/", replace: true });
  });
});
