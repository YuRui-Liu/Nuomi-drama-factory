// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createLazyFileRoute } from "@tanstack/react-router";
import { LoginCinematicPage } from "@/components/login/cinematic/LoginCinematicPage";

export const Route = createLazyFileRoute("/login")({
  component: LoginCinematicPage,
});
