import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceAcceptancePanel } from "@/components/assets/voice-acceptance-panel";

const mocks = vi.hoisted(() => ({ query: {} as Record<string, unknown>, save: vi.fn() }));
vi.mock("@/lib/queries/voice-acceptance", () => ({
  useVoiceAcceptance: () => mocks.query,
  useReviewVoiceAcceptance: () => ({ mutateAsync: mocks.save, isPending: false }),
}));

describe("voice acceptance", () => {
  beforeEach(() => {
    mocks.save.mockReset().mockResolvedValue({ ok: true });
    mocks.query = { data: { ok: true, data: [] }, isLoading: false };
  });
  it("keeps the entry visible even without samples", async () => {
    render(<VoiceAcceptancePanel project="p" />);
    await userEvent.click(screen.getByRole("button", { name: "查看历史声音样本" }));
    expect(screen.getByText(/独立于角色声音/)).toBeInTheDocument();
    expect(screen.getByText("暂无验收样本")).toBeInTheDocument();
  });
  it("shows historical cost and submits an independent review", async () => {
    mocks.query = { data: { ok: true, data: [{ sample_id: "s", label: "儿童男声", kind: "dialogue",
      instruction: "清澈童声", text: "你好", coins: "6", duration: 6.24, task_id: "t",
      status: "pending", notes: "", url: "/static/child.wav", reviewed_at: "", reviewed_by: "" }] } };
    render(<VoiceAcceptancePanel project="p" />);
    await userEvent.click(screen.getByRole("button", { name: "查看历史声音样本" }));
    expect(screen.getByText("历史消耗 6 积分")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("儿童男声验收结果"), "rejected");
    await userEvent.type(screen.getByLabelText("儿童男声验收备注"), "听感偏成熟");
    await userEvent.click(screen.getByRole("button", { name: "保存验收" }));
    expect(mocks.save).toHaveBeenCalledWith({ sampleId: "s", status: "rejected", notes: "听感偏成熟" });
    expect(screen.queryByText("人工发布候选")).not.toBeInTheDocument();
  });
});
