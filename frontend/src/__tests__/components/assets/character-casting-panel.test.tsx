import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));
import { CharacterCastingPanel } from "@/components/assets/character-casting-panel";

const base =
  "http://localhost:3000/api/v1/projects/demo/characters/hero/casting";
const fact = {
  fact_id: "f1",
  field: "hair_style",
  value: "齐肩黑发",
  evidence: "她拨开齐肩黑发",
  source_span: { start_line: 12, end_line: 14 },
  source_document: "第一章",
  confidence: 1,
  assertion: "explicit",
  trust: "trusted",
};
const decisions = [
  {
    schema_version: 1,
    decision_id: "d1",
    attribute: "hair_style",
    value: "齐肩黑发",
    reason: "遵循原文",
    basis: "evidence",
    fact_ids: ["f1"],
  },
  {
    schema_version: 1,
    decision_id: "d2",
    attribute: "face_shape",
    value: "宽颧窄颌",
    reason: "形成轮廓辨识度",
    basis: "creative_choice",
    fact_ids: [],
  },
];
const proposals = ["纪实", "克制", "锐利"].map((title, i) => ({
  proposal_id: `p${i}`,
  title,
  rationale: "保持普通人的可信度",
  kind: "creative_design",
  recommended: i === 0,
  face_shape: "宽颧窄颌",
  casting_decisions: decisions,
  identity_anchors: ["宽颧", "窄颌"],
  quality_issues: [],
  facial_features: [],
  distinctive_features: [],
  outfit_states: {},
  asymmetry_detail: "",
}));
function workspace(extra = {}) {
  return {
    character_id: "hero",
    identity_id: null,
    can_edit: true,
    identities: [{ identity_id: "old-id", name: "晚年" }],
    dossier: {
      narrative: { biography: "广播站主持人" },
      hard_constraints: [fact],
      interpretations: [],
      issues: [],
    },
    revision: { revision_id: "r1" },
    proposals,
    selected_proposal_id: "p0",
    current: { url: "/current.png", candidate_id: "old" },
    legacy_current: null,
    tasks: [],
    draft_stale: false,
    ...extra,
  };
}
function candidate(extra = {}) {
  return {
    candidate_id: "c1",
    generation_status: "succeeded",
    review_status: "failed",
    error: "检查服务暂时不可用",
    snapshot: {
      revision_id: "r1",
      hard_constraints: [fact],
      design_decisions: decisions,
      prompt: "compiled secret",
    },
    url: "/candidate.png",
    stale: false,
    report: null,
    review_attempts: [],
    adoption_requirements: {
      expected_review_attempt_id: "a1",
      required_acknowledgements: ["review_failed"],
      override_reason_required: true,
      blocked_reason: null,
    },
    ...extra,
  };
}
const server = setupServer();
let writes: Array<{ url: string; body: Record<string, unknown> }>;
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  writes = [];
  server.use(
    http.get(base, () => HttpResponse.json({ ok: true, data: workspace() })),
    http.get(`${base}/candidates`, () =>
      HttpResponse.json({ ok: true, data: [candidate()] }),
    ),
    http.get("http://localhost:3000/api/v1/generation-credit-cost", () =>
      HttpResponse.json({ ok: true, data: { display: "6 积分", cost: 6 } }),
    ),
    http.post(`${base}/*`, async ({ request }) => {
      writes.push({
        url: request.url,
        body: (await request.json()) as Record<string, unknown>,
      });
      return HttpResponse.json({ ok: true, data: { status: "queued" } });
    }),
  );
});
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <CharacterCastingPanel
          project="demo"
          name="hero"
          imageModel="portrait-model"
        />
      </QueryClientProvider>,
    ),
  };
}
describe("story-grounded casting", () => {
  it("uses an external identity entry and resets selection and acknowledgements", async () => {
    function Entry() {
      const [identityId, setIdentityId] = useState<string | null>(null);
      return (
        <>
          <button onClick={() => setIdentityId("old-id")}>
            从晚年入口选角
          </button>
          <button onClick={() => setIdentityId(null)}>从基础入口选角</button>
          <CharacterCastingPanel
            project="demo"
            name="hero"
            imageModel="portrait-model"
            identityId={identityId}
            onIdentityChange={setIdentityId}
          />
        </>
      );
    }
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <Entry />
      </QueryClientProvider>,
    );
    await screen.findByAltText("候选 1");
    await userEvent.click(screen.getByRole("button", { name: "选择候选 1" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /检查失败/ }));
    await userEvent.type(
      screen.getByLabelText("采用原因"),
      "基础形象的人工确认",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "从晚年入口选角" }),
    );
    expect(screen.getByLabelText("身份阶段")).toHaveValue("old-id");
    await screen.findByRole("button", { name: "生成候选" });
    expect(screen.queryByLabelText("采用原因")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "生成候选" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].url).toBe(`${base}/candidates?identity_id=old-id`);
    await userEvent.click(
      screen.getByRole("button", { name: "从基础入口选角" }),
    );
    expect(screen.getByLabelText("身份阶段")).toHaveValue("");
    expect(screen.queryByLabelText("采用原因")).not.toBeInTheDocument();
  });
  it("does not dirty an unchanged proposal selection or a reverted edit", async () => {
    mount();
    await screen.findByRole("heading", { name: "纪实" });
    await userEvent.click(screen.getByRole("button", { name: "选择纪实" }));
    expect(screen.getByRole("button", { name: "生成候选" })).toBeEnabled();
    await userEvent.click(screen.getByText("编辑造型决定"));
    const input = screen.getByLabelText("脸型造型决定");
    await userEvent.clear(input);
    await userEvent.type(input, "方脸");
    expect(screen.getByRole("button", { name: "生成候选" })).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "宽颧窄颌");
    expect(screen.getByRole("button", { name: "生成候选" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "保存选角方案" })).toBeDisabled();
  });
  it("clears the saved draft even when the server revision remains unchanged", async () => {
    server.use(
      http.patch(base, () =>
        HttpResponse.json({
          ok: true,
          data: { revision: { revision_id: "r1" } },
        }),
      ),
    );
    mount();
    await screen.findByRole("heading", { name: "纪实" });
    await userEvent.click(screen.getByRole("button", { name: "选择克制" }));
    await userEvent.click(screen.getByRole("button", { name: "保存选角方案" }));
    await screen.findByText("设计已保存，可生成候选。");
    expect(screen.getByRole("button", { name: "生成候选" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "保存选角方案" })).toBeDisabled();
  });
  it("does not clear a newer edit when an older save response arrives", async () => {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    let received = false;
    server.use(
      http.patch(base, async () => {
        received = true;
        await pending;
        return HttpResponse.json({
          ok: true,
          data: { revision: { revision_id: "r1" } },
        });
      }),
    );
    mount();
    await screen.findByRole("heading", { name: "纪实" });
    await userEvent.click(screen.getByText("编辑造型决定"));
    const input = screen.getByLabelText("脸型造型决定");
    fireEvent.change(input, { target: { value: "方脸" } });
    await userEvent.click(screen.getByRole("button", { name: "保存选角方案" }));
    await waitFor(() => expect(received).toBe(true));
    // Reproduce a change event already queued before the pending render.
    fireEvent.change(input, { target: { value: "窄长脸" } });
    release();
    await screen.findByText("设计已保存，可生成候选。");
    expect(input).toHaveValue("窄长脸");
    expect(screen.getByRole("button", { name: "生成候选" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存选角方案" })).toBeEnabled();
  });
  it("clears the matching saved draft after retrying the same failed save", async () => {
    let requests = 0;
    server.use(
      http.patch(base, () =>
        ++requests === 1
          ? HttpResponse.json({ detail: "连接中断" }, { status: 503 })
          : HttpResponse.json({
              ok: true,
              data: { revision: { revision_id: "r1" } },
            }),
      ),
    );
    mount();
    await screen.findByRole("heading", { name: "纪实" });
    await userEvent.click(screen.getByRole("button", { name: "选择克制" }));
    await userEvent.click(screen.getByRole("button", { name: "保存选角方案" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "重试同一次请求" }),
    );
    await screen.findByText("设计已保存，可生成候选。");
    expect(screen.getByRole("button", { name: "生成候选" })).toBeEnabled();
  });
  it("reads three proposals with evidence and creative labels without starting paid work", async () => {
    mount();
    expect(
      await screen.findByRole("heading", { name: "选角依据" }),
    ).toBeInTheDocument();
    for (const title of ["纪实", "克制", "锐利"])
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    expect(screen.getAllByText("原文事实").length).toBeGreaterThan(0);
    expect(screen.getAllByText("设计解释").length).toBeGreaterThan(0);
    expect(screen.getAllByText("自由选择").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByText("查看来源原文"));
    expect(screen.getByText("她拨开齐肩黑发")).toBeVisible();
    expect(screen.queryByText("compiled secret")).not.toBeInTheDocument();
    expect(writes).toEqual([]);
  });
  it("keeps the current portrait separate and restores candidates on remount", async () => {
    const view = mount();
    expect(await screen.findByAltText("当前定角")).toHaveAttribute(
      "src",
      "/current.png",
    );
    expect(await screen.findByAltText("候选 1")).toHaveAttribute(
      "src",
      "/candidate.png",
    );
    view.unmount();
    mount();
    expect(await screen.findByAltText("候选 1")).toBeInTheDocument();
    expect(writes).toEqual([]);
  });
  it("never treats a failed check as passing and requires acknowledgement plus reason", async () => {
    mount();
    expect(await screen.findByText("检查失败")).toBeInTheDocument();
    expect(screen.queryByText("检查通过")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "选择候选 1" }));
    const adopt = screen.getByRole("button", { name: "确认定角" });
    expect(adopt).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /检查失败/ }));
    expect(adopt).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText("采用原因"),
      "已人工核对原文与造型",
    );
    await userEvent.click(adopt);
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].url).toBe(`${base}/candidates/c1/adopt`);
    expect(writes[0].body).toMatchObject({
      candidate_id: "c1",
      expected_review_attempt_id: "a1",
      acknowledged_findings: ["review_failed"],
      override_reason: "已人工核对原文与造型",
    });
  });
  it("blocks stale candidates from adoption", async () => {
    server.use(
      http.get(`${base}/candidates`, () =>
        HttpResponse.json({ ok: true, data: [candidate({ stale: true })] }),
      ),
    );
    mount();
    expect(await screen.findByText(/候选已过期/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择候选 1" })).toBeDisabled();
  });
  it.each([false, undefined])(
    "does not permit writes when can_edit is %s",
    async (can_edit) => {
      server.use(
        http.get(base, () =>
          HttpResponse.json({ ok: true, data: workspace({ can_edit }) }),
        ),
      );
      mount();
      await screen.findByRole("heading", { name: "选角依据" });
      expect(screen.getByRole("button", { name: "重新选角" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "生成候选" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "选择候选 1" })).toBeDisabled();
      expect(writes).toEqual([]);
    },
  );
  it("clears candidate and warning form when switching identity stages", async () => {
    const urls: string[] = [];
    server.use(
      http.get(base, ({ request }) => {
        urls.push(request.url);
        return HttpResponse.json({ ok: true, data: workspace() });
      }),
    );
    mount();
    await screen.findByAltText("候选 1");
    await userEvent.click(screen.getByRole("button", { name: "选择候选 1" }));
    await userEvent.type(screen.getByLabelText("采用原因"), "基础形象原因");
    await userEvent.selectOptions(screen.getByLabelText("身份阶段"), "old-id");
    await waitFor(() =>
      expect(urls.some((url) => url.includes("identity_id=old-id"))).toBe(true),
    );
    expect(screen.queryByLabelText("采用原因")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("基础形象原因")).not.toBeInTheDocument();
  });
  it("requires every server finding and shows evidence/design references", async () => {
    const findings = [
      {
        finding_id: "f-warning",
        dimension: "facts",
        verdict: "deviation",
        description: "头发短于原文",
        visibility: "visible",
        fact_ids: ["f1"],
        decision_ids: [],
        reference_candidate_ids: [],
      },
      {
        finding_id: "d-warning",
        dimension: "design",
        verdict: "unjudgeable",
        description: "轮廓被遮挡",
        visibility: "not_visible",
        fact_ids: [],
        decision_ids: ["d2"],
        reference_candidate_ids: [],
      },
    ];
    server.use(
      http.get(`${base}/candidates`, () =>
        HttpResponse.json({
          ok: true,
          data: [
            candidate({
              review_status: "completed",
              report: { findings, comparison_scope: "none" },
              adoption_requirements: {
                expected_review_attempt_id: "a2",
                required_acknowledgements: ["f-warning", "d-warning"],
                override_reason_required: true,
                blocked_reason: null,
              },
            }),
          ],
        }),
      ),
    );
    mount();
    await screen.findByText("头发短于原文");
    expect(screen.getByText(/原文引用：.*齐肩黑发/)).toBeInTheDocument();
    expect(screen.getByText(/设计引用：.*宽颧窄颌/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "选择候选 1" }));
    const form = screen.getByRole("region", { name: "确认定角" });
    await userEvent.click(within(form).getAllByRole("checkbox")[0]);
    await userEvent.type(screen.getByLabelText("采用原因"), "人工确认");
    expect(screen.getByRole("button", { name: "确认定角" })).toBeDisabled();
    await userEvent.click(within(form).getAllByRole("checkbox")[1]);
    expect(screen.getByRole("button", { name: "确认定角" })).toBeEnabled();
  });
  it("retries checks without generating another image", async () => {
    mount();
    await screen.findByAltText("候选 1");
    await userEvent.click(screen.getByRole("button", { name: "重试检查" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].url).toBe(`${base}/candidates/c1/review`);
  });
  it("keeps legacy proposals read only until an explicit recast", async () => {
    server.use(
      http.get(base, () =>
        HttpResponse.json({
          ok: true,
          data: workspace({
            revision: null,
            current: { url: "/current.png", candidate_id: null },
          }),
        }),
      ),
    );
    mount();
    await screen.findByAltText("当前定角");
    expect(screen.getByText(/历史定角/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成候选" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "选择纪实" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "重新选角" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].body.expected_revision).toBeNull();
    expect(screen.getByAltText("当前定角")).toHaveAttribute(
      "src",
      "/current.png",
    );
  });
  it("edits a design while keeping source facts unchanged, then saves before generating", async () => {
    let saved: Record<string, unknown> | undefined;
    server.use(
      http.patch(base, async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ok: true, data: {} });
      }),
    );
    mount();
    await screen.findByRole("heading", { name: "纪实" });
    await userEvent.click(screen.getByText("编辑造型决定"));
    const decision = screen.getByLabelText("脸型造型决定");
    await userEvent.clear(decision);
    await userEvent.type(decision, "方形轮廓");
    expect(screen.getByRole("button", { name: "生成候选" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保存选角方案" }));
    await waitFor(() => expect(saved).toBeDefined());
    expect(saved).not.toHaveProperty("dossier");
    expect(
      (saved?.proposals as typeof proposals)[0].casting_decisions[1].value,
    ).toBe("方形轮廓");
    expect((saved?.proposals as typeof proposals)[0].face_shape).toBe(
      "方形轮廓",
    );
  });
  it("retries an uncertain paid request with the identical idempotency key", async () => {
    let count = 0;
    server.use(
      http.post(`${base}/candidates`, async ({ request }) => {
        writes.push({
          url: request.url,
          body: (await request.json()) as Record<string, unknown>,
        });
        count++;
        return count === 1
          ? HttpResponse.json({ detail: "连接中断" }, { status: 503 })
          : HttpResponse.json({ ok: true, data: {} });
      }),
    );
    mount();
    await screen.findByAltText("当前定角");
    await userEvent.click(screen.getByRole("button", { name: "生成候选" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "重试同一次请求" }),
    );
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(writes[1].body).toEqual(writes[0].body);
    expect(screen.getByAltText("当前定角")).toHaveAttribute(
      "src",
      "/current.png",
    );
  });
  it("honors durable task completion after a restart instead of stale queued status", async () => {
    server.use(
      http.get(base, () =>
        HttpResponse.json({
          ok: true,
          data: workspace({
            tasks: [
              {
                request_id: "t",
                operation: "recast",
                status: "queued",
                execution_status: "completed",
              },
            ],
          }),
        }),
      ),
    );
    mount();
    await screen.findByRole("heading", { name: "选角依据" });
    expect(screen.getByRole("button", { name: "重新选角" })).toBeEnabled();
    expect(screen.getByText("方案整理：已完成")).toBeInTheDocument();
  });
  it.each([
    {
      code: "CASTING_RECAST_FAILED",
      message: "重新选角未能完成：原文、角色或草案可能已变化，或提案未通过验证。当前形象未改变；请检查输入后主动重新选角。",
    },
    "重新选角未能完成：原文、角色或草案可能已变化，或提案未通过验证。当前形象未改变；请检查输入后主动重新选角。",
  ])("shows a failed task without losing the current portrait or blocking explicit recast (%j)", async (error) => {
    const message = typeof error === "string" ? error : error.message;
    server.use(http.get(base, () => HttpResponse.json({
      ok: true,
      data: workspace({ tasks: [{ request_id: "failed-recast", operation: "recast", status: "failed", execution_status: "failed", error }] }),
    })));
    mount();
    expect(await screen.findByText(`方案整理：失败 · ${message}`)).toBeInTheDocument();
    expect(screen.getByAltText("当前定角")).toHaveAttribute("src", "/current.png");
    expect(writes).toEqual([]);
    const recast = screen.getByRole("button", { name: "重新选角" });
    expect(recast).toBeEnabled();
    await userEvent.click(recast);
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].url).toBe(`${base}/recast`);
    expect(screen.getByAltText("当前定角")).toHaveAttribute("src", "/current.png");
  });
});
