// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReactFlowProvider } from "@xyflow/react";

import { CANVAS_NODE_TYPES, type AgentNodeData } from "@/features/canvas/domain/canvasNodes";
import { canvasNodeDefinitions } from "@/features/canvas/domain/nodeRegistry";
import { CanvasAddNodePanel } from "@/features/canvas/ui/CanvasAddNodePanel";
import { AgentNode } from "@/features/canvas/nodes/AgentNode";
import { registerFreezoneCanvasRuntime } from "@/features/freezone/canvasSyncRuntime";
import type { ExternalCanvasOperation } from "@/features/freezone/externalCanvasOperation";
import { useCanvasStore } from "@/stores/canvasStore";
import enTranslations from "../../../../public/locales/en/translation.json";
import zhTranslations from "../../../../public/locales/zh/translation.json";

const agentRuntimeHarness = vi.hoisted(() => ({
  saveSettings: undefined as (() => Promise<boolean>) | undefined,
  runtime: {
    nodes: [],
    loading: false,
    maxModelCalls: 1,
    setMaxModelCalls: () => undefined,
    working: false,
    generate: async () => true,
    runtimeStatus: null,
    pendingIntent: null,
    pendingWrite: null,
    incoming: { refs: [], unadoptedAgentNodeIds: [] },
    error: "",
    notice: "",
    conversationId: null,
    history: [],
    refresh: async () => undefined,
    resumePending: async () => true,
    recoverCandidateWrite: async () => true,
    isStale: () => false,
    stopRun: async () => true,
    resumeRun: async () => true,
    saveCandidateEdit: async () => true,
    adoptCandidate: async () => true,
  },
}));

vi.mock("@/features/ideation/useAgentNodeRuntime", () => ({
  useAgentNodeRuntime: ({ saveSettings }: { saveSettings: () => Promise<boolean> }) => {
    agentRuntimeHarness.saveSettings = saveSettings;
    return agentRuntimeHarness.runtime;
  },
}));

const translations: Record<string, string> = {
  "node.menu.sectionAddNode": "添加节点",
  "node.menu.sectionSkillNode": "技能节点",
  "node.menu.agent": "Agent",
  "node.menu.agentOptions": "选择 Agent",
  "node.menu.agentCreativeLead": "创意主创",
  "node.menu.agentCreativeLeadDescription": "统筹故事方向与创作方案",
  "node.menu.agentTextWriter": "文本编剧",
  "node.menu.agentTextWriterDescription": "撰写与完善剧本文本",
  "node.menu.agentIdeation": "立意",
  "node.menu.agentIdeationDescription": "确定主题材并形成故事种子",
  "canvas.agentNode.primaryGenre": "主题材",
  "canvas.agentNode.fusionGenre": "融合题材",
  "canvas.agentNode.storySeed": "故事种子",
  "canvas.agentNode.storySeedPlaceholder": "一句话，或一段自由的想象",
  "canvas.agentNode.summary": "当前设定",
  "canvas.agentNode.editSettings": "编辑完整设定",
  "canvas.agentNode.noFusion": "不融合",
  "canvas.agentNode.unspecified": "暂未指定",
  "canvas.agentNode.saveFailure": "保存失败，请重试",
  "node.menu.beatContext": "镜头上下文",
};

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => translations[key] ?? key }),
}));

describe("Agent canvas node", () => {
  let unregisterRuntime: (() => void) | null = null;

  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
  });

  afterEach(() => {
    unregisterRuntime?.();
    unregisterRuntime = null;
    vi.unstubAllGlobals();
  });

  function renderAgent(flush: () => Promise<boolean> = async () => true) {
    const data = canvasNodeDefinitions[CANVAS_NODE_TYPES.agent].createDefaultData() as AgentNodeData;
    useCanvasStore.getState().setCanvasData([{
      id: "agent-1",
      type: CANVAS_NODE_TYPES.agent,
      position: { x: 120, y: 240 },
      data,
    }], []);
    const operation = {
      capture: vi.fn(),
      resolveServer: vi.fn(async () => ({ status: "applied" as const })),
      dispose: vi.fn(),
      run: vi.fn(async () => ({ status: "applied" as const })),
    } as unknown as ExternalCanvasOperation;
    unregisterRuntime = registerFreezoneCanvasRuntime(
      "project-1", "canvas-1", () => {}, flush, undefined, undefined, operation,
    );
    const props = { id: "agent-1", data, selected: false } as ComponentProps<typeof AgentNode>;
    return { data, view: render(<ReactFlowProvider><AgentNode {...props} /></ReactFlowProvider>) };
  }

  it.each([
    ["zh", zhTranslations],
    ["en", enTranslations],
  ])("places Agent node copy on the canvas namespace in the %s locale", (_locale, translation) => {
    const copy = translation.canvas.agentNode as Record<string, unknown>;
    expect(copy).toMatchObject({
      primaryGenre: expect.any(String),
      fusionGenre: expect.any(String),
      storySeed: expect.any(String),
      editSettings: expect.any(String),
      runtimeTitle: expect.any(String),
      runtimeRefresh: expect.any(String),
      runtimeRefreshing: expect.any(String),
      runtimeCallLimit: expect.any(String),
      runtimeWorking: expect.any(String),
      runtimeGenerate: expect.any(String),
      runtimeUnavailable: expect.any(String),
      runtimePending: expect.any(String),
      runtimeRestore: expect.any(String),
      runtimeWritePending: expect.any(String),
      runtimeUnadopted: expect.any(String),
      runtimeInputs: expect.any(String),
      runtimeHistory: expect.any(String),
      runtimeNoResults: expect.any(String),
      runtimeNoConversation: expect.any(String),
      runtimeStale: expect.any(String),
      runtimeInvalidCandidates: expect.any(String),
      runtimeRawProposal: expect.any(String),
      runtimeSource: expect.any(String),
      runtimeCompareEdit: expect.any(String),
      runtimeProposal: expect.any(String),
      runtimeStop: expect.any(String),
      runtimeResume: expect.any(String),
      runtimeCandidate: expect.any(String),
      runtimeCandidateCompare: expect.any(String),
      runtimeEdit: expect.any(String),
      runtimeSaveVersion: expect.any(String),
      runtimeAdopted: expect.any(String),
      runtimeAdopt: expect.any(String),
    });
  });

  it("offers the three builtin roles through one agent node type", async () => {
    const user = userEvent.setup();
    const onSelectNode = vi.fn();
    render(
      <CanvasAddNodePanel
        skillItems={[]}
        onSelectNode={onSelectNode}
        onSelectSkill={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Agent" }));
    const creativeLead = screen.getByRole("button", { name: /创意主创.*统筹故事方向与创作方案/ });
    const textWriter = screen.getByRole("button", { name: /文本编剧.*撰写与完善剧本文本/ });
    const ideation = screen.getByRole("button", { name: /立意.*确定主题材并形成故事种子/ });
    expect(creativeLead).toBeInTheDocument();
    expect(textWriter).toBeInTheDocument();
    expect(ideation).toBeInTheDocument();

    await user.click(creativeLead);
    await user.click(textWriter);
    await user.click(ideation);

    expect(onSelectNode).toHaveBeenNthCalledWith(1, CANVAS_NODE_TYPES.agent, expect.objectContaining({
      agentRef: { id: "builtin:creative-lead", revision: 1 }, displayName: "创意主创",
    }));
    expect(onSelectNode).toHaveBeenNthCalledWith(2, CANVAS_NODE_TYPES.agent, expect.objectContaining({
      agentRef: { id: "builtin:text-writer", revision: 1 }, displayName: "文本编剧",
    }));
    expect(onSelectNode).toHaveBeenNthCalledWith(3, CANVAS_NODE_TYPES.agent, expect.objectContaining({
      agentRef: { id: "builtin:ideation", revision: 1 }, displayName: "立意",
    }));
  });

  it("creates fresh settings per node and retains agent data through canvas hydration", () => {
    const definition = canvasNodeDefinitions[CANVAS_NODE_TYPES.agent];
    const first = definition.createDefaultData() as AgentNodeData;
    const second = definition.createDefaultData() as AgentNodeData;

    first.settings.roles.push("侦探");
    expect(second.settings.roles).toEqual([]);
    expect(first.agentRef).toEqual({ id: "builtin:ideation", revision: 1 });

    useCanvasStore.getState().hydrateCanvasDraft({
      nodes: [{
        id: "agent-1",
        type: CANVAS_NODE_TYPES.agent,
        position: { x: 120, y: 240 },
        data: {
          agentRef: { id: "builtin:text-writer", revision: 1 },
          settings: second.settings,
          conversationId: "conversation-1",
          runRefs: [{ id: "run-1", revision: 2, state: "running" }],
          adoptedRef: {
            kind: "ideation_selection",
            request_id: "request-1",
            canvas_id: "canvas-1",
            conversation_id: "conversation-1",
            node_id: "agent-1",
            object_id: "object-1",
            revision: 1,
            candidate_id: "candidate-1",
          },
        },
      }],
      edges: [],
      history: { past: [], future: [] },
      mutation: { userEditsSinceHydrate: 0, lastMutationSource: null, pendingClearIntent: false },
    });

    expect(useCanvasStore.getState().nodes).toEqual([
      expect.objectContaining({
        id: "agent-1",
        type: CANVAS_NODE_TYPES.agent,
        data: expect.objectContaining({
          agentRef: { id: "builtin:text-writer", revision: 1 },
          conversationId: "conversation-1",
          runRefs: [{ id: "run-1", revision: 2, state: "running" }],
          adoptedRef: {
            kind: "ideation_selection",
            request_id: "request-1",
            canvas_id: "canvas-1",
            conversation_id: "conversation-1",
            node_id: "agent-1",
            object_id: "object-1",
            revision: 1,
            candidate_id: "candidate-1",
          },
        }),
      }),
    ]);
  });

  it("keeps direct ideation fields synchronized and marks inputs as non-draggable", async () => {
    const flush = vi.fn().mockResolvedValue(true);
    renderAgent(flush);

    const genre = screen.getByRole("combobox", { name: "主题材" });
    expect(genre).toHaveClass("nodrag", "nowheel");
    fireEvent.change(genre, { target: { value: "悬疑推理" } });
    expect(screen.getByTestId("agent-settings-summary")).toHaveTextContent("都市现实");
    fireEvent.blur(genre);

    await waitFor(() => expect(flush).toHaveBeenCalledTimes(1));
    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({
        settings: expect.objectContaining({ genrePrimary: "悬疑推理" }),
      }),
    );
    expect(screen.getByTestId("agent-settings-summary")).toHaveTextContent("悬疑推理");
  });

  it("mounts runtime controls and requires confirmed settings before a run", async () => {
    const flush = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    renderAgent(flush);

    expect(screen.getByRole("button", { name: "canvas.agentNode.runtimeGenerate" })).toBeInTheDocument();
    const genre = screen.getByRole("combobox", { name: "主题材" });
    fireEvent.change(genre, { target: { value: "悬疑推理" } });

    expect(agentRuntimeHarness.saveSettings).toBeTypeOf("function");
    await expect(agentRuntimeHarness.saveSettings!()).resolves.toBe(false);
    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({ settings: expect.objectContaining({ genrePrimary: "都市现实" }) }),
    );

    await expect(agentRuntimeHarness.saveSettings!()).resolves.toBe(true);
    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({ settings: expect.objectContaining({ genrePrimary: "悬疑推理" }) }),
    );
  });

  it("keeps an inline draft retryable and the summary confirmed after flush failure", async () => {
    const flush = vi.fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    const { data } = renderAgent(flush);
    const genre = screen.getByRole("combobox", { name: "主题材" });

    fireEvent.change(genre, { target: { value: "悬疑推理" } });
    fireEvent.blur(genre);
    await waitFor(() => expect(flush).toHaveBeenCalledTimes(1));

    expect(genre).toHaveValue("悬疑推理");
    expect(screen.getByTestId("agent-settings-summary")).toHaveTextContent("都市现实");
    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({ settings: data.settings }),
    );

    fireEvent.focus(genre);
    fireEvent.blur(genre);
    await waitFor(() => expect(flush).toHaveBeenCalledTimes(2));

    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({ settings: expect.objectContaining({ genrePrimary: "悬疑推理" }) }),
    );
    expect(screen.getByTestId("agent-settings-summary")).toHaveTextContent("悬疑推理");
  });

  it("does not let an older inline success overwrite the latest confirmed summary", async () => {
    const pendingFlushes: Array<(result: boolean) => void> = [];
    const flush = vi.fn(() => new Promise<boolean>((resolve) => pendingFlushes.push(resolve)));
    renderAgent(flush);
    const genre = screen.getByRole("combobox", { name: "主题材" });
    const summary = screen.getByTestId("agent-settings-summary");

    fireEvent.change(genre, { target: { value: "悬疑推理" } });
    fireEvent.blur(genre);
    await waitFor(() => expect(flush).toHaveBeenCalledTimes(1));

    fireEvent.change(genre, { target: { value: "末世科幻" } });
    fireEvent.blur(genre);
    await waitFor(() => expect(flush).toHaveBeenCalledTimes(2));

    pendingFlushes[1](true);
    await waitFor(() => expect(summary).toHaveTextContent("末世科幻"));
    pendingFlushes[0](true);
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));

    expect(summary).toHaveTextContent("末世科幻");
  });

  it("ignores a stale inline failure after a newer save succeeds", async () => {
    const pendingFlushes: Array<(result: boolean) => void> = [];
    const flush = vi.fn(() => new Promise<boolean>((resolve) => pendingFlushes.push(resolve)));
    renderAgent(flush);
    const genre = screen.getByRole("combobox", { name: "主题材" });
    const summary = screen.getByTestId("agent-settings-summary");

    fireEvent.change(genre, { target: { value: "悬疑推理" } });
    fireEvent.blur(genre);
    await waitFor(() => expect(flush).toHaveBeenCalledTimes(1));
    fireEvent.change(genre, { target: { value: "末世科幻" } });
    fireEvent.blur(genre);
    await waitFor(() => expect(flush).toHaveBeenCalledTimes(2));

    pendingFlushes[1](true);
    await waitFor(() => expect(summary).toHaveTextContent("末世科幻"));
    pendingFlushes[0](false);
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));

    expect(summary).toHaveTextContent("末世科幻");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores a pending save completion after undo restores prior settings", async () => {
    const pendingFlushes: Array<(result: boolean) => void> = [];
    const flush = vi.fn(() => new Promise<boolean>((resolve) => pendingFlushes.push(resolve)));
    const { data } = renderAgent(flush);
    const genre = screen.getByRole("combobox", { name: "主题材" });
    const summary = screen.getByTestId("agent-settings-summary");

    fireEvent.change(genre, { target: { value: "悬疑推理" } });
    fireEvent.blur(genre);
    await waitFor(() => expect(flush).toHaveBeenCalledTimes(1));

    act(() => {
      expect(useCanvasStore.getState().undo()).toBe(true);
    });
    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({ settings: data.settings }),
    );
    expect(summary).toHaveTextContent("都市现实");

    pendingFlushes[0](true);
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));

    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({ settings: data.settings }),
    );
    expect(summary).toHaveTextContent("都市现实");
  });

  it("updates node fields and saved summary from the shared full settings form", async () => {
    const flush = vi.fn().mockResolvedValue(true);
    renderAgent(flush);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "编辑完整设定" }));
    await user.click(screen.getAllByRole("button", { name: "喜剧" })[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "故事想法" }), {
      target: { value: "一次误会引发的温暖旅程" },
    });
    await user.click(screen.getByRole("button", { name: "保存创作设定" }));

    await waitFor(() => expect(flush).toHaveBeenCalledTimes(1));
    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({
        settings: expect.objectContaining({
          genrePrimary: "喜剧",
          idea: "一次误会引发的温暖旅程",
        }),
      }),
    );
    expect(screen.getByTestId("agent-settings-summary")).toHaveTextContent("喜剧");
    expect(screen.getByTestId("agent-settings-summary")).toHaveTextContent("一次误会引发的温暖旅程");
    expect(screen.queryByRole("dialog", { name: "剧本设定器" })).not.toBeInTheDocument();
  });

  it("cancels full settings without writing the modal draft", async () => {
    const flush = vi.fn().mockResolvedValue(true);
    const { data } = renderAgent(flush);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "编辑完整设定" }));
    fireEvent.change(screen.getByRole("textbox", { name: "故事想法" }), {
      target: { value: "取消时不应写入" },
    });
    await user.click(screen.getByRole("button", { name: "取消" }));

    expect(flush).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({ settings: data.settings }),
    );
  });

  it("keeps the full settings draft open with an error when canvas flush fails", async () => {
    const flush = vi.fn().mockResolvedValue(false);
    const { data } = renderAgent(flush);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "编辑完整设定" }));
    const idea = screen.getByRole("textbox", { name: "故事想法" });
    fireEvent.change(idea, { target: { value: "保留这段创意" } });
    await user.click(screen.getByRole("button", { name: "保存创作设定" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
    expect(screen.getByRole("textbox", { name: "故事想法" })).toHaveValue("保留这段创意");
    expect(screen.getByRole("dialog", { name: "剧本设定器" })).toBeInTheDocument();
    expect(flush).toHaveBeenCalledTimes(1);
    expect(useCanvasStore.getState().nodes[0].data).toEqual(
      expect.objectContaining({ settings: data.settings }),
    );
    expect(screen.getByTestId("agent-settings-summary")).toHaveTextContent("都市现实");
  });
});
