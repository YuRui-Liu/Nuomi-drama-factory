// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useAuthStore } from "@/stores/auth-store";
import { authRequired } from "@/lib/runtime-config";

export type AuthMode = "cookie" | "local";

export function authMode(): AuthMode {
  return import.meta.env.VITE_AUTH_MODE === "local" ? "local" : "cookie";
}

export function isLocalAuthMode(): boolean {
  return authMode() === "local";
}

export async function ensureAuthenticatedForAppRoute(): Promise<boolean> {
  const auth = useAuthStore.getState();
  if (auth.username) return true;
  if (authRequired()) return Boolean(await auth.getCurrentUser());
  // CE has no login gate, but the app shell still needs the synthetic owner
  // returned by /auth/me. A transient fetch failure must not redirect to
  // /login, where CE would immediately send the browser back here.
  await auth.getCurrentUser({ clearOnNetworkFailure: false });
  return true;
}
