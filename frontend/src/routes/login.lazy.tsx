// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute } from "@tanstack/react-router";
import { LoginCinematicPage } from "@/components/login/cinematic/LoginCinematicPage";
import { LoginCard } from "@/components/login/login-card";
import { isTeamRuntime } from "@/lib/runtime-config";

export const Route = createLazyFileRoute("/login")({
  component: () => isTeamRuntime() ? (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <section className="w-full max-w-md space-y-6">
        <h1 className="text-center text-2xl font-semibold">糯米短剧 · 团队工作台</h1>
        <p className="text-center text-sm text-muted-foreground">使用管理员创建的账号登录</p>
        <LoginCard />
      </section>
    </main>
  ) : <LoginCinematicPage />,
});
