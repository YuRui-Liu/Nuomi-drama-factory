// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Header } from "@/components/layout/header";

const runtimeState = vi.hoisted(() => ({ authRequired: true, isCe: false }));
const authState = vi.hoisted(() => ({ username: "local", logout: vi.fn() }));
const resetUserSessionStateMock = vi.hoisted(() => vi.fn());
const navigateMock = vi.hoisted(() => vi.fn());
const episodeStoreState = vi.hoisted(() => ({
  lastEpisodeLocationByProject: {} as Record<string, string>,
  setLastEpisodeLocation: vi.fn(),
  clearLastEpisodeLocation: vi.fn(),
}));
const projectNavState = vi.hoisted(() => ({ rememberSection: vi.fn() }));
const routerState = vi.hoisted(() => ({
  pathname: "/",
  searchStr: "",
  hash: "",
  project: undefined as string | undefined,
}));

vi.mock("@/lib/reset-region-state", () => ({
  resetUserSessionState: resetUserSessionStateMock,
}));

vi.mock("@/lib/runtime-config", () => ({
  authRequired: () => runtimeState.authRequired,
  isCeRuntime: () => runtimeState.isCe,
}));

vi.mock("@/lib/queries/model-gateway", () => ({
  useModelGatewayConfig: () => ({ data: undefined }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to = "", params, ...props }: React.ComponentProps<"a"> & {
    to?: string;
    params?: { project?: string };
  }) => {
    const href = params?.project
      ? to.replace("$project", encodeURIComponent(params.project))
      : to;
    return <a href={href} {...props}>{children}</a>;
  },
  useNavigate: () => navigateMock,
  useParams: () => ({ project: routerState.project }),
  useRouterState: ({ select }: {
    select: (state: { location: { pathname: string; searchStr: string; hash: string } }) => unknown;
  }) => select({
    location: {
      pathname: routerState.pathname,
      searchStr: routerState.searchStr,
      hash: routerState.hash,
    },
  }),
}));

vi.mock("@/stores/episode-workbench-store", () => ({
  normalizeLastEpisodeLocation: (_project: string, location: unknown) =>
    typeof location === "string" ? location : null,
  useEpisodeWorkbenchStore: (selector: (state: typeof episodeStoreState) => unknown) =>
    selector(episodeStoreState),
}));

vi.mock("@/stores/project-nav-store", () => ({
  isRememberedSection: (section: string | null) =>
    section !== null && section !== "tasks",
  useProjectNavStore: (selector: (state: typeof projectNavState) => unknown) =>
    selector(projectNavState),
}));

vi.mock("@/lib/queries/projects", () => ({
  useAllProjectSummaries: () => ({
    data: [
      { id: "demo", name: "Demo", status: "active" },
      { id: "next", name: "Next", status: "active" },
    ],
  }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "app.logoHomeTooltip": "Home",
        "header.account.open": "Open account",
        "header.account.changeAvatar": "Change avatar",
        "header.account.selectLanguage": "Select language",
        "header.account.languageChinese": "Chinese",
        "header.account.languageEnglish": "English",
        "auth.logout": "Log out",
        "nav.projectNavigation": "Project navigation",
        "nav.ingest": "Script import",
        "nav.assets": "Asset center",
        "nav.episodes": "Episode production",
        "nav.freezone": "Creation canvas",
        "nav.styles": "Visual styles",
        "nav.tasks": "Task center",
        "nav.aiAssistant": "Nuomi assistant",
        "nav.creationMode": "旧模式切换",
        "nav.xiaji": "虾集",
        "nav.xiajiMenu": "旧制作菜单",
      })[key] ?? key,
    i18n: {
      language: "en",
      resolvedLanguage: "en",
      changeLanguage: vi.fn(),
    },
  }),
}));

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: () => authState,
}));

vi.mock("@/stores/app-store", () => ({
  useAppStore: () => vi.fn(),
}));

vi.mock("@/components/layout/credit-balance-badge", () => ({
  CreditBalanceBadge: () => <div data-testid="credit-balance" />,
}));

vi.mock("@/components/task-center/header-entry", () => ({
  HeaderEntry: () => <button type="button">Tasks</button>,
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, ...props }: React.ComponentProps<"button">) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
}));

vi.mock("@/components/ui/tooltip", () => ({
  TooltipProvider: ({ children }: React.PropsWithChildren) => <>{children}</>,
  Tooltip: ({ children }: React.PropsWithChildren) => <>{children}</>,
  TooltipTrigger: ({ children }: React.PropsWithChildren) => <>{children}</>,
  TooltipContent: ({ children }: React.PropsWithChildren) => <>{children}</>,
}));

vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuTrigger: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuContent: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuGroup: ({ children }: React.PropsWithChildren) => <>{children}</>,
  DropdownMenuLabel: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuItem: ({ children, ...props }: React.ComponentProps<"button">) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
}));

function renderHeader({ project, pathname }: { project?: string; pathname?: string } = {}) {
  routerState.project = project;
  routerState.pathname = pathname ?? (project ? `/projects/${project}/ingest` : "/");
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Header />
    </QueryClientProvider>,
  );
}

describe("Header runtime gating", () => {
  beforeEach(() => {
    routerState.project = undefined;
    routerState.pathname = "/";
    routerState.searchStr = "";
    routerState.hash = "";
    runtimeState.authRequired = true;
    authState.username = "local";
    authState.logout.mockReset();
    resetUserSessionStateMock.mockReset();
    navigateMock.mockReset();
    episodeStoreState.lastEpisodeLocationByProject = {};
    episodeStoreState.setLastEpisodeLocation.mockReset();
    episodeStoreState.clearLastEpisodeLocation.mockReset();
    projectNavState.rememberSection.mockReset();
  });

  it("shows the NuomiDrama brand and direct project navigation", () => {
    renderHeader({ project: "demo" });

    expect(screen.getByLabelText("NuomiDrama")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Project navigation" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Script import" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getAllByRole("link", { name: /Script import|Asset center|Episode production|Creation canvas|Visual styles|Task center|Nuomi assistant/ })).toHaveLength(7);
    expect(screen.queryByRole("navigation", { name: "旧模式切换" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "旧制作菜单" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "虾集" })).not.toBeInTheDocument();
  });

  it("uses a non-overlapping desktop grid and a scrollable narrow-screen navigation row", () => {
    renderHeader({ project: "demo" });

    expect(screen.getByRole("banner")).toHaveClass(
      "grid",
      "grid-cols-[minmax(0,1fr)_minmax(240px,2fr)_minmax(0,1fr)]",
      "max-lg:grid-rows-[56px_40px]",
    );
    expect(screen.getByRole("navigation", { name: "Project navigation" })).toHaveClass(
      "min-w-0",
      "overflow-x-auto",
      "max-lg:col-span-2",
      "max-lg:row-start-2",
    );
    expect(screen.getByRole("navigation", { name: "Project navigation" })).not.toHaveClass(
      "absolute",
    );
  });

  it("resolves every direct navigation link for the current project", () => {
    renderHeader({ project: "demo" });

    expect(screen.getByRole("link", { name: "Script import" })).toHaveAttribute("href", "/projects/demo/ingest");
    expect(screen.getByRole("link", { name: "Asset center" })).toHaveAttribute("href", "/projects/demo/characters");
    expect(screen.getByRole("link", { name: "Episode production" })).toHaveAttribute("href", "/projects/demo/episodes");
    expect(screen.getByRole("link", { name: "Creation canvas" })).toHaveAttribute("href", "/projects/demo/freezone");
    expect(screen.getByRole("link", { name: "Visual styles" })).toHaveAttribute("href", "/projects/demo/styles");
    expect(screen.getByRole("link", { name: "Task center" })).toHaveAttribute("href", "/projects/demo/tasks");
    expect(screen.getByRole("link", { name: "Nuomi assistant" })).toHaveAttribute("href", "/projects/demo/assistant");
  });

  it("keeps the current section when switching projects", () => {
    renderHeader({ project: "demo", pathname: "/projects/demo/tasks" });

    fireEvent.click(screen.getByText("Next"));
    expect(navigateMock).toHaveBeenCalledWith({
      to: "/projects/$project/tasks",
      params: { project: "next" },
    });
  });

  it("restores the remembered episode deep link", () => {
    episodeStoreState.lastEpisodeLocationByProject.demo =
      "/projects/demo/episodes/12?group=ng-02#video";
    renderHeader({ project: "demo" });

    expect(screen.getByRole("link", { name: "Episode production" })).toHaveAttribute(
      "href",
      "/projects/demo/episodes/12?group=ng-02#video",
    );
  });

  it("updates the remembered episode location when only query and hash change", () => {
    const view = renderHeader({
      project: "demo",
      pathname: "/projects/demo/episodes/12",
    });
    expect(episodeStoreState.setLastEpisodeLocation).toHaveBeenLastCalledWith(
      "demo",
      "/projects/demo/episodes/12",
    );

    routerState.searchStr = "?group=ng-03";
    routerState.hash = "#video";
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <Header />
      </QueryClientProvider>,
    );

    expect(episodeStoreState.setLastEpisodeLocation).toHaveBeenLastCalledWith(
      "demo",
      "/projects/demo/episodes/12?group=ng-03#video",
    );
  });

  it("renders logout in the account panel when runtime requires auth", async () => {
    renderHeader();

    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);

    expect(await screen.findByText("Log out")).toBeInTheDocument();
  });

  it("hides logout when runtime does not require auth while keeping the local identity", async () => {
    runtimeState.authRequired = false;

    renderHeader();

    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);

    await waitFor(() => {
      expect(screen.getByText("local")).toBeInTheDocument();
    });
    expect(screen.queryByText("Log out")).not.toBeInTheDocument();
  });

  it("purges user-scoped caches after logout so the next account can't see stale data", async () => {
    // 回归用例：手动退出是 SPA 内部跳转，不清 QueryClient 的话换账号登录后
    // projectSummaries 还在 staleTime 内，新账号会看到上一个账号的项目列表。
    authState.logout.mockResolvedValue(undefined);

    renderHeader();

    fireEvent.mouseEnter(screen.getByLabelText("Open account").parentElement!);
    fireEvent.click(await screen.findByText("Log out"));

    await waitFor(() => {
      expect(resetUserSessionStateMock).toHaveBeenCalled();
    });
    expect(authState.logout).toHaveBeenCalled();
  });
});
