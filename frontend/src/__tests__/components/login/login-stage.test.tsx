import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import type { ElementType } from "react";

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
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...props }: React.ComponentProps<"a">) => <a {...props}>{children}</a>,
}));
vi.mock("@/components/login/light-rays", () => ({ default: () => null }));
vi.mock("@/components/react-bits/split-text", () => ({
  default: ({ tag: Tag = "h1", text, className }: { tag?: ElementType; text: string; className?: string }) => (
    <Tag className={className}>{text}</Tag>
  ),
}));
vi.mock("@/components/react-bits/aurora", () => ({ default: () => null }));
vi.mock("@/hooks/use-reduced-motion", () => ({ useReducedMotion: () => true }));
vi.mock("lenis", () => ({
  default: class {
    scroll = 0;
    on() {}
    raf() {}
    scrollTo() {}
    destroy() {}
  },
}));
vi.mock("gsap", () => ({
  gsap: { registerPlugin: vi.fn(), ticker: { add: vi.fn(), remove: vi.fn(), lagSmoothing: vi.fn() } },
}));
vi.mock("gsap/ScrollTrigger", () => ({
  ScrollTrigger: {
    create: () => ({ kill: vi.fn() }),
    refresh: vi.fn(),
    update: vi.fn(),
    scrollerProxy: vi.fn(),
  },
}));
vi.mock("@/components/login/login-modal", () => ({
  LoginModal: ({ open }: { open: boolean }) => (open ? <div role="dialog" aria-label="登录">登录</div> : null),
}));

vi.mock("@/components/login/cinematic/SecondScreenVideo", () => ({ SecondScreenVideo: () => null }));
vi.mock("@/components/login/cinematic/ThirdScreenVideo", () => ({ ThirdScreenVideo: () => null }));
vi.mock("@/components/login/cinematic/FourthScreen", () => ({ FourthScreen: () => null }));
vi.mock("@/components/login/cinematic/FifthScreenVideo", () => ({ FifthScreenVideo: () => null }));
vi.mock("@/components/login/cinematic/SixthShowcaseScreen", () => ({ SixthShowcaseScreen: () => null }));
vi.mock("@/components/login/cinematic/SeventhPipelineScreen", () => ({ SeventhPipelineScreen: () => null }));
vi.mock("@/components/login/cinematic/EighthControlScreen", () => ({ EighthControlScreen: () => null }));
vi.mock("@/components/login/cinematic/NinthWorkflowScreen", () => ({ NinthWorkflowScreen: () => null }));
vi.mock("@/components/login/cinematic/TenthTestimonialsScreen", () => ({ TenthTestimonialsScreen: () => null }));
vi.mock("@/components/login/cinematic/EleventhFaqScreen", () => ({ EleventhFaqScreen: () => null }));
vi.mock("@/components/login/cinematic/TwelfthFinalScreen", () => ({ TwelfthFinalScreen: () => null }));

import { LoginStageContent } from "@/components/login/login-stage";
import { LoginCinematicPage } from "@/components/login/cinematic/LoginCinematicPage";

describe("LoginStageContent", () => {
  it("presents Nuomi Drama Factory as a professional production workspace", () => {
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
    expect(screen.getByRole("link", { name: "GitHub" })).toHaveAttribute("href", "https://github.com/YuRui-Liu/Nuomi-drama-factory");
    expect(screen.getByRole("button", { name: "打开商务联系" })).toBeInTheDocument();
  });
});

describe("the real cinematic login entry", () => {
  it("mounts the editorial workspace hero and opens the existing login modal", () => {
    render(<LoginCinematicPage />);

    expect(screen.getByRole("heading", { name: "从故事到成片，一站完成" })).toBeInTheDocument();
    expect(screen.getByLabelText("产品工作台预览")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "让灵感发生" })).not.toBeInTheDocument();
    expect(screen.queryByText("auth.community.heading")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "开始创作" }));
    expect(screen.getByRole("dialog", { name: "登录" })).toBeInTheDocument();
  });

  it("does not retain the retired HUD wordmark or final-mark image", () => {
    const sources = [
      "src/components/login/cinematic/IntroRitualScreen.tsx",
      "src/components/login/cinematic/TwelfthFinalScreen.tsx",
      "src/components/login/cinematic/LoginCinematicHero.tsx",
    ].map((path) => readFileSync(path, "utf8")).join("\n");

    expect(sources).not.toMatch(/DRAMACLAW|final-mark\.png|让灵感发生/);
  });

  it("consumes the cinematic exit variables on the editorial hero", () => {
    const css = readFileSync("src/components/login/login.module.css", "utf8");

    expect(css).toMatch(/\.hero\s*\{[^}]*var\(--hero-exit-offset[^}]*var\(--hero-exit-scale/s);
    expect(css).toMatch(/\.hero\s*\{[^}]*filter:\s*blur\(var\(--hero-exit-blur/s);
    expect(css).toContain("opacity: var(--hero-exit-opacity, 1)");
  });
});
