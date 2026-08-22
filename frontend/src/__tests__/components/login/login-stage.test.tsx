import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "auth.businessWechat.open": "打开商务联系",
        "auth.businessWechat.label": "商务联系",
        "auth.businessWechat.qrAlt": "商务联系二维码",
        "auth.businessWechat.title": "联系商务团队",
        "auth.businessWechat.subtitle": "咨询团队方案",
        "auth.businessWechat.note": "扫码添加",
        "auth.github.star": "Star",
        "auth.openManual": "打开产品手册",
        "auth.learnMore": "产品手册",
      })[key] ?? key,
  }),
}));

vi.mock("@/hooks/use-github-stars", () => ({ useGithubStars: () => 1200 }));
vi.mock("@/components/login/community-showcase", () => ({ CommunityShowcase: () => null }));
vi.mock("@/components/login/light-rays", () => ({ default: () => null }));
vi.mock("@/components/react-bits/split-text", () => ({
  default: ({ tag: Tag = "div", text, className }: { tag?: "h1"; text: string; className?: string }) => (
    <Tag className={className}>{text}</Tag>
  ),
}));

import { LoginStageContent } from "@/components/login/login-stage";

describe("LoginStageContent", () => {
  it("presents NuomiDrama as a professional production workspace", () => {
    render(<LoginStageContent onStart={vi.fn()} />);

    expect(screen.getByLabelText("NuomiDrama")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "从故事到成片，一站完成" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始创作" })).toBeInTheDocument();
    expect(screen.getByLabelText("产品工作台预览")).toBeInTheDocument();
    expect(screen.getByText("9:16")).toBeInTheDocument();
  });

  it("keeps the start, manual, GitHub, and business-contact actions available", () => {
    const onStart = vi.fn();
    render(<LoginStageContent onStart={onStart} />);

    fireEvent.click(screen.getByRole("button", { name: "开始创作" }));
    expect(onStart).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: "打开产品手册" })).toHaveAttribute("href");
    expect(screen.getByRole("link", { name: "GitHub" })).toHaveAttribute("href", "https://github.com/dramaclaw/dramaclaw");
    expect(screen.getByRole("button", { name: "打开商务联系" })).toBeInTheDocument();
  });
});
