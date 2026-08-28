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

  it("keeps the Piko station out of the eager app route bundle", () => {
    const appRoute = readSource("src/routes/_app.tsx");

    expect(appRoute).not.toMatch(
      /^\s*import(?!\s*\()(?!\s+type\b)[^\r\n]*["']@\/features\/piko-mini-game\/PikoInspirationStation["']/m,
    );
    expect(appRoute).toMatch(
      /lazy\(\(\)\s*=>\s*import\("@\/features\/piko-mini-game\/PikoInspirationStation"\)/,
    );
  });

  it("mounts the lazy Piko station only while it is open", () => {
    const appRoute = readSource("src/routes/_app.tsx");

    expect(appRoute).toMatch(
      /\{pikoStationOpen\s*&&\s*\([\s\S]*?<Suspense[\s\S]*?<PikoInspirationStation/,
    );
  });

  it("shows an accessible local status while the Piko chunk is loading", () => {
    const appRoute = readSource("src/routes/_app.tsx");

    expect(appRoute).not.toContain("<Suspense fallback={null}>");
    expect(appRoute).toMatch(
      /<Suspense[\s\S]*?fallback=\{[\s\S]*?role="status"[\s\S]*?aria-live="polite"[\s\S]*?\}[\s\S]*?>/,
    );
  });

  it("keeps the companion and version checks eager", () => {
    const appRoute = readSource("src/routes/_app.tsx");

    expect(appRoute).toMatch(
      /^\s*import\s+\{\s*MyBuddyCompanion\s*\}\s+from\s+["']@\/features\/companion\/MyBuddyCompanion["']/m,
    );
    expect(appRoute).toMatch(
      /^\s*import\s+\{\s*VersionUpdateDialog\s*\}\s+from\s+["']@\/features\/version-update\/VersionUpdateDialog["']/m,
    );
  });

  it("routes every production 3D director entry through the lazy wrapper", () => {
    const entrypoints = [
      "src/components/assets/scenes-panel.tsx",
      "src/components/episode/beat-workbench/sketch-section.tsx",
      "src/components/episode/beat-workbench/render-section.tsx",
      "src/features/canvas/nodes/ImageGenNode.tsx",
      "src/features/canvas/nodes/SkillNode.tsx",
      "src/features/canvas/nodes/UploadNode.tsx",
      "src/features/canvas/nodes/ThreeDWorldNode.tsx",
      "src/features/canvas/ui/CanvasHistoryAssetsModal.tsx",
    ];

    for (const entrypoint of entrypoints) {
      const source = readSource(entrypoint);
      expect(source, entrypoint).not.toMatch(
        /^\s*import(?!\s+type\b)[^\r\n]*["']@\/features\/viewer-kit\/three-d\/ThreeDDirectorDialog["']/m,
      );
      expect(source, entrypoint).toContain("@/features/viewer-kit/three-d/LazyThreeDDirectorDialog");
    }
  });

  it("loads the 3D director implementation only through React.lazy", () => {
    const wrapper = readSource("src/features/viewer-kit/three-d/LazyThreeDDirectorDialog.tsx");

    expect(wrapper).toMatch(/lazy\(\(\)\s*=>\s*import\([\s\S]*ThreeDDirectorDialog/);
    expect(wrapper).toMatch(/import\s+type\s+\{\s*ThreeDDirectorDialogProps\s*\}/);
    expect(wrapper).toMatch(/if\s*\(!open\)\s*return\s+null/);
    expect(wrapper).not.toContain("<Suspense fallback={null}>");
    expect(wrapper).toMatch(/role=["']status["']/);
    expect(wrapper).toMatch(/aria-live=["']polite["']/);
  });

  it("limits eager asset thumbnails to the first eight items in every asset card", () => {
    const assetLibrary = readSource("src/features/freezone/AssetLibraryPanel.tsx");

    expect(assetLibrary).not.toContain('index < 20 ? "eager" : "lazy"');
    expect(assetLibrary.match(/index < 8 \? "eager" : "lazy"/g)).toHaveLength(2);
  });

  it("does not fetch video metadata for coverless asset cards", () => {
    const assetLibrary = readSource("src/features/freezone/AssetLibraryPanel.tsx");

    expect(assetLibrary).not.toContain("<video");
    expect(assetLibrary).not.toContain('preload="metadata"');
    expect(assetLibrary.match(/aria-label=\{`视频：\$\{asset\.label\}`\}/g)).toHaveLength(2);
  });

  it("hydrates the lightweight freezone index from the existing full-assets query", () => {
    const assetLibrary = readSource("src/features/freezone/AssetLibraryPanel.tsx");

    expect(assetLibrary).toContain("useFreezoneProjectAssetIndex");
    expect(assetLibrary).toContain("useFreezoneProjectAssets");
    expect(assetLibrary).toMatch(/useFreezoneProjectAssets\(project,\s*projectAssetIndexQuery\.isSuccess\)/);
  });

  it("requests each visible asset list through one aggregate reference query", () => {
    const entrypoints = [
      "src/routes/_app/projects.$project/characters.lazy.tsx",
      "src/components/assets/scenes-panel.tsx",
      "src/components/assets/props-panel.tsx",
    ];

    for (const entrypoint of entrypoints) {
      const source = readSource(entrypoint);
      expect(source, entrypoint).toContain("useAssetReferences");
      expect(source, entrypoint).not.toContain("useAssetReferenceIndex");
    }

    const characters = readSource("src/routes/_app/projects.$project/characters.lazy.tsx");
    const scenes = readSource("src/components/assets/scenes-panel.tsx");
    const props = readSource("src/components/assets/props-panel.tsx");
    expect(characters).toMatch(/identities\.map\([\s\S]*type:\s*"identity"/);
    expect(scenes).toMatch(/allItems\.map\([\s\S]*type:\s*"scene"/);
    expect(props).toMatch(/allItems\.map\([\s\S]*type:\s*"prop"/);
    expect(characters).toMatch(/enabled:\s*identities\.length\s*>\s*0/);
    expect(scenes).toMatch(/enabled:\s*allItems\.length\s*>\s*0/);
    expect(props).toMatch(/enabled:\s*allItems\.length\s*>\s*0/);
  });
  it("keeps failed aggregate reference counts unknown in all three consumers", () => {
    const entrypoints = [
      "src/routes/_app/projects.$project/characters.lazy.tsx",
      "src/components/assets/scenes-panel.tsx",
      "src/components/assets/props-panel.tsx",
    ];

    for (const entrypoint of entrypoints) {
      const source = readSource(entrypoint);
      expect(source, entrypoint).toContain(".isError");
      expect(source, entrypoint).toContain("referenceLoadFailed");
    }
    expect(readSource("src/components/assets/scenes-panel.tsx")).toContain(
      'sortKey === "usage" && refIndex.isError ? "name" : sortKey',
    );
    expect(readSource("src/components/assets/props-panel.tsx")).toContain(
      'sortKey === "usage" && refIndex.isError ? "name" : sortKey',
    );
  });
});
