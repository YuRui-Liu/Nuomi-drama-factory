import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const initialData = {
    restart_required: false,
    lanes: {
      default: {
        configured: 4,
        running_limits: { project: 4, user: 4, executor: 4 },
        active: 2,
        managed_by_environment: false,
      },
      video: {
        configured: 3,
        running_limits: { project: 6, user: 4, executor: 3 },
        active: 1,
        managed_by_environment: false,
      },
      world: {
        configured: 2,
        running_limits: { project: 2, user: 2, executor: 2 },
        active: 0,
        managed_by_environment: true,
      },
      ffmpeg: {
        configured: 1,
        running_limits: { project: 1, user: 1, executor: 1 },
        active: 1,
        managed_by_environment: false,
      },
    },
  };
  return {
    mutateAsync: vi.fn(),
    toastSuccess: vi.fn(),
    toastError: vi.fn(),
    apiGet: vi.fn(),
    apiPut: vi.fn(),
    queryEnabled: true,
    initialData,
    data: initialData,
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string; [key: string]: unknown }) =>
      (options?.defaultValue ?? key).replace(/{{(\w+)}}/g, (_match, name: string) =>
        String(options?.[name] ?? ""),
      ),
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));

vi.mock("@/lib/api", () => ({
  api: { get: mocks.apiGet, put: mocks.apiPut },
}));

vi.mock("@/lib/queries/task-concurrency", () => ({
  useTaskConcurrency: (enabled: boolean) => {
    mocks.queryEnabled = enabled;
    return { data: mocks.data, isLoading: false, isError: false };
  },
  useSaveTaskConcurrency: () => ({
    mutateAsync: mocks.mutateAsync,
    isPending: false,
  }),
}));

import { TaskConcurrencyCard } from "@/components/settings/task-concurrency-card";

beforeEach(() => {
  mocks.data = mocks.initialData;
  mocks.mutateAsync.mockReset().mockResolvedValue({
    ...mocks.data,
    restart_required: true,
  });
  mocks.toastSuccess.mockReset();
  mocks.toastError.mockReset();
  mocks.apiGet.mockReset();
  mocks.apiPut.mockReset();
  mocks.queryEnabled = true;
});

it("executes GET and PUT through the standard envelope contract", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  mocks.apiGet.mockReturnValue({
    json: vi.fn().mockResolvedValue({ ok: true, data: mocks.initialData }),
  });
  mocks.apiPut.mockResolvedValue({
    ok: true,
    json: vi.fn().mockResolvedValue({
      ok: true,
      data: { ...mocks.initialData, restart_required: true },
    }),
  });
  const queries = await vi.importActual<typeof import("@/lib/queries/task-concurrency")>(
    "@/lib/queries/task-concurrency",
  );

  const getHook = renderHook(() => queries.useTaskConcurrency(true), { wrapper });
  await waitFor(() => expect(getHook.result.current.data).toEqual(mocks.initialData));
  expect(mocks.apiGet.mock.calls[0]?.[0]).toBe("api/v1/task-runtime/concurrency");

  const mutationHook = renderHook(() => queries.useSaveTaskConcurrency(), { wrapper });
  const values = { default: 8, video: 5, world: 2, ffmpeg: 2 };
  let saved: unknown;
  await act(async () => {
    saved = await mutationHook.result.current.mutateAsync(values);
  });

  expect(mocks.apiPut).toHaveBeenCalledWith(
    "api/v1/task-runtime/concurrency",
    expect.objectContaining({ json: values, throwHttpErrors: false }),
  );
  expect(saved).toEqual({ ...mocks.initialData, restart_required: true });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["task-runtime", "concurrency"] });
});

it("initializes all lane drafts and shows effective executor usage", () => {
  render(<TaskConcurrencyCard open />);

  expect(screen.getByLabelText("普通任务")).toHaveValue(4);
  expect(screen.getByLabelText("视频任务")).toHaveValue(3);
  expect(screen.getByLabelText("世界构建")).toHaveValue(2);
  expect(screen.getByLabelText("FFmpeg")).toHaveValue(1);
  expect(screen.getByText("当前运行 2 / 4")).toBeInTheDocument();
  expect(screen.getByText("当前运行 1 / 3")).toBeInTheDocument();
  expect(screen.getByText("项目 6 · 用户 4 · 执行器 3")).toBeInTheDocument();
  expect(screen.queryByText("项目 4 · 用户 4 · 执行器 4")).not.toBeInTheDocument();
});

it("disables environment-managed lanes and explains why", () => {
  render(<TaskConcurrencyCard open />);

  expect(screen.getByLabelText("世界构建")).toBeDisabled();
  expect(screen.getByText("由环境变量管理")).toBeInTheDocument();
});

it.each([
  ["", "不能为空"],
  ["1.5", "请输入整数"],
  ["33", "请输入 1–32 之间的整数"],
])("rejects an invalid editable lane value %s", (value, message) => {
  render(<TaskConcurrencyCard open />);

  fireEvent.change(screen.getByLabelText("普通任务"), { target: { value } });

  expect(screen.getByText(message)).toBeInTheDocument();
  expect(screen.getByText("请修正标记的并发数后再保存。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存并发设置" })).toBeDisabled();
});

it("submits all four lanes and keeps the server value for an environment-managed lane", async () => {
  render(<TaskConcurrencyCard open />);

  fireEvent.change(screen.getByLabelText("普通任务"), { target: { value: "8" } });
  fireEvent.change(screen.getByLabelText("视频任务"), { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText("FFmpeg"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "保存并发设置" }));

  await waitFor(() => {
    expect(mocks.mutateAsync).toHaveBeenCalledWith({
      default: 8,
      video: 5,
      world: 2,
      ffmpeg: 2,
    });
  });
  expect(mocks.toastSuccess).toHaveBeenCalledWith("配置已保存，重启服务后生效");
  expect(screen.getByText("待重启")).toBeInTheDocument();
});

it("preserves the local draft when saving fails", async () => {
  const serverError = new Error("后端拒绝了该配置");
  serverError.name = "TaskConcurrencyServerError";
  mocks.mutateAsync.mockRejectedValueOnce(serverError);
  render(<TaskConcurrencyCard open />);

  fireEvent.change(screen.getByLabelText("普通任务"), { target: { value: "9" } });
  fireEvent.click(screen.getByRole("button", { name: "保存并发设置" }));

  await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith("后端拒绝了该配置"));
  expect(screen.getByLabelText("普通任务")).toHaveValue(9);
});

it("does not overwrite a failed dirty draft when query data refreshes", async () => {
  const serverError = new Error("后端拒绝了该配置");
  serverError.name = "TaskConcurrencyServerError";
  mocks.mutateAsync.mockRejectedValueOnce(serverError);
  const view = render(<TaskConcurrencyCard open />);

  fireEvent.change(screen.getByLabelText("普通任务"), { target: { value: "9" } });
  fireEvent.click(screen.getByRole("button", { name: "保存并发设置" }));
  await waitFor(() => expect(mocks.toastError).toHaveBeenCalled());

  mocks.data = {
    ...mocks.initialData,
    lanes: {
      ...mocks.initialData.lanes,
      default: { ...mocks.initialData.lanes.default, configured: 6 },
      world: { ...mocks.initialData.lanes.world, configured: 5 },
    },
  };
  view.rerender(<TaskConcurrencyCard open />);

  expect(screen.getByLabelText("普通任务")).toHaveValue(9);
  expect(screen.getByLabelText("世界构建")).toHaveValue(5);
});

it("uses the localized fallback for non-envelope save failures", async () => {
  mocks.mutateAsync.mockRejectedValueOnce(new Error("Failed to fetch"));
  render(<TaskConcurrencyCard open />);

  fireEvent.click(screen.getByRole("button", { name: "保存并发设置" }));

  await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith("保存任务并发设置失败"));
});

it("only enables its query while the settings section is open", () => {
  render(<TaskConcurrencyCard open={false} />);

  expect(mocks.queryEnabled).toBe(false);
});
