// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { delay, http, HttpResponse } from "msw";
import { server } from "@/__mocks__/msw/server";
import ky from "ky";
import { toast } from "sonner";
import { useState, type ReactNode } from "react";
import { beforeEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/use-task-controller", () => ({
  useTaskController: () => ({ started: false, start: vi.fn(), stop: vi.fn(), stopping: false, stream: {}, logs: [] }),
}));

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { CharacterVoicePanel } from "@/components/assets/character-voice-panel";
import type { Character, CharacterVoiceDesign, CharacterVoiceRecommendation } from "@/types/character";

beforeEach(() => server.use(http.post("http://localhost:3000/api/v1/projects/:project/characters/:name/voice-recommendation", () =>
  HttpResponse.json({ ok: false, error: "推荐服务暂不可用" }, { status: 503 }))));
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
});

function wrapper({ children }: { children: ReactNode }) {
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
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

function enableVoiceDescriptionPreparation(
  texture: string,
  options: { recommendationDelayMs?: number; recommendation?: CharacterVoiceRecommendation } = {},
) {
  server.use(
    http.post("http://localhost:3000/api/v1/projects/demo/characters/:name/voice-recommendation", async ({ request }) => {
      const body = await request.json() as { adjustment?: string; design?: CharacterVoiceDesign };
      if (!body.adjustment) {
        if (options.recommendationDelayMs) await delay(options.recommendationDelayMs);
        return options.recommendation
          ? HttpResponse.json({ ok: true, data: options.recommendation })
          : HttpResponse.json({ ok: false, error: "推荐暂不可用" }, { status: 503 });
      }
      const base = body.design ?? { voice_spec: {}, performance: {} };
      return HttpResponse.json({ ok: true, data: {
        design: { ...base, voice_spec: { ...base.voice_spec, texture } },
        voice_description: `待编译声音：${texture}`, audition_text: "解析台词", audition_source: "example",
        rationale: "整理自由声音描述", source_digest: "draft-source", voice_facts: { vocalization_mode: "dialogue" },
      } });
    }),
    http.post("*/voice-samples/default/prepare", async ({ request }) => {
      const body = await request.json() as { audition_text: string; design: CharacterVoiceDesign; perceived_age?: string };
      const voiceSpec = { ...body.design.voice_spec, texture };
      if (body.perceived_age) voiceSpec.age_impression = body.perceived_age;
      const design = { ...body.design, voice_spec: voiceSpec, audition_text: body.audition_text };
      return HttpResponse.json({ ok: true, data: {
        design, voice_description: `已编译声音：${texture}`, audition_text: body.audition_text,
        voice_spec: voiceSpec, compiler_version: "test-compiler",
      } });
    }),
  );
}

describe("CharacterVoicePanel", () => {
  it("does not write an in-flight character A refinement after the selected node role switches to B", async () => {
    emptySamplesFor("异步角色A", { voice_facts: { vocalization_mode: "dialogue" } });
    emptySamplesFor("当前角色B", { voice_facts: { vocalization_mode: "dialogue" } });
    let releaseRefinement!: () => void;
    let signalRefinementStarted!: () => void;
    let signalRefinementFinished!: () => void;
    const refinementGate = new Promise<void>(resolve => { releaseRefinement = resolve; });
    const refinementStarted = new Promise<void>(resolve => { signalRefinementStarted = resolve; });
    const refinementFinished = new Promise<void>(resolve => { signalRefinementFinished = resolve; });
    const selected = { project: "demo", nodeId: "voice-node-1", character: "异步角色A" };
    const isCurrentRole = (name: string) => () => selected.project === "demo"
      && selected.nodeId === "voice-node-1"
      && selected.character === name;
    const onNodeDataChange = vi.fn();
    server.use(http.post("*/voice-recommendation", async ({ request }) => {
      const body = await request.json() as { adjustment?: string };
      if (!body.adjustment) return HttpResponse.json({ ok: false, error: "推荐暂不可用" }, { status: 503 });
      signalRefinementStarted();
      await refinementGate;
      signalRefinementFinished();
      return HttpResponse.json({ ok: true, data: {
        design: { voice_spec: { texture: "A 的迟到结果" }, performance: {} },
        voice_description: "A 的迟到结果", audition_text: "A 的迟到试听词", audition_source: "example",
        rationale: "异步测试", source_digest: "a", voice_facts: { vocalization_mode: "dialogue" },
      } });
    }));

    const view = render(
      <CharacterVoicePanel
        project="demo"
        character={{ name: "异步角色A" }}
        nodeVoiceDesign={{ voice_spec: {}, performance: {} }}
        isNodeContextCurrent={isCurrentRole("异步角色A")}
        onNodeDataChange={onNodeDataChange}
      />,
      { wrapper },
    );
    fireEvent.click(await screen.findByRole("button", { name: "重新拟定声音方案" }));
    await refinementStarted;

    selected.character = "当前角色B";
    view.rerender(
      <CharacterVoicePanel
        project="demo"
        character={{ name: "当前角色B" }}
        isNodeContextCurrent={isCurrentRole("当前角色B")}
        onNodeDataChange={onNodeDataChange}
      />,
    );
    await screen.findByRole("button", { name: "重新拟定声音方案" });
    releaseRefinement();
    await act(async () => {
      await refinementFinished;
      await new Promise(resolve => setTimeout(resolve, 20));
    });

    expect(onNodeDataChange).not.toHaveBeenCalledWith(expect.objectContaining({
      voiceDescription: "A 的迟到结果",
      auditionText: "A 的迟到试听词",
    }));
    expect(screen.queryByText("A 的迟到结果")).not.toBeInTheDocument();
  });

  it("shows the backend validation reason when voice compilation returns 422", async () => {
    const errorToast = vi.spyOn(toast, "error");
    emptySamples({ voice_facts: { vocalization_mode: "dialogue" } });
    enableVoiceDescriptionPreparation("老年沙哑");
    server.use(http.post("*/voice-samples/default/design", () =>
      HttpResponse.json({ ok: false, error: "声音年龄与所选槽位冲突" }, { status: 422 })));
    renderPanel({ name: "秦" });
    await waitFor(() => expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("自定义声音描述"), { target: { value: "老年男声" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "请跟我来。" } });
    fireEvent.click(screen.getByRole("button", { name: "解析并查看声线字段差异" }));
    fireEvent.click(await screen.findByRole("button", { name: "接受差异并重新编译" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "一键生成声音" }));
    await waitFor(() => expect(errorToast).toHaveBeenCalledWith("声音年龄与所选槽位冲突"));
    errorToast.mockRestore();
  });

  it("does not submit a recommendation if the character role changes while it is loading", async () => {
    emptySamplesFor("角色C", { voice_facts: { vocalization_mode: "dialogue" } });
    let releaseRecommendation!: () => void;
    let signalRecommendationStarted!: () => void;
    let submitted: unknown;
    const recommendationGate = new Promise<void>(resolve => { releaseRecommendation = resolve; });
    const recommendationStarted = new Promise<void>(resolve => { signalRecommendationStarted = resolve; });
    const errorToast = vi.spyOn(toast, "error");
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/characters/:name/voice-recommendation", async () => {
        signalRecommendationStarted();
        await recommendationGate;
        return HttpResponse.json({ ok: true, data: {
          voice_description: "学生身份声音", audition_text: "学生身份台词", audition_source: "dialogue",
          rationale: "按学生角色设定", source_digest: "old", voice_facts: { vocalization_mode: "dialogue" },
        } });
      }),
      http.post("*/voice-samples/default/design", async ({ request }) => {
        submitted = await request.json();
        return HttpResponse.json({ task_id: "t1", scope: "default" });
      }),
    );
    const view = renderPanel({ name: "角色C", role: "学生" });
    await waitFor(() => expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "一键生成声音" }));
    await recommendationStarted;
    view.rerender(<CharacterVoicePanel project="demo" character={{ name: "角色C", role: "教师" }} />);
    releaseRecommendation();
    await waitFor(() => expect(errorToast).toHaveBeenCalled());
    expect(submitted).toBeUndefined();
    expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled();
    errorToast.mockRestore();
  });

  it("allows one-step speaking confirmation and keeps advanced settings collapsed", async () => {
    emptySamples();
    let saved: unknown;
    server.use(http.patch("http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6", async ({ request }) => {
      saved = await request.json(); return HttpResponse.json({ ok: true });
    }));
    renderPanel({ name: "秦" });
    const confirm = await screen.findByRole("button", { name: "需要人声对白" });
    expect(screen.getByText("更多设置").closest("details")).not.toHaveAttribute("open");
    fireEvent.click(confirm);
    await waitFor(() => expect(saved).toMatchObject({ voice_facts: { vocalization_mode: "dialogue", provenance: "human" } }));
    await waitFor(() => expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled());
  });

  it("requires explicit acceptance before generating from a free voice description", async () => {
    emptySamplesFor("接受角色", { voice_facts: { vocalization_mode: "dialogue" } });
    enableVoiceDescriptionPreparation("温和清晰");
    let generated: unknown;
    server.use(http.post("*/voice-samples/default/design", async ({ request }) => {
      generated = await request.json();
      return HttpResponse.json({ task_id: "t1", scope: "default" });
    }));
    renderPanel({ name: "接受角色" });
    await waitFor(() => expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled());
    const generate = screen.getByRole("button", { name: "一键生成声音" });
    expect(generate).toBeEnabled();
    fireEvent.change(screen.getByLabelText("自定义声音描述"), { target: { value: "温和清晰" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "请跟我来。" } });
    expect(generate).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "解析并查看声线字段差异" }));
    fireEvent.click(await screen.findByRole("button", { name: "接受差异并重新编译" }));
    await waitFor(() => expect(generate).toBeEnabled());
    expect(screen.getByLabelText("自定义声音描述")).toHaveValue("");
    fireEvent.click(generate);
    await waitFor(() => expect(generated).toMatchObject({
      design: { voice_spec: { texture: "温和清晰" }, audition_text: "请跟我来。" },
      audition_text: "请跟我来。",
    }));
  });

  it("uses an AI recommendation and audition only after one-click generation", async () => {
    emptySamplesFor("推荐角色", { voice_facts: { vocalization_mode: "dialogue" } });
    let generated: unknown;
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/characters/:name/voice-recommendation", () => HttpResponse.json({ ok: true, data: {
        voice_description: "沉稳、克制，语速适中", audition_text: "先别急，把事情说清楚。",
        audition_source: "dialogue", rationale: "依据角色处理冲突时的对白", source_digest: "s1",
        voice_facts: { vocalization_mode: "dialogue" },
      } })),
      http.post("*/voice-samples/default/design", async ({ request }) => {
        generated = await request.json(); return HttpResponse.json({ ok: true, scope: "voice" });
      }),
    );
    renderPanel({ name: "推荐角色" });
    await waitFor(() => expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(generated).toBeUndefined();
    fireEvent.click(screen.getByRole("button", { name: "一键生成声音" }));
    await waitFor(() => expect(generated).toMatchObject({
      voice_description: "沉稳、克制，语速适中", audition_text: "先别急，把事情说清楚。",
    }));
  });

  it("preserves a free description while a slow recommendation arrives and requires acceptance", async () => {
    emptySamplesFor("慢速角色", { voice_facts: { vocalization_mode: "dialogue" } });
    let submitted: unknown;
    const errorToast = vi.spyOn(toast, "error");
    server.use(http.post("*/voice-samples/default/design", async ({ request }) => {
      submitted = await request.json(); return HttpResponse.json({ ok: true, scope: "default" });
    }));
    enableVoiceDescriptionPreparation("结构化用户声线", {
      recommendationDelayMs: 100,
      recommendation: {
        design: { voice_spec: { texture: "AI 音色" }, performance: {} },
        voice_description: "AI 建议", audition_text: "例句", audition_source: "example",
        rationale: "创作建议", source_digest: "s1", voice_facts: { vocalization_mode: "dialogue" },
      },
    });
    renderPanel({ name: "慢速角色" });
    await waitFor(() => expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "一键生成声音" }));
    fireEvent.change(screen.getByLabelText("自定义声音描述"), { target: { value: "用户的声线" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "用户的台词" } });
    expect(screen.getByLabelText("自定义声音描述")).toHaveValue("用户的声线");
    expect(screen.getByLabelText("试听文本")).toHaveValue("用户的台词");
    await waitFor(() => expect(errorToast).toHaveBeenCalledWith("请先审阅并接受自由描述对应的结构化声线差异"));
    expect(submitted).toBeUndefined();
    fireEvent.click(screen.getByRole("button", { name: "解析并查看声线字段差异" }));
    fireEvent.click(await screen.findByRole("button", { name: "接受差异并重新编译" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled());
    expect(screen.getByLabelText("自定义声音描述")).toHaveValue("");
    errorToast.mockRestore();
  });

  it.each(["local", "external"] as const)("does not accept a delayed compile after %s voice draft changes", async changeSource => {
    const characterName = changeSource === "local" ? "本地延迟测试" : "外部延迟测试";
    emptySamplesFor(characterName, { voice_facts: { vocalization_mode: "dialogue" } });
    let releasePrepare!: () => void;
    let signalPrepareStarted!: () => void;
    let signalPrepareResponse!: () => void;
    let prepareBody: unknown;
    const prepareGate = new Promise<void>(resolve => { releasePrepare = resolve; });
    const prepareStarted = new Promise<void>(resolve => { signalPrepareStarted = resolve; });
    const prepareResponseSent = new Promise<void>(resolve => { signalPrepareResponse = resolve; });
    const onNodeDataChange = vi.fn();
    server.use(
      http.post("*/voice-recommendation", async ({ request }) => {
        const body = await request.json() as { adjustment?: string };
        if (!body.adjustment) return HttpResponse.json({ ok: false, error: "推荐暂不可用" }, { status: 503 });
        return HttpResponse.json({ ok: true, data: {
          design: { voice_spec: { texture: "待编译音色" }, performance: {}, audition_text: "解析台词", audition_source: "example" },
          voice_description: "待编译声音", audition_text: "解析台词", audition_source: "example",
          rationale: "将草案整理为声线字段", source_digest: "s1", voice_facts: { vocalization_mode: "dialogue" },
        } });
      }),
      http.post("*/voice-samples/default/prepare", async ({ request }) => {
        prepareBody = await request.json();
        signalPrepareStarted();
        await prepareGate;
        signalPrepareResponse();
        return HttpResponse.json({ ok: true, data: {
          design: { voice_spec: { texture: "过期编译音色" }, performance: {}, audition_text: "过期编译台词", audition_source: "custom" },
          voice_description: "过期编译描述", audition_text: "过期编译台词",
          voice_spec: { texture: "过期编译音色" }, compiler_version: "test-compiler",
        } });
      }),
    );
    const view = render(
      <CharacterVoicePanel project="demo" character={{ name: characterName }} nodeVoiceDesign={{ voice_spec: {}, performance: {} }} onNodeDataChange={onNodeDataChange} />,
      { wrapper },
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("自定义声音描述"), { target: { value: "待解析的声音描述" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "编译时试听文本" } });
    fireEvent.click(screen.getByRole("button", { name: "解析并查看声线字段差异" }));
    fireEvent.click(await screen.findByRole("button", { name: "接受差异并重新编译" }));
    await prepareStarted;
    expect(prepareBody).toMatchObject({ audition_text: "编译时试听文本", design: { voice_spec: { texture: "待编译音色" } } });

    const expectedDraft = changeSource === "external" ? "外部最新的声音描述" : "用户最新的声音描述";
    const expectedAudition = changeSource === "external" ? "外部最新的试听文本" : "用户最新的试听文本";
    if (changeSource === "local") {
      fireEvent.change(screen.getByLabelText("自定义声音描述"), { target: { value: expectedDraft } });
      fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: expectedAudition } });
    } else {
      view.rerender(
        <CharacterVoicePanel
          project="demo"
          character={{ name: characterName }}
          nodeVoiceDesign={{ voice_spec: { texture: "外部新声线" }, performance: {} }}
          nodeVoiceDescriptionDraft={expectedDraft}
          nodeAuditionText={expectedAudition}
          onNodeDataChange={onNodeDataChange}
        />,
      );
    }
    await waitFor(() => {
      expect(screen.getByLabelText("自定义声音描述")).toHaveValue(expectedDraft);
      expect(screen.getByLabelText("试听文本")).toHaveValue(expectedAudition);
    });
    const callsBeforeResponse = onNodeDataChange.mock.calls.length;
    releasePrepare();
    await act(async () => {
      await prepareResponseSent;
      await new Promise(resolve => setTimeout(resolve, 20));
    });
    expect(screen.getByLabelText("自定义声音描述")).toHaveValue(expectedDraft);
    expect(screen.getByLabelText("试听文本")).toHaveValue(expectedAudition);
    expect(onNodeDataChange).toHaveBeenCalledTimes(callsBeforeResponse);
  });

  it("keeps the current voice before generation and facts, with candidates adopted locally", async () => {
    let recommendations = 0;
    let audioRequests = 0;
    server.use(
      http.post("*/voice-recommendation", () => { recommendations += 1; return HttpResponse.json({ ok: false, error: "unavailable" }); }),
      http.post("*/voice-samples/default/design", () => { audioRequests += 1; return HttpResponse.json({ ok: true }); }),
    );
    emptySamples({ voice_facts: { vocalization_mode: "dialogue", age_group: "youth" }, candidates: [{ candidate_id: "c1", slot: "default", status: "qc_unavailable", url: "/candidate.wav" }] });
    const { container } = renderPanel({ name: "秦" });
    await screen.findByRole("button", { name: "采用为角色声音" });
    expect(screen.queryByRole("button", { name: "一键生成声音" })).not.toBeInTheDocument();
    expect(recommendations).toBe(0);
    expect(audioRequests).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "设计新的声音" }));
    expect(recommendations).toBe(0);
    expect(audioRequests).toBe(0);
    const current = screen.getByRole("heading", { name: "当前声音" });
    const generation = screen.getByRole("heading", { name: "为这个角色推荐的声音" });
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
    emptySamplesFor("秦", extra);
  }

  function emptySamplesFor(character: string, extra = {}) {
    server.use(http.get(`http://localhost:3000/api/v1/projects/demo/characters/${encodeURIComponent(character)}/voice-samples`, () => HttpResponse.json({ ok: true, data: { character, slots: [], ...extra } })));
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
    emptySamplesFor("角色A");
    let body: unknown;
    server.use(
      http.get("http://localhost:3000/api/v1/projects/demo/characters/B/voice-samples", () => HttpResponse.json({ ok: true, data: { character: "B", slots: [] } })),
      http.patch("http://localhost:3000/api/v1/projects/demo/characters/B", async ({ request }) => { body = await request.json(); return HttpResponse.json({ ok: true, data: {} }); }),
    );
    const view = renderPanel({ name: "角色A" });
    fireEvent.change(screen.getByLabelText("声音特征"), { target: { value: "A 的声线事实" } });
    fireEvent.change(screen.getByLabelText("发声模式"), { target: { value: "dialogue" } });
    fireEvent.change(screen.getByLabelText("自定义声音描述"), { target: { value: "A 的描述" } });
    fireEvent.change(screen.getByLabelText("听感年龄"), { target: { value: "老年" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "A 的台词" } });
    view.rerender(<CharacterVoicePanel project="demo" character={{ name: "B" }} />);
    const visibleInput = (label: string) => screen.getAllByLabelText(label).find(input => !input.closest("[hidden]"))!;
    expect(visibleInput("声音特征")).toHaveValue("");
    expect(visibleInput("发声模式")).toHaveValue("unknown");
    expect(visibleInput("自定义声音描述")).toHaveValue("");
    expect(visibleInput("听感年龄")).toHaveValue("");
    expect(visibleInput("试听文本")).toHaveValue("");
    expect(screen.getByRole("button", { name: "保存已核对的事实" })).toBeDisabled();
    fireEvent.change(visibleInput("声音特征"), { target: { value: "B 的声线事实" } });
    fireEvent.click(screen.getByRole("button", { name: "保存已核对的事实" }));
    await waitFor(() => expect(body).toEqual({ voice_facts: { voice_traits: "B 的声线事实", conflicts: [] } }));
  });

  it("sends an independent perceived age as voice_spec without overwriting source facts", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "dialogue", age_group: "youth", provenance: "source" } });
    let body: unknown;
    enableVoiceDescriptionPreparation("低沉沙哑");
    server.use(http.post("http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6/voice-samples/default/design", async ({ request }) => { body = await request.json(); return HttpResponse.json({ task_id: "t1", scope: "default" }); }));
    renderPanel({ name: "秦" });
    fireEvent.change(await screen.findByLabelText("听感年龄"), { target: { value: "老年" } });
    fireEvent.change(screen.getByLabelText("自定义声音描述"), { target: { value: "低沉沙哑" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "你好。" } });
    fireEvent.click(screen.getByRole("button", { name: "解析并查看声线字段差异" }));
    fireEvent.click(await screen.findByRole("button", { name: "接受差异并重新编译" }));
    await screen.findByText("青年（默认 · 必填）");
    const generate = screen.getByRole("button", { name: "一键生成声音" });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await waitFor(() => expect(body).toMatchObject({
      design: { voice_spec: { age_impression: "老年", texture: "低沉沙哑" }, audition_text: "你好。" },
      perceived_age: "老年",
      audition_text: "你好。",
    }));
    expect(screen.getByLabelText("事实年龄")).toHaveValue("youth");
  });

  it("keeps approved publication visible beside unavailable QC and requires confirmation to restore", async () => {
    emptySamples({ candidates: [{ candidate_id: "c1", slot: "default", status: "approved", url: "/candidate.wav", report: { status: "qc_unavailable", reason: "评估器不可用" } }] });
    renderPanel({ name: "秦" });
    expect(await screen.findByText(/历史采用/)).toBeInTheDocument();
    expect(screen.getByText(/质检不可用/)).toBeInTheDocument();
    const restore = screen.getByRole("button", { name: "恢复为角色声音" });
    expect(restore).toBeDisabled();
    fireEvent.change(screen.getByLabelText("采用理由"), { target: { value: "重复发布" } });
    fireEvent.click(screen.getByLabelText("我已试听，确认适合此角色"));
    expect(restore).toBeEnabled();
  });

  it("requires explicit facts before generating a legacy character", async () => {
    emptySamples();
    renderPanel({ name: "秦" });
    expect(await screen.findByText("资料中尚未确认：这个角色需要人声对白吗？")).toBeInTheDocument();
    expect(screen.getByLabelText("发声模式")).toHaveValue("unknown");
    expect(screen.getByRole("button", { name: "一键生成声音" })).toBeEnabled();
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
    enableVoiceDescriptionPreparation("苍老沙哑");
    server.use(http.post("http://localhost:3000/api/v1/projects/demo/characters/%E7%A7%A6/voice-samples/elder/design", async ({ request }) => { body = await request.json(); return HttpResponse.json({ task_id: "t1", scope: "elder" }); }));
    renderPanel({ name: "秦" });
    fireEvent.change(await screen.findByLabelText("自定义声音描述"), { target: { value: "苍老沙哑" } });
    fireEvent.change(screen.getByLabelText("试听文本"), { target: { value: "孩子，别怕。" } });
    fireEvent.click(screen.getByRole("button", { name: "解析并查看声线字段差异" }));
    fireEvent.click(await screen.findByRole("button", { name: "接受差异并重新编译" }));
    fireEvent.click(screen.getByText("不同年龄的声音（可选）"));
    const buttons = await screen.findAllByRole("button", { name: "AI 设计音色" });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(body).toMatchObject({
      design: { voice_spec: { texture: "苍老沙哑" }, audition_text: "孩子，别怕。" },
      audition_text: "孩子，别怕。",
    }));
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
    await waitFor(() => expect(approvalBody).toEqual({
      reason: "试听符合老人特征",
      confirm: true,
      expected_reference: { path: "", sha256: "" },
    }));
  });

  it("blocks conflicting facts even when dialogue is known", async () => {
    emptySamples({ voice_facts: { vocalization_mode: "dialogue", conflicts: ["age_group: child vs elder"] } });
    renderPanel({ name: "秦" });
    expect(await screen.findByRole("alert")).toHaveTextContent("age_group: child vs elder");
    expect(screen.getByRole("button", { name: "一键生成声音" })).toBeDisabled();
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
    expect(screen.getByRole("button", { name: "设计新的声音" })).toBeInTheDocument();
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
      expect(screen.getByText("尚未设置声音，试听满意后即可采用")).toBeInTheDocument(),
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
