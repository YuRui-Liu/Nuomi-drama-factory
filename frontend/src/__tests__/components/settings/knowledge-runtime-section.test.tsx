import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/queries/knowledge-runtime", () => {
  const runningHubWorkflows = {
    image_upscale: "",
    video_minimax_h3: "2087934731806658562",
    tts_qwen3_voice_design: "",
    tts_indextts2_voice_clone: "",
  };
  return {
  useKnowledgeRuntimeStatus: () => ({
    data: {
      ready: false,
      state: "unconfigured",
      message: "请选择 Ollama 模型",
      codex: { installed: true, authenticated: true, version: "codex-cli 1.2.3", message: "ok" },
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
  useTestCodex: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useMediaProviderAccounts: () => ({ data: [], isLoading: false }),
  useSaveMediaProviderAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRunningHubWorkflows: () => ({ data: runningHubWorkflows }),
  useSaveRunningHubWorkflows: () => ({ mutateAsync: vi.fn(), isPending: false }),
  };
});

import { KnowledgeRuntimeSection } from "@/components/settings/knowledge-runtime-section";

it("shows the actual local knowledge and media providers", () => {
  render(<KnowledgeRuntimeSection open />);

  expect(screen.getByText("Codex CLI")).toBeInTheDocument();
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
