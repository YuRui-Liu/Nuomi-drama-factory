// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { ArrowLeft, Check, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  PROJECT_SECTION_ROUTES,
  PROJECT_NAV_ITEMS,
  projectSectionFromPath,
} from "@/components/layout/project-navigation-routes";
import { normalizeLastEpisodeLocation, useEpisodeWorkbenchStore } from "@/stores/episode-workbench-store";
import { isRememberedSection, useProjectNavStore } from "@/stores/project-nav-store";
import { useAllProjectSummaries } from "@/lib/queries/projects";
import { getProjectCover } from "@/lib/project-cover";
import { cn } from "@/lib/utils";

function ProjectAvatar({ name }: { name: string }) {
  const { gradient, initial } = useMemo(() => getProjectCover(name), [name]);
  return (
    <span
      className="flex size-5 shrink-0 items-center justify-center rounded text-xs font-bold text-white/95"
      style={{ background: gradient }}
    >
      {initial}
    </span>
  );
}

export function ProjectSwitcher({ current }: { current: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: summaries } = useAllProjectSummaries();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const targetSection = projectSectionFromPath(pathname) ?? "freezone";
  const [open, setOpen] = useState(false);
  const closeTimerRef = useRef<number | null>(null);
  const projects = useMemo(
    () =>
      (summaries ?? [])
        .filter((project) => project.status === "active")
        .map((project) => ({ id: project.id || project.name, name: project.name })),
    [summaries],
  );
  const currentSummary = useMemo(
    () =>
      projects.find((project) => project.id === current) ??
      projects.find((project) => project.name === current),
    [current, projects],
  );
  const currentName = currentSummary?.name ?? current;

  const cancelClose = () => {
    if (closeTimerRef.current === null) return;
    window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = null;
  };
  const openMenu = () => {
    cancelClose();
    setOpen(true);
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false);
      closeTimerRef.current = null;
    }, 120);
  };

  useEffect(() => cancelClose, []);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        onMouseEnter={openMenu}
        onMouseLeave={scheduleClose}
        className="inline-flex h-8 max-w-[156px] cursor-pointer items-center gap-1.5 bg-transparent px-1 text-left text-[13px] leading-none text-sidebar-foreground/90 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
      >
        <span className="min-w-0 truncate leading-none">{currentName}</span>
        <ChevronDown className="size-3.5 shrink-0 translate-y-px text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        sideOffset={8}
        onMouseEnter={openMenu}
        onMouseLeave={scheduleClose}
        className="w-56 rounded-md border border-white/10 bg-popover p-1 shadow-xl shadow-black/20 ring-0"
      >
        <DropdownMenuGroup>
          <DropdownMenuItem
            onClick={() => navigate({ to: "/" })}
            className="min-h-8 gap-2 rounded-sm px-2 py-1.5 text-xs focus:bg-white/8 focus:text-current"
          >
            <ArrowLeft className="size-3.5" />
            {t("project.dashboardReturn")}
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuLabel className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
            {t("nav.switchProject")}
          </DropdownMenuLabel>
          {projects.map((project) => (
            <DropdownMenuItem
              key={project.id}
              onClick={() =>
                navigate({
                  to: PROJECT_SECTION_ROUTES[targetSection],
                  params: { project: project.id },
                })
              }
              className="min-h-8 gap-2 rounded-sm px-2 py-1.5 text-xs focus:bg-white/8 focus:text-current"
            >
              <ProjectAvatar name={project.name} />
              <span className="flex-1 truncate">{project.name}</span>
              {project.id === current ? (
                <Check className="size-3.5 text-primary" aria-hidden />
              ) : null}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function ProjectHeaderNavigation({ project }: { project: string }) {
  const { t } = useTranslation();
  const location = useRouterState({
    select: (state) => ({
      pathname: state.location.pathname,
      searchStr: state.location.searchStr,
      hash: state.location.hash,
    }),
  });
  const { pathname } = location;
  const rememberedEpisodeLocation = useEpisodeWorkbenchStore(
    (state) => state.lastEpisodeLocationByProject[project],
  );
  const setLastEpisodeLocation = useEpisodeWorkbenchStore((state) => state.setLastEpisodeLocation);
  const clearLastEpisodeLocation = useEpisodeWorkbenchStore((state) => state.clearLastEpisodeLocation);
  const rememberSection = useProjectNavStore((state) => state.rememberSection);

  // 保留原有区块记忆，确保项目切换与旧持久化数据继续兼容。
  useEffect(() => {
    const section = projectSectionFromPath(pathname);
    if (isRememberedSection(section)) {
      rememberSection(project, section);
    }
  }, [pathname, project, rememberSection]);

  useEffect(() => {
    const episodesRoot = `/projects/${encodeURIComponent(project)}/episodes`;
    if (pathname === episodesRoot) {
      clearLastEpisodeLocation(project);
      return;
    }
    const match = pathname.match(/^\/projects\/([^/]+)\/episodes\/(\d+)(?:\/|$)/);
    if (!match || decodeURIComponent(match[1]) !== project) return;
    const hash = location.hash
      ? `#${location.hash.replace(/^#/, "")}`
      : "";
    setLastEpisodeLocation(project, `${pathname}${location.searchStr}${hash}`);
  }, [
    clearLastEpisodeLocation,
    location.hash,
    location.searchStr,
    pathname,
    project,
    setLastEpisodeLocation,
  ]);

  return (
    <nav
      aria-label={t("nav.projectNavigation")}
      className="col-start-2 row-start-1 flex h-14 min-w-0 items-stretch overflow-x-auto whitespace-nowrap max-lg:col-span-2 max-lg:col-start-1 max-lg:row-start-2 max-lg:h-10 max-lg:w-full"
    >
      {PROJECT_NAV_ITEMS.map((item) => {
        const sectionPath = item.to.replace("$project", encodeURIComponent(project));
        const active = pathname === sectionPath || pathname.startsWith(`${sectionPath}/`);
        const target =
          item.to === PROJECT_SECTION_ROUTES.episodes && rememberedEpisodeLocation
            ? normalizeLastEpisodeLocation(project, rememberedEpisodeLocation) ?? item.to
            : item.to;

        return (
          <Link
            key={item.labelKey}
            to={target}
            params={{ project }}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative inline-flex min-h-10 items-center px-3 text-xs font-medium transition-colors duration-150",
              "after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-[var(--brand-accent)] after:transition-opacity",
              active
                ? "text-foreground after:opacity-100"
                : "text-muted-foreground after:opacity-0 hover:text-foreground",
            )}
          >
            {t(item.labelKey)}
          </Link>
        );
      })}
    </nav>
  );
}
