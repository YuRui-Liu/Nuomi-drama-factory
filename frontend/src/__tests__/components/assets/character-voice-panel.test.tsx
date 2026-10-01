// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/use-task-controller", () => ({
  useTaskController: () => ({ started: false, start: vi.fn(), stop: vi.fn(), stopping: false, stream: {}, logs: [] }),
}));

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { CharacterVoicePanel } from "@/components/assets/character-voice-panel";
import type { Character } from "@/types/character";

const server = setupServer();
const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh",
    fallbackLng: "zh",
    resources: {
      zh: {
        translation: {
          characters: {
            voiceSamples: {
              title: "声线管理 (IndexTTS2)",
              hint: "通常只需上传默认声线；只有年龄变体需要不同声音时再覆盖。",
              defaultRequired: "默认（必填）",
              ageDefaultRequired: "{{age}}（默认 · 必填）",
              optionalOverride: "{{age}}（可选覆盖）",
              missingDefault: "未配置 → 角色将无法出声",
              inheritedDefault: "→ 继承默认",
              missing: "未配置",
              upload: "上传声音样本",
              record: "录音",
              design: "AI 设计音色",
              designed: "音色设计完成",
              designQueued: "音色设计任务已进入队列",
              trim: "裁剪到 3-5 秒",
              clear: "清除",
              loading: "正在读取声线样本",
              loadFailed: "读取声线样本失败",
              currentDuration: "当前约 {{seconds}} 秒",
            },
            ageGroups: {
              child: "幼年",
              young: "青年",
              middle: "中年",
              elder: "老年",
            },
          },
          common: {
            error: "错误",
          },
        },
      },
    },
    interpolation: { escapeValue: false },
  });
  server.listen();
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </I18nextProvider>
  );
}

function renderPanel(character: Character) {
  return render(
    <CharacterVoicePanel project="demo" character={character} />,
    { wrapper },
  );
}

describe("CharacterVoicePanel", () => {
  it("keeps the current voice before generation and facts, with candidates adopted locally", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "dialogue", age_group: "youth" }, candidates: [{ candidate_id: "c1", slot: "default", status: "qc_unavailable", url: "/candidate.wav" }] });
    const { container } = renderPanel({ name: "秦" });
    await screen.findByRole("button", { name: "采用为角色声音" });
    const current = screen.getByRole("heading", { name: "当前声音" });
    const generation = screen.getByRole("heading", { name: "生成声音" });
    expect(current.compareDocumentPosition(generation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(generation.compareDocumentPosition(screen.getByText("声音设定与依据")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector('[data-voice-slot]')).toHaveAttribute("data-voice-slot", "default");
  });

  it("allows creature audio upload and recording inside the role without a project acceptance link", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "nonverbal" } });
    renderPanel({ name: "秦" });
    expect(await screen.findByRole("button", { name: "上传声音样本" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "录音" })).toBeEnabled();
    expect(screen.queryByText(/角色页顶部/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AI 设计音色" })).not.toBeInTheDocument();
  });

  function emptySamples(extra = {}) {
    server.use(http.get("http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6/voice-samples", () => HttpResponse.json({ ok: true, data: { character: "秦", slots: [], ...extra } })));
  }

  it("explains missing legacy facts in Chinese without exposing raw unknown values", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "unknown", provenance: "unknown" } });
    renderPanel({ name: "秦", age_group: "youth" });
    expect(await screen.findByText("尚未核对")).toBeInTheDocument();
    expect(screen.getByText(/旧项目中的外观年龄不会自动当作声音事实/)).toBeInTheDocument();
    expect(screen.queryByText(/unknown/)).not.toBeInTheDocument();
  });

  it("presents source-confirmed creature sounds without human voice design fields", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "nonverbal", provenance: "source", species: "狸状异兽", evidence: ["核心伙伴，不说人话"] } });
    renderPanel({ name: "秦", age_group: "youth" });
    expect(await screen.findByText("原文已记载")).toBeInTheDocument();
    expect(screen.getAllByText("非语言发声").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("听感年龄")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("自定义声音描述")).not.toBeInTheDocument();
    expect(screen.getByText(/无需配置人类年龄声线/)).toBeInTheDocument();
  });

  it("resets unsaved facts and generation inputs when switching characters", async () => {
    emptySamples();
    let body: unknown;
    server.use(
      http.get("http://localhost:3000/api/v1/projects/demo/characters/B/voice-samples", () => HttpResponse.json({ ok: true, data: { character: "B", slots: [] } })),
      http.patch("http://localhost:3000/api/v1/projects/demo/characters/B", async ({ request }) => { body = await request.json(); return HttpResponse.json({ ok: true, data: {} }); }),
    );
    const view = renderPanel({ name: "秦" });
    fireEvent.change(screen.getByLabelText("声音特征"), { target: { value: "A 的声线事实" } });
    fireEvent.change(screen.getByLabelText("发声模式"), { target: { value: "dialogue" } });
    fireEvent.change(screen.getByLabelText("自定义声音描述"), { target: { value: "A 的描述" } });
    fireEvent.change(screen.getByLabelText("听感年龄"), { target: { value: "老年" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "A 的台词" } });
    view.rerender(<CharacterVoicePanel project="demo" character={{ name: "B" }} />);
    expect(screen.getByLabelText("声音特征")).toHaveValue("");
    expect(screen.getByLabelText("发声模式")).toHaveValue("unknown");
    expect(screen.getByLabelText("自定义声音描述")).toHaveValue("");
    expect(screen.getByLabelText("听感年龄")).toHaveValue("");
    expect(screen.getByLabelText("试听文本")).toHaveValue("");
    expect(screen.getByRole("button", { name: "保存已核对的事实" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("声音特征"), { target: { value: "B 的声线事实" } });
    fireEvent.click(screen.getByRole("button", { name: "保存已核对的事实" }));
    await waitFor(() => expect(body).toEqual({ voice_facts: { voice_traits: "B 的声线事实", conflicts: [] } }));
  });

  it("sends an independent perceived age as voice_spec without overwriting source facts", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "dialogue", age_group: "youth", provenance: "source" } });
    let body: unknown;
    server.use(http.post("http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6/voice-samples/default/design", async ({ request }) => { body = await request.json(); return HttpResponse.json({ task_id: "t1", scope: "default" }); }));
    renderPanel({ name: "秦" });
    fireEvent.change(await screen.findByLabelText("听感年龄"), { target: { value: "老年" } });
    fireEvent.change(screen.getByLabelText("自定义声音描述"), { target: { value: "低沉沙哑" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "你好。" } });
    await screen.findByText("青年（默认 · 必填）");
    const buttons = screen.getAllByRole("button", { name: "AI 设计音色" });
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(body).toEqual({ voice_spec: { age_impression: "老年", texture: "低沉沙哑" }, audition_text: "你好。" }));
    expect(screen.getByLabelText("事实年龄")).toHaveValue("youth");
  });

  it("keeps approved publication visible beside unavailable QC and prevents repeat approval", async () => {
    emptySamples({ candidates: [{ candidate_id: "c1", slot: "default", status: "approved", url: "/candidate.wav", report: { status: "qc_unavailable", reason: "评估器不可用" } }] });
    renderPanel({ name: "秦" });
    expect(await screen.findByText(/已采用/)).toBeInTheDocument();
    expect(screen.getByText(/质检不可用/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("采用理由"), { target: { value: "重复发布" } });
    fireEvent.click(screen.getByLabelText("我已试听，确认适合此角色"));
    expect(screen.getByRole("button", { name: "采用为角色声音" })).toBeDisabled();
  });

  it("requires explicit facts before generating a legacy character", async () => {
    emptySamples();
    renderPanel({ name: "秦" });
    expect(await screen.findByText("请先核对发声事实，再生成人声候选。" )).toBeInTheDocument();
    expect(screen.getByLabelText("发声模式")).toHaveValue("unknown");
    for (const button of await screen.findAllByRole("button", { name: "AI 设计音色" })) expect(button).toBeDisabled();
  });

  it.each(["nonverbal", "none"])("hides human generation for %s", async (mode) => {
    emptySamples({ voice_facts: { vocalization_mode: mode } });
    renderPanel({ name: "秦" });
    expect(await screen.findByText(/无需配置人类年龄声线/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AI 设计音色" })).not.toBeInTheDocument();
    expect(screen.queryByText("幼年（可选覆盖）")).not.toBeInTheDocument();
  });

  it("sends custom description and audition text for the elder slot", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "dialogue", conflicts: [] } });
    let body: unknown;
    server.use(http.post("http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6/voice-samples/elder/design", async ({ request }) => { body = await request.json(); return HttpResponse.json({ task_id: "t1", scope: "elder" }); }));
    renderPanel({ name: "秦" });
    fireEvent.change(await screen.findByLabelText("自定义声音描述"), { target: { value: "苍老沙哑" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "孩子，别怕。" } });
    fireEvent.click(screen.getByText("不同年龄的声音（可选）"));
    const buttons = await screen.findAllByRole("button", { name: "AI 设计音色" });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(body).toEqual({ voice_description: "苍老沙哑", audition_text: "孩子，别怕。" }));
  });

  it("shows unavailable QC honestly and requires listening confirmation before publication", async () => {
    emptySamples({ candidates: [{ candidate_id: "c1", slot: "default", status: "qc_unavailable", url: "/candidate.wav", payload: { voice_description: "苍老沙哑" }, report: { status: "qc_unavailable", reason: "评估器不可用" } }] });
    renderPanel({ name: "秦" });
    expect(await screen.findByText(/质检不可用/)).toBeInTheDocument();
    expect(screen.getByText("苍老沙哑")).toBeInTheDocument();
    expect(screen.getByText("评估器不可用")).toBeInTheDocument();
    expect(screen.getByLabelText("我已试听，确认适合此角色")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "采用为角色声音" })).toBeDisabled();
  });

  it("saves explicitly checked facts and publishes only with a reason and confirmation", async () => {
    emptySamples({ candidates: [{ candidate_id: "c1", slot: "default", status: "qc_unavailable", url: "/candidate.wav" }] });
    let factsBody: unknown;
    let approvalBody: unknown;
    server.use(
      http.patch("http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6", async ({ request }) => { factsBody = await request.json(); return HttpResponse.json({ ok: true, data: {} }); }),
      http.post("http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6/voice-candidates/c1/approve", async ({ request }) => { approvalBody = await request.json(); return HttpResponse.json({ ok: true, data: {} }); }),
    );
    renderPanel({ name: "秦" });
    await screen.findByRole("button", { name: "采用为角色声音" });
    fireEvent.change(screen.getByLabelText("发声模式"), { target: { value: "dialogue" } });
    fireEvent.change(screen.getByLabelText("声音特征"), { target: { value: "苍老沙哑" } });
    fireEvent.click(screen.getByRole("button", { name: "保存已核对的事实" }));
    await waitFor(() => expect(factsBody).toMatchObject({ voice_facts: { vocalization_mode: "dialogue", voice_traits: "苍老沙哑" } }));
    fireEvent.change(screen.getByLabelText("采用理由"), { target: { value: "试听符合老人特征" } });
    expect(screen.getByRole("button", { name: "采用为角色声音" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText("我已试听，确认适合此角色"));
    await waitFor(() => expect(screen.getByRole("button", { name: "采用为角色声音" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "采用为角色声音" }));
    await waitFor(() => expect(approvalBody).toEqual({ reason: "试听符合老人特征", confirm: true }));
  });

  it("blocks conflicting facts even when dialogue is known", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "dialogue", conflicts: ["age_group: child vs elder"] } });
    renderPanel({ name: "秦" });
    expect(await screen.findByRole("alert")).toHaveTextContent("age_group: child vs elder");
    expect(screen.getByRole("button", { name: "AI 设计音色" })).toBeDisabled();
  });

  it("uses source voice age instead of legacy appearance age", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "dialogue", age_group: "elder", provenance: "source" } });
    renderPanel({ name: "秦", age_group: "youth" });
    expect(await screen.findByText("老年（默认 · 必填）")).toBeInTheDocument();
    expect(screen.getByText("青年（可选覆盖）")).toBeInTheDocument();
  });
  it("renders default and age voice slots with inherited status and actions", async () => {
    server.use(
      http.get(
        "http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6/voice-samples",
        () =>
          HttpResponse.json({
            ok: true,
            data: {
              character: "秦",
              slots: [
                {
                  slot: "default",
                  label: "默认（兜底）",
                  path: "assets/characters/秦/voices/voice_default.wav",
                  url: "/static/admin/demo/assets/characters/秦/voices/voice_default.wav",
                  sha256: "sha-default",
                  updated_at: "2026-05-13T00:00:00+00:00",
                  inherited_from_default: false,
                  required: true,
                },
                {
                  slot: "child",
                  label: "幼年",
                  path: "",
                  url: "",
                  sha256: "",
                  updated_at: "",
                  inherited_from_default: true,
                  required: false,
                },
                {
                  slot: "youth",
                  label: "青年",
                  path: "",
                  url: "",
                  sha256: "",
                  updated_at: "",
                  inherited_from_default: true,
                  required: false,
                },
                {
                  slot: "middle",
                  label: "中年",
                  path: "",
                  url: "",
                  sha256: "",
                  updated_at: "",
                  inherited_from_default: true,
                  required: false,
                },
                {
                  slot: "elder",
                  label: "老年",
                  path: "assets/characters/秦/voices/voice_elder.wav",
                  url: "/static/admin/demo/assets/characters/秦/voices/voice_elder.wav",
                  sha256: "sha-elder",
                  updated_at: "2026-05-13T00:00:01+00:00",
                  inherited_from_default: false,
                  required: false,
                },
              ],
            },
          }),
      ),
    );

    const { container } = renderPanel({ name: "秦", age_group: "youth" });

    expect(await screen.findByText("角色声音")).toBeInTheDocument();
    expect(await screen.findByText("青年（默认 · 必填）")).toBeInTheDocument();
    expect(screen.getByText("幼年（可选覆盖）")).toBeInTheDocument();
    expect(screen.getAllByText("→ 继承默认").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("voice_elder.wav")).toHaveAttribute(
      "title",
      "assets/characters/秦/voices/voice_elder.wav",
    );
    fireEvent.click(screen.getByText("不同年龄的声音（可选）"));
    expect(screen.getAllByRole("button", { name: "上传声音样本" })).toHaveLength(4);
    expect(screen.getAllByRole("button", { name: "录音" })).toHaveLength(4);
    expect(screen.getAllByRole("button", { name: "AI 设计音色" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "裁剪到 3-5 秒" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "清除" })).toHaveLength(2);

    const audio = await waitFor(() => {
      const el = container.querySelector("audio");
      expect(el).not.toBeNull();
      return el as HTMLAudioElement;
    });
    Object.defineProperty(audio, "duration", { value: 6.5, configurable: true });
    fireEvent.loadedMetadata(audio);
    expect(await screen.findByText("当前约 6.5 秒")).toBeInTheDocument();
  });

  it("warns when the default voice is missing", async () => {
    server.use(
      http.get(
        "http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6/voice-samples",
        () =>
          HttpResponse.json({
            ok: true,
            data: {
              character: "秦",
              slots: [
                {
                  slot: "default",
                  label: "默认（兜底）",
                  path: "",
                  url: "",
                  sha256: "",
                  updated_at: "",
                  inherited_from_default: false,
                  required: true,
                },
              ],
            },
          }),
      ),
    );

    renderPanel({ name: "秦" });

    await waitFor(() =>
      expect(screen.getByText("未配置 → 角色将无法出声")).toBeInTheDocument(),
    );
  });

  it("shows an error instead of crashing when the voice API returns ok false", async () => {
    server.use(
      http.get(
        "http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6/voice-samples",
        () =>
          HttpResponse.json({
            ok: false,
            error: "Character '秦' not found",
          }),
      ),
    );

    renderPanel({ name: "秦" });

    expect(await screen.findByText("读取声线样本失败")).toBeInTheDocument();
  });
});
