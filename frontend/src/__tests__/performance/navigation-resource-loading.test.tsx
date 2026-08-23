import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const readSource = (relativePath: string) => {
  const path = resolve(process.cwd(), relativePath);
  expect(existsSync(path), `${relativePath} should exist`).toBe(true);
  const source = readFileSync(path, "utf8");
  expect(source.trim(), `${relativePath} should not be empty`).not.toBe("");
  return source;
};

describe("navigation resource loading", () => {
  it("keeps the login visual experience out of the eager route module", () => {
    const loginRoute = readSource("src/routes/login.tsx");

    expect(loginRoute).not.toMatch(
      /^\s*import(?!\s+type\b)[^\r\n]*["']@\/components\/login\/cinematic\/LoginCinematicPage["']/m,
    );
  });

  it("keeps authentication and region gating in the eager login route", () => {
    const loginRoute = readSource("src/routes/login.tsx");

    expect(loginRoute).toContain("beforeLoad: async () =>");
    expect(loginRoute).toContain('clusterConfig.mode === "multi-region"');
    expect(loginRoute).toContain("getRegionCookie()");
    expect(loginRoute).toContain("useAuthStore.getState().reset()");
    expect(loginRoute).toContain("authRequired()");
    expect(loginRoute).toContain("ensureAuthenticatedForAppRoute()");
    expect(loginRoute).toContain('redirect({ to: "/", replace: true })');
  });

  it("renders the login visual experience from a lazy route module", () => {
    const loginLazy = readSource("src/routes/login.lazy.tsx");
    const routeTree = readSource("src/routeTree.gen.ts");

    expect(loginLazy).toContain('createLazyFileRoute("/login")');
    expect(loginLazy).toContain("component: LoginCinematicPage");
    expect(routeTree).toContain("import('./routes/login.lazy')");
    expect(routeTree).toMatch(
      /const LoginRoute = LoginRouteImport\.update\([\s\S]*?\)\.lazy\(\(\) =>/,
    );
  });

  it("reuses intent preloads for thirty seconds", () => {
    const main = readSource("src/main.tsx");

    expect(main).toContain("defaultPreloadStaleTime: 30_000");
  });
});
