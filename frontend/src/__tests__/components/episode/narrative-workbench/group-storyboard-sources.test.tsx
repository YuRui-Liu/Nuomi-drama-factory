import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ api: ky.create({ baseUrl: "http://localhost:3000/" }) }));
import { GroupStoryboardSources } from "@/components/episode/narrative-workbench/group-storyboard-sources";
import { GroupPipeline } from "@/components/episode/narrative-workbench/group-pipeline";

const root = "http://localhost:3000/api/v1/projects/demo/episodes/1/narrative-groups/group/storyboard-sources";
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

it("shows the source selector in the production workbench for versioned frames", async () => {
  server.use(
    http.get(root, () => HttpResponse.json({ ok: true, data: {
      selected_storyboard_id: "a", selected_storyboard_sources: { batch: "a" }, items: [],
    } })),
    http.get(/\/revisions$/, () => HttpResponse.json({ ok: true, data: { items: [], current_revision: 1 } })),
  );
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <GroupPipeline project="demo" episode={1} group={{
      id: "group", ordinal: 1, title: "灯塔", beat_ids: [], layout: { rows: 1, columns: 1, capacity: 1 },
      cell_to_beat: [], errors: [], stages: {
        sketch: { status: "pending", revision: 0 },
        render: { status: "completed", revision: 1, selected_storyboard_id: "a" },
        video: { status: "pending", revision: 0 },
      },
    }} onAction={vi.fn()} onRepairBeat={vi.fn()} />
  </QueryClientProvider>);
  expect(await screen.findByRole("region", { name: "分镜来源" })).toBeInTheDocument();
});

it.each([false, true])("selects without generation and preserves CAS conflicts (%s)", async (conflict) => {
  let selected = "a";
  const writes: unknown[] = [];
  server.use(
    http.get(root, () => HttpResponse.json({ ok: true, data: {
      selected_storyboard_id: selected, selected_storyboard_sources: { batch: selected },
      items: [
        { source_id: "a", asset_id: "original.png", grid_url: "/a.png", validation: { valid: true } },
        { source_id: "b", asset_id: "candidate.png", grid_url: "/b.png", validation: { valid: true } },
        { source_id: "broken", validation: { valid: false, code: "STORYBOARD_SOURCE_INVALID" } },
      ],
    } })),
    http.put(root + "/selection", async ({ request }) => {
      writes.push(await request.json());
      if (conflict) return HttpResponse.json({ detail: { code: "STORYBOARD_SELECTION_CHANGED" } }, { status: 409 });
      selected = "b";
      return HttpResponse.json({ ok: true, data: { selected_storyboard_id: selected } });
    }),
  );
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <GroupStoryboardSources project="demo" episode={1} groupId="group" />
  </QueryClientProvider>);
  const select = await screen.findByRole("button", { name: "选用 candidate.png" });
  expect(screen.getByRole("button", { name: "选用 broken" })).toBeDisabled();
  expect(screen.getByAltText("分镜来源 original.png")).toHaveAttribute("src", "/a.png");
  fireEvent.click(select);
  if (conflict) {
    expect(await screen.findByRole("alert")).toHaveTextContent("来源已变化，请刷新后重新选择");
  } else {
    await waitFor(() => expect(screen.getByRole("button", { name: "当前选中 candidate.png" })).toBeDisabled());
  }
  expect(writes).toEqual([{ source_id: "b", expected_selected_id: "a" }]);
});
