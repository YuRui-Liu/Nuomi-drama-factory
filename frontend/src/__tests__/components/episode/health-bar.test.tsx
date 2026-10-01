// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EpisodeHealthSummary, HealthBar } from "@/components/episode/health-bar";
import { useBeatStates } from "@/hooks/use-beat-states";
const routing = vi.hoisted(() => ({ navigate: vi.fn(), sub: 'video' }));
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => routing.navigate,
  useSearch: () => ({sub:routing.sub}),
  useRouterState: ({select}: {select: (s: {location:{pathname:string}}) => unknown}) => select({location:{pathname:'/projects/p/episodes/2/beats'}}),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, number>) => {
      const labels: Record<string, string> = {
        "episode.nav.script": "Script",
        "episode.nav.shots": "Beats",
        "episode.nav.compose": "Compose",
        "episode.health.readyRatio": `${vars?.ready}/${vars?.total}`,
        "episode.health.blockedCount": `${vars?.count} blocked`,
        "episode.health.composeReady": "Ready",
        "episode.health.composeBlocked": `Missing ${vars?.count}`,
      };
      return labels[key] ?? key;
    },
  }),
}));

vi.mock("@/hooks/use-beat-states", () => ({
  useBeatStates: vi.fn(),
}));

const mockUseBeatStates = vi.mocked(useBeatStates);

describe("EpisodeHealthSummary", () => {
  it('offers the approved four stages and keeps video separate from storyboards', () => {
    render(<HealthBar project="p" episode={2} />);
    expect(screen.getByRole('button', {name:/视频生成/})).toHaveAttribute('aria-current','page');
    fireEvent.click(screen.getByRole('button', {name:/分镜生图/}));
    expect(routing.navigate).toHaveBeenLastCalledWith({to:'/projects/$project/episodes/$episode/beats',params:{project:'p',episode:'2'},search:{sub:'render'}});
    fireEvent.click(screen.getByRole('button', {name:/叙事组脚本/}));
    expect(routing.navigate).toHaveBeenLastCalledWith({to:'/projects/$project/episodes/$episode/script',params:{project:'p',episode:'2'},search:{}});
    expect(screen.getByRole('button', {name:/整集合成/})).toBeInTheDocument();
  });
  it("shows ready and blocked health for script, beats, and compose tabs", () => {
    mockUseBeatStates.mockReturnValue({
      states: {},
      loading: false,
      counts: {
        script: { ready: 2, total: 3, active: 1, failed: 0 },
        sketch: { ready: 1, total: 3, active: 1, failed: 0 },
        audio: { ready: 2, total: 3, active: 0, failed: 0 },
        video: { ready: 1, total: 3, active: 0, failed: 1 },
        compose: {
          ready: false,
          missing: [
            { beatNum: 1, stages: ["audio"] },
            { beatNum: 3, stages: ["video"] },
          ],
        },
      },
    });

    render(<EpisodeHealthSummary project="demo" episode={1} />);

    const script = screen.getByTestId("episode-health-script-status");
    expect(within(script).getByText("2/3")).toBeInTheDocument();
    expect(within(script).queryByText("1 blocked")).not.toBeInTheDocument();

    const beats = screen.getByTestId("episode-health-beats-status");
    expect(within(beats).getByText("4/9")).toBeInTheDocument();
    expect(within(beats).queryByText("5 blocked")).not.toBeInTheDocument();

    const compose = screen.getByTestId("episode-health-compose-status");
    expect(within(compose).getByText("Missing 2")).toBeInTheDocument();
  });
});
