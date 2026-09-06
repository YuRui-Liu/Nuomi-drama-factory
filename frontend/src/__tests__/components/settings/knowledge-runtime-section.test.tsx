import { fireEvent, render, screen } from "@testing-library/react";
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
    tts_qwen3_voice_design: "",
    tts_indextts2_voice_clone: "",
  } as {
    image_upscale: string;
    video_minimax_h3: string;
    tts_qwen3_voice_design: string;
    tts_indextts2_voice_clone: string;
  } | undefined,
  recognize: vi.fn(),
  recognizePending: false,
  concurrencyOpen: undefined as boolean | undefined,
}));

vi.mock("@/components/settings/task-concurrency-card", () => ({
  TaskConcurrencyCard: ({ open }: { open: boolean }) => {
    knowledgeRuntimeMockState.concurrencyOpen = open;
    return <div>任务并发</div>;
  },
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
  useSaveMediaProviderAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
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
  knowledgeRuntimeMockState.recognizePending = false;
  knowledgeRuntimeMockState.concurrencyOpen = undefined;
  knowledgeRuntimeMockState.runningHubWorkflows = {
    image_upscale: "",
    video_minimax_h3: "2087934731806658562",
    tts_qwen3_voice_design: "",
    tts_indextts2_voice_clone: "",
  };
});

it("uses the director MiniMax H3 workflow before saved workflow settings load", () => {
  knowledgeRuntimeMockState.runningHubWorkflows = undefined;

  render(<KnowledgeRuntimeSection open />);

  expect(screen.getByLabelText("MiniMax H3 图生视频 Workflow ID")).toHaveValue("2089723723468328961");
});

it("shows the actual local knowledge and media providers", () => {
  render(<KnowledgeRuntimeSection open />);

  expect(screen.getByText("Codex CLI")).toBeInTheDocument();
  expect(screen.getByText("文本任务路由")).toBeInTheDocument();
  expect(screen.getByText("任务并发")).toBeInTheDocument();
  expect(knowledgeRuntimeMockState.concurrencyOpen).toBe(true);
  expect(
    screen.getByText("文本任务路由").compareDocumentPosition(screen.getByText("任务并发")) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.getByLabelText("整集导演规划执行运行时")).toBeInTheDocument();
  expect(screen.getByText("Ollama Embedding")).toBeInTheDocument();
  expect(screen.getByText("GRSAI")).toBeInTheDocument();
  expect(screen.getByLabelText("GRSAI 图片模型")).toHaveTextContent("gpt-image-2");
  expect(screen.getByText("RunningHub")).toBeInTheDocument();
  expect(screen.getByLabelText("图片超分 Workflow ID")).toBeInTheDocument();
  expect(screen.getByLabelText("MiniMax H3 图生视频 Workflow ID")).toHaveValue("2087934731806658562");
  expect(screen.getByLabelText("Qwen3 音色设计 Workflow ID")).toBeInTheDocument();
  expect(screen.getByLabelText("IndexTTS2 声音克隆 Workflow ID")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "测试并保存" })).toBeInTheDocument();
  expect(screen.queryByText(/RelayClaw/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/NewAPI/i)).not.toBeInTheDocument();
});

it("passes the closed state to the task concurrency card", () => {
  render(<KnowledgeRuntimeSection open={false} />);

  expect(knowledgeRuntimeMockState.concurrencyOpen).toBe(false);
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
