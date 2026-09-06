import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, expect, it, vi } from "vitest";

const knowledgeRuntimeMockState = vi.hoisted(() => ({
  codex: {
    installed: true,
    compatible: true,
    authenticated: true,
    ready: true,
    path: "E:\\Tools\\Codex\\codex.exe",
    version: "codex-cli 1.2.3",
    message: "Codex CLI 可用",
    state: "ready",
  },
  runningHubWorkflows: {
    image_upscale: "",
    video_minimax_h3: "2087934731806658562",
    video_minimax_h3_ref: "2096502793044582401",
    video_minimax_h3_ref_max_images: 7,
    tts_qwen3_voice_design: "",
    tts_indextts2_voice_clone: "",
  } as {
    image_upscale: string;
    video_minimax_h3: string;
    video_minimax_h3_ref: string;
    video_minimax_h3_ref_max_images: number;
    tts_qwen3_voice_design: string;
    tts_indextts2_voice_clone: string;
  } | undefined,
  saveProvider: vi.fn(),
  recognize: vi.fn(),
  recognizePending: false,
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/queries/model-gateway", () => {
  const data = {
      data: {
        roles: [
          {
            id: "director_plan",
            label: "整集导演规划",
            route: {
              runtime: "model_api",
              model: "deepseek-v4-flash",
              reasoning_effort: null,
              skill_id: null,
              skill_version: null,
              fallback: "stop",
            },
          },
        ],
      },
    };
  return {
  useTaskRuntimeConfig: () => ({
    data,
    isLoading: false,
    isError: false,
  }),
  useSaveTaskRuntimeConfig: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});
vi.mock("@/lib/queries/knowledge-runtime", () => {
  return {
  useKnowledgeRuntimeStatus: () => ({
    data: {
      ready: false,
      state: "unconfigured",
      message: "请选择 Ollama 模型",
      codex: knowledgeRuntimeMockState.codex,
      ollama: {
        provider: "ollama",
        baseUrl: "http://127.0.0.1:11434",
        model: "",
        dimension: 0,
        digest: "",
        batchSize: 8,
        probedAt: "",
        configured: false,
      },
    },
    isLoading: false,
  }),
  useOllamaModels: () => ({
    data: [{ name: "bge-m3:latest", digest: "sha256:def" }],
    isFetching: false,
    refetch: vi.fn(),
  }),
  useSaveKnowledgeRuntimeSettings: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRecognizeCodex: () => ({
    mutate: knowledgeRuntimeMockState.recognize,
    isPending: knowledgeRuntimeMockState.recognizePending,
  }),
  useMediaProviderAccounts: () => ({ data: [], isLoading: false }),
  useSaveMediaProviderAccount: () => ({ mutateAsync: knowledgeRuntimeMockState.saveProvider, isPending: false }),
  useRunningHubWorkflows: () => ({ data: knowledgeRuntimeMockState.runningHubWorkflows }),
  useSaveRunningHubWorkflows: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});

import { KnowledgeRuntimeSection } from "@/components/settings/knowledge-runtime-section";

beforeEach(() => {
  knowledgeRuntimeMockState.codex = {
    installed: true,
    compatible: true,
    authenticated: true,
    ready: true,
    path: "E:\\Tools\\Codex\\codex.exe",
    version: "codex-cli 1.2.3",
    message: "Codex CLI 可用",
    state: "ready",
  };
  knowledgeRuntimeMockState.recognize.mockReset().mockResolvedValue(undefined);
  knowledgeRuntimeMockState.saveProvider.mockReset().mockResolvedValue(undefined);
  vi.mocked(toast.error).mockClear();
  vi.mocked(toast.success).mockClear();
  knowledgeRuntimeMockState.recognizePending = false;
  knowledgeRuntimeMockState.runningHubWorkflows = {
    image_upscale: "",
    video_minimax_h3: "2087934731806658562",
    video_minimax_h3_ref: "2096502793044582401",
    video_minimax_h3_ref_max_images: 7,
    tts_qwen3_voice_design: "",
    tts_indextts2_voice_clone: "",
  };
});

it("uses the director MiniMax H3 workflow before saved workflow settings load", () => {
  knowledgeRuntimeMockState.runningHubWorkflows = undefined;

  render(<KnowledgeRuntimeSection open />);

  expect(screen.getByLabelText("MiniMax H3 图生视频 Workflow ID")).toHaveValue("2089723723468328961");
  expect(screen.getByLabelText("MiniMax H3 带 Ref 导演台 Workflow ID")).toHaveValue("2096502793044582401");
  expect(screen.getByLabelText("带 Ref 导演台全局 Ref 上限")).toHaveValue(5);
});

it("shows the actual local knowledge and media providers", () => {
  render(<KnowledgeRuntimeSection open />);

  expect(screen.getByText("Codex CLI")).toBeInTheDocument();
  expect(screen.getByText("文本任务路由")).toBeInTheDocument();
  expect(screen.getByLabelText("整集导演规划执行运行时")).toBeInTheDocument();
  expect(screen.getByText("Ollama Embedding")).toBeInTheDocument();
  expect(screen.getByText("GRSAI")).toBeInTheDocument();
  expect(screen.getByLabelText("GRSAI 图片模型")).toHaveTextContent("gpt-image-2");
  expect(screen.getByText("RunningHub")).toBeInTheDocument();
  expect(screen.getByLabelText("图片超分 Workflow ID")).toBeInTheDocument();
  expect(screen.getByLabelText("MiniMax H3 图生视频 Workflow ID")).toHaveValue("2087934731806658562");
  expect(screen.getByLabelText("MiniMax H3 带 Ref 导演台 Workflow ID")).toHaveValue("2096502793044582401");
  expect(screen.getByLabelText("带 Ref 导演台全局 Ref 上限")).toHaveValue(7);
  expect(screen.getByLabelText("带 Ref 导演台全局 Ref 上限")).toHaveAttribute("min", "1");
  expect(screen.getByLabelText("带 Ref 导演台全局 Ref 上限")).toHaveAttribute("max", "10");
  expect(screen.getByLabelText("带 Ref 导演台全局 Ref 上限")).toHaveAttribute("step", "1");
  expect(screen.getByLabelText("Qwen3 音色设计 Workflow ID")).toBeInTheDocument();
  expect(screen.getByLabelText("IndexTTS2 声音克隆 Workflow ID")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "测试并保存" })).toBeInTheDocument();
  expect(screen.queryByText(/RelayClaw/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/NewAPI/i)).not.toBeInTheDocument();
});

it.each(["1", "10"])("accepts a Ref image limit of %s and preserves the legacy workflow", async (limit) => {
  render(<KnowledgeRuntimeSection open />);

  fireEvent.change(screen.getByLabelText("RunningHub API Key"), { target: { value: "rh-secret" } });
  fireEvent.change(screen.getByLabelText("MiniMax H3 带 Ref 导演台 Workflow ID"), {
    target: { value: "ref-workflow" },
  });
  fireEvent.change(screen.getByLabelText("带 Ref 导演台全局 Ref 上限"), { target: { value: limit } });
  fireEvent.click(screen.getAllByRole("button", { name: "保存" })[1]);

  await waitFor(() => expect(knowledgeRuntimeMockState.saveProvider).toHaveBeenCalledTimes(1));
  expect(knowledgeRuntimeMockState.saveProvider).toHaveBeenCalledWith(expect.objectContaining({
    workflows: expect.objectContaining({
      video_minimax_h3: "2087934731806658562",
      video_minimax_h3_ref: "ref-workflow",
      video_minimax_h3_ref_max_images: Number(limit),
    }),
  }));
});

it.each(["", "0", "11", "1.5"])("rejects an invalid Ref image limit of %j", async (limit) => {
  render(<KnowledgeRuntimeSection open />);

  fireEvent.change(screen.getByLabelText("RunningHub API Key"), { target: { value: "rh-secret" } });
  fireEvent.change(screen.getByLabelText("带 Ref 导演台全局 Ref 上限"), { target: { value: limit } });
  fireEvent.click(screen.getAllByRole("button", { name: "保存" })[1]);

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(knowledgeRuntimeMockState.saveProvider).not.toHaveBeenCalled();
});

it("shows an available Codex runtime with its resolved path, version and message", () => {
  render(<KnowledgeRuntimeSection open />);

  expect(screen.getByText("可用")).toBeInTheDocument();
  expect(screen.getByText("codex-cli 1.2.3")).toBeInTheDocument();
  expect(screen.getByText("E:\\Tools\\Codex\\codex.exe")).toBeInTheDocument();
  expect(screen.getByText("Codex CLI 可用")).toBeInTheDocument();
});

it("recognizes Codex again through the non-throwing mutation callback", () => {
  render(<KnowledgeRuntimeSection open />);

  fireEvent.click(screen.getByRole("button", { name: "重新识别" }));

  expect(knowledgeRuntimeMockState.recognize).toHaveBeenCalledTimes(1);
});

it("shows the recognition spinner while Codex recognition is pending", () => {
  knowledgeRuntimeMockState.recognizePending = true;

  render(<KnowledgeRuntimeSection open />);

  expect(screen.getByLabelText("正在识别 Codex")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /重新识别/ })).toBeDisabled();
});

it.each([
  ["a missing installation", { installed: false, compatible: false, authenticated: false, ready: false, state: "not_installed" }, "未安装"],
  ["an incompatible version", { installed: true, compatible: false, authenticated: false, ready: false, state: "version_unsupported" }, "版本过低"],
  ["an unauthenticated installation", { installed: true, compatible: true, authenticated: false, ready: false, state: "not_authenticated" }, "未登录"],
  ["a runtime whose version probe failed", { installed: true, compatible: false, authenticated: false, ready: false, state: "exec_failed" }, "无法启动"],
])("maps %s to the expected status", (_name, codexState, label) => {
  knowledgeRuntimeMockState.codex = { ...knowledgeRuntimeMockState.codex, ...codexState };

  render(<KnowledgeRuntimeSection open />);

  expect(screen.getByText(label)).toBeInTheDocument();
});
