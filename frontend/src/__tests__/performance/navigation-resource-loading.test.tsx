import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const readSource = (relativePath: string) => {
  const path = resolve(process.cwd(), relativePath);
  expect(existsSync(path), `${relativePath} should exist`).toBe(true);
  const source = readFileSync(path, "utf8");
  expect(source.trim(), `${relativePath} should not be empty`).not.toBe("");
  return source;
};
const readSourceFile = (relativePath: string) =>
  ts.createSourceFile(
    relativePath,
    readSource(relativePath),
    ts.ScriptTarget.Latest,
    true,
    relativePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );

const modulePathOf = (node: ts.ImportDeclaration) =>
  ts.isStringLiteralLike(node.moduleSpecifier) ? node.moduleSpecifier.text : null;

const isProductionValueImport = (node: ts.ImportDeclaration) => {
  const clause = node.importClause;
  if (!clause || clause.isTypeOnly) return false;
  if (clause.name) return true;
  if (!clause.namedBindings) return false;
  if (ts.isNamespaceImport(clause.namedBindings)) return true;
  return clause.namedBindings.elements.some((element) => !element.isTypeOnly);
};

const productionValueImportsFrom = (sourceFile: ts.SourceFile, modulePath: string) =>
  sourceFile.statements.filter(
    (statement): statement is ts.ImportDeclaration =>
      ts.isImportDeclaration(statement) &&
      modulePathOf(statement) === modulePath &&
      isProductionValueImport(statement),
  );

const hasDynamicImport = (root: ts.Node, modulePath: string) => {
  let found = false;
  const visit = (node: ts.Node) => {
    if (found) return;
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length === 1 &&
      ts.isStringLiteralLike(node.arguments[0]) &&
      node.arguments[0].text === modulePath
    ) {
      found = true;
      return;
    }
    ts.forEachChild(node, visit);
  };
  visit(root);
  return found;
};

const isLazyCall = (node: ts.Node): node is ts.CallExpression =>
  ts.isCallExpression(node) &&
  (ts.isIdentifier(node.expression)
    ? node.expression.text === "lazy"
    : ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === "lazy");

const lazyBindingsForModule = (sourceFile: ts.SourceFile, modulePath: string) => {
  const bindings: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      isLazyCall(node.initializer) &&
      hasDynamicImport(node.initializer, modulePath)
    ) {
      bindings.push(node.name.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return bindings;
};

const jsxTagName = (node: ts.JsxTagNameExpression, sourceFile: ts.SourceFile) =>
  node.getText(sourceFile);

const containsJsxComponent = (
  root: ts.Node,
  componentName: string,
  sourceFile: ts.SourceFile,
) => {
  let found = false;
  const visit = (node: ts.Node) => {
    if (found) return;
    if (
      (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
      jsxTagName(node.tagName, sourceFile) === componentName
    ) {
      found = true;
      return;
    }
    ts.forEachChild(node, visit);
  };
  visit(root);
  return found;
};

const literalJsxAttribute = (
  element: ts.JsxOpeningLikeElement,
  attributeName: string,
  sourceFile: ts.SourceFile,
) => {
  const attribute = element.attributes.properties.find(
    (property): property is ts.JsxAttribute =>
      ts.isJsxAttribute(property) && property.name.getText(sourceFile) === attributeName,
  );
  if (!attribute?.initializer) return null;
  if (ts.isStringLiteral(attribute.initializer)) return attribute.initializer.text;
  if (
    ts.isJsxExpression(attribute.initializer) &&
    attribute.initializer.expression &&
    ts.isStringLiteralLike(attribute.initializer.expression)
  ) {
    return attribute.initializer.expression.text;
  }
  return null;
};

const containsAccessibleStatus = (root: ts.Node, sourceFile: ts.SourceFile) => {
  let found = false;
  const visit = (node: ts.Node) => {
    if (found) return;
    if (
      (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
      literalJsxAttribute(node, "role", sourceFile) === "status" &&
      literalJsxAttribute(node, "aria-live", sourceFile) === "polite"
    ) {
      found = true;
      return;
    }
    ts.forEachChild(node, visit);
  };
  visit(root);
  return found;
};

const hasAccessibleSuspenseBoundary = (
  sourceFile: ts.SourceFile,
  componentName: string,
) => {
  let found = false;
  const visit = (node: ts.Node) => {
    if (found) return;
    if (ts.isJsxElement(node)) {
      const suspenseName = jsxTagName(node.openingElement.tagName, sourceFile);
      const fallback = node.openingElement.attributes.properties.find(
        (property): property is ts.JsxAttribute =>
          ts.isJsxAttribute(property) && property.name.getText(sourceFile) === "fallback",
      );
      const rendersComponent = node.children.some((child) =>
        containsJsxComponent(child, componentName, sourceFile),
      );
      if (
        (suspenseName === "Suspense" || suspenseName.endsWith(".Suspense")) &&
        fallback?.initializer &&
        rendersComponent &&
        containsAccessibleStatus(fallback.initializer, sourceFile)
      ) {
        found = true;
        return;
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return found;
};

const importedValueBindings = (
  sourceFile: ts.SourceFile,
  modulePath: string,
  importedName: string,
) => {
  const bindings: string[] = [];
  for (const declaration of productionValueImportsFrom(sourceFile, modulePath)) {
    const namedBindings = declaration.importClause?.namedBindings;
    if (!namedBindings || !ts.isNamedImports(namedBindings)) continue;
    for (const element of namedBindings.elements) {
      const sourceName = element.propertyName?.text ?? element.name.text;
      if (!element.isTypeOnly && sourceName === importedName) bindings.push(element.name.text);
    }
  }
  return bindings;
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

  it("defers optional freezone features until interaction", () => {
    const freezoneShell = readSourceFile("src/features/freezone/FreezoneShell.tsx");
    const optionalModules = [
      "@/features/superchat/superchat-panel",
      "./commit/CommitDialog",
      "@/pipeline-import/CreateIdentityDialog",
      "@/pipeline-import/CompareDialog",
      "@/pipeline-import/MaskEditor",
    ];

    for (const modulePath of optionalModules) {
      expect(productionValueImportsFrom(freezoneShell, modulePath), modulePath).toHaveLength(0);
      expect(hasDynamicImport(freezoneShell, modulePath), modulePath).toBe(true);

      const lazyBindings = lazyBindingsForModule(freezoneShell, modulePath);
      expect(lazyBindings.length, `${modulePath} should be loaded through React.lazy`).toBeGreaterThan(0);
      expect(
        lazyBindings.some((binding) =>
          hasAccessibleSuspenseBoundary(freezoneShell, binding),
        ),
        `${modulePath} should render inside an accessible Suspense fallback`,
      ).toBe(true);
    }
  });

  it("uses one static canvas node domain import in the freezone shell", () => {
    const freezoneShell = readSourceFile("src/features/freezone/FreezoneShell.tsx");
    const canvasNodeModule = "@/features/canvas/domain/canvasNodes";

    expect(hasDynamicImport(freezoneShell, canvasNodeModule)).toBe(false);
    const importsBothRuntimeValues = productionValueImportsFrom(
      freezoneShell,
      canvasNodeModule,
    ).some((declaration) => {
      const namedBindings = declaration.importClause?.namedBindings;
      if (!namedBindings || !ts.isNamedImports(namedBindings)) return false;
      const runtimeNames = namedBindings.elements
        .filter((element) => !element.isTypeOnly)
        .map((element) => element.propertyName?.text ?? element.name.text);
      return (
        runtimeNames.includes("CANVAS_NODE_TYPES") &&
        runtimeNames.includes("DEFAULT_NODE_WIDTH")
      );
    });
    expect(importsBothRuntimeValues).toBe(true);
  });

  it("defers the node tool dialog and annotate editor", () => {
    const canvas = readSourceFile("src/features/canvas/Canvas.tsx");
    const nodeToolDialog = readSourceFile("src/features/canvas/ui/NodeToolDialog.tsx");
    const lazyNodeToolDialog = readSourceFile(
      "src/features/canvas/ui/LazyNodeToolDialog.tsx",
    );

    expect(productionValueImportsFrom(canvas, "./ui/NodeToolDialog")).toHaveLength(0);
    const lazyDialogBindings = importedValueBindings(
      canvas,
      "./ui/LazyNodeToolDialog",
      "LazyNodeToolDialog",
    );
    expect(lazyDialogBindings.length).toBeGreaterThan(0);
    expect(
      lazyDialogBindings.some((binding) => containsJsxComponent(canvas, binding, canvas)),
    ).toBe(true);

    const nodeToolBindings = lazyBindingsForModule(lazyNodeToolDialog, "./NodeToolDialog");
    expect(nodeToolBindings.length).toBeGreaterThan(0);
    expect(
      nodeToolBindings.some((binding) =>
        hasAccessibleSuspenseBoundary(lazyNodeToolDialog, binding),
      ),
    ).toBe(true);

    const annotateModule = "./tool-editors/AnnotateToolEditor";
    expect(productionValueImportsFrom(nodeToolDialog, annotateModule)).toHaveLength(0);
    const annotateBindings = lazyBindingsForModule(nodeToolDialog, annotateModule);
    expect(annotateBindings.length).toBeGreaterThan(0);
    expect(
      annotateBindings.some((binding) =>
        containsJsxComponent(nodeToolDialog, binding, nodeToolDialog),
      ),
    ).toBe(true);
  });

  it("loads video transcoding only after a video upload starts", () => {
    const videoNode = readSourceFile("src/features/canvas/nodes/VideoNode.tsx");
    const transcodeModule = "@/features/canvas/application/videoTranscode";

    expect(productionValueImportsFrom(videoNode, transcodeModule)).toHaveLength(0);
    expect(hasDynamicImport(videoNode, transcodeModule)).toBe(true);
  });
});
