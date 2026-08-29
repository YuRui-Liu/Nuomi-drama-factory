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
  if (!clause) return true;
  if (clause.isTypeOnly) return false;
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

const namedValueBindings = (
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
      if (!element.isTypeOnly && sourceName === importedName) {
        bindings.push(element.name.text);
      }
    }
  }
  return bindings;
};

const moduleObjectBindings = (sourceFile: ts.SourceFile, modulePath: string) => {
  const bindings: string[] = [];
  for (const declaration of productionValueImportsFrom(sourceFile, modulePath)) {
    const clause = declaration.importClause;
    if (!clause) continue;
    if (clause.name) bindings.push(clause.name.text);
    if (clause.namedBindings && ts.isNamespaceImport(clause.namedBindings)) {
      bindings.push(clause.namedBindings.name.text);
    }
  }
  return bindings;
};

const isReactLazyCall = (
  node: ts.Node,
  sourceFile: ts.SourceFile,
): node is ts.CallExpression => {
  if (!ts.isCallExpression(node)) return false;
  if (ts.isIdentifier(node.expression)) {
    return namedValueBindings(sourceFile, "react", "lazy").includes(node.expression.text);
  }
  return (
    ts.isPropertyAccessExpression(node.expression) &&
    node.expression.name.text === "lazy" &&
    ts.isIdentifier(node.expression.expression) &&
    moduleObjectBindings(sourceFile, "react").includes(node.expression.expression.text)
  );
};

const unwrapLoaderExpression = (expression: ts.Expression): ts.Expression => {
  if (
    ts.isParenthesizedExpression(expression) ||
    ts.isAsExpression(expression) ||
    ts.isTypeAssertionExpression(expression) ||
    ts.isNonNullExpression(expression) ||
    ts.isSatisfiesExpression(expression) ||
    ts.isAwaitExpression(expression)
  ) {
    return unwrapLoaderExpression(expression.expression);
  }
  return expression;
};

const loaderExpressionReturnsImport = (
  expression: ts.Expression,
  modulePath: string,
): boolean => {
  const unwrapped = unwrapLoaderExpression(expression);
  if (
    ts.isCallExpression(unwrapped) &&
    unwrapped.expression.kind === ts.SyntaxKind.ImportKeyword &&
    unwrapped.arguments.length === 1 &&
    ts.isStringLiteralLike(unwrapped.arguments[0]) &&
    unwrapped.arguments[0].text === modulePath
  ) {
    return true;
  }
  return (
    ts.isCallExpression(unwrapped) &&
    ts.isPropertyAccessExpression(unwrapped.expression) &&
    unwrapped.expression.name.text === "then" &&
    loaderExpressionReturnsImport(unwrapped.expression.expression, modulePath)
  );
};

const lazyLoaderReturnsImport = (call: ts.CallExpression, modulePath: string) => {
  const loader = call.arguments[0];
  if (!loader || (!ts.isArrowFunction(loader) && !ts.isFunctionExpression(loader))) {
    return false;
  }
  if (!ts.isBlock(loader.body)) {
    return loaderExpressionReturnsImport(loader.body, modulePath);
  }

  const returnedExpressions: ts.Expression[] = [];
  const visit = (node: ts.Node) => {
    if (node !== loader.body && ts.isFunctionLike(node)) return;
    if (ts.isReturnStatement(node) && node.expression) {
      returnedExpressions.push(node.expression);
      return;
    }
    ts.forEachChild(node, visit);
  };
  visit(loader.body);
  return (
    returnedExpressions.length > 0 &&
    returnedExpressions.every((expression) =>
      loaderExpressionReturnsImport(expression, modulePath),
    )
  );
};

const lazyBindingsForModule = (sourceFile: ts.SourceFile, modulePath: string) => {
  const bindings: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      isReactLazyCall(node.initializer, sourceFile) &&
      lazyLoaderReturnsImport(node.initializer, modulePath)
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

const containsGuardIdentifier = (root: ts.Node, guardNames: string[]) => {
  let found = false;
  const visit = (node: ts.Node) => {
    if (found) return;
    if (ts.isIdentifier(node) && guardNames.includes(node.text)) {
      found = true;
      return;
    }
    ts.forEachChild(node, visit);
  };
  visit(root);
  return found;
};

const isNullishExpression = (node: ts.Expression) =>
  node.kind === ts.SyntaxKind.NullKeyword ||
  (ts.isIdentifier(node) && node.text === "undefined");

const conditionRequiresPresentGuard = (
  condition: ts.Expression,
  conditionResult: boolean,
  guardNames: string[],
): boolean => {
  const unwrapped = unwrapLoaderExpression(condition);
  if (!containsGuardIdentifier(unwrapped, guardNames)) return false;
  if (ts.isPrefixUnaryExpression(unwrapped) && unwrapped.operator === ts.SyntaxKind.ExclamationToken) {
    return !conditionResult;
  }
  if (ts.isBinaryExpression(unwrapped)) {
    const comparesNullish =
      isNullishExpression(unwrapped.left) || isNullishExpression(unwrapped.right);
    if (comparesNullish) {
      if (
        unwrapped.operatorToken.kind === ts.SyntaxKind.EqualsEqualsToken ||
        unwrapped.operatorToken.kind === ts.SyntaxKind.EqualsEqualsEqualsToken
      ) {
        return !conditionResult;
      }
      if (
        unwrapped.operatorToken.kind === ts.SyntaxKind.ExclamationEqualsToken ||
        unwrapped.operatorToken.kind === ts.SyntaxKind.ExclamationEqualsEqualsToken
      ) {
        return conditionResult;
      }
    }
  }
  return conditionResult;
};

const nodeWithin = (node: ts.Node, container: ts.Node) =>
  node.pos >= container.pos && node.end <= container.end;

const statementReturns = (statement: ts.Statement) => {
  let found = false;
  const visit = (node: ts.Node) => {
    if (found || (node !== statement && ts.isFunctionLike(node))) return;
    if (ts.isReturnStatement(node)) {
      found = true;
      return;
    }
    ts.forEachChild(node, visit);
  };
  visit(statement);
  return found;
};

const jsxMountHasGuard = (mount: ts.Node, guardNames: string[]) => {
  let current: ts.Node = mount;
  while (current.parent) {
    const parent = current.parent;
    if (
      ts.isBinaryExpression(parent) &&
      parent.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken &&
      nodeWithin(current, parent.right) &&
      conditionRequiresPresentGuard(parent.left, true, guardNames)
    ) {
      return true;
    }
    if (ts.isConditionalExpression(parent)) {
      if (
        nodeWithin(current, parent.whenTrue) &&
        conditionRequiresPresentGuard(parent.condition, true, guardNames)
      ) {
        return true;
      }
      if (
        nodeWithin(current, parent.whenFalse) &&
        conditionRequiresPresentGuard(parent.condition, false, guardNames)
      ) {
        return true;
      }
    }
    if (ts.isIfStatement(parent)) {
      if (
        nodeWithin(current, parent.thenStatement) &&
        conditionRequiresPresentGuard(parent.expression, true, guardNames)
      ) {
        return true;
      }
      if (
        parent.elseStatement &&
        nodeWithin(current, parent.elseStatement) &&
        conditionRequiresPresentGuard(parent.expression, false, guardNames)
      ) {
        return true;
      }
    }
    if (ts.isBlock(parent)) {
      for (const statement of parent.statements) {
        if (statement.end > mount.pos) break;
        if (
          ts.isIfStatement(statement) &&
          !statement.elseStatement &&
          statementReturns(statement.thenStatement) &&
          conditionRequiresPresentGuard(statement.expression, false, guardNames)
        ) {
          return true;
        }
      }
    }
    current = parent;
  }
  return false;
};

const jsxComponentMountsAreGuarded = (
  sourceFile: ts.SourceFile,
  componentName: string,
  guardNames: string[],
) => {
  const mounts: ts.Node[] = [];
  const visit = (node: ts.Node) => {
    if (
      (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
      jsxTagName(node.tagName, sourceFile) === componentName
    ) {
      mounts.push(node);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return mounts.length > 0 && mounts.every((mount) => jsxMountHasGuard(mount, guardNames));
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
      /^[a-z]/.test(jsxTagName(node.tagName, sourceFile)) &&
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

const localComponentDefinition = (sourceFile: ts.SourceFile, componentName: string) => {
  for (const statement of sourceFile.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name?.text === componentName) {
      return statement;
    }
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (
        ts.isIdentifier(declaration.name) &&
        declaration.name.text === componentName &&
        declaration.initializer &&
        (ts.isArrowFunction(declaration.initializer) ||
          ts.isFunctionExpression(declaration.initializer))
      ) {
        return declaration.initializer;
      }
    }
  }
  return null;
};

const localJsxComponentNames = (root: ts.Node) => {
  const names: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
      ts.isIdentifier(node.tagName) &&
      /^[A-Z]/.test(node.tagName.text)
    ) {
      names.push(node.tagName.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(root);
  return names;
};

const localComponentProvidesAccessibleStatus = (
  sourceFile: ts.SourceFile,
  componentName: string,
  visited = new Set<string>(),
): boolean => {
  if (visited.has(componentName)) return false;
  visited.add(componentName);
  const definition = localComponentDefinition(sourceFile, componentName);
  if (!definition) return false;
  if (containsAccessibleStatus(definition, sourceFile)) return true;
  return localJsxComponentNames(definition).some((nestedName) =>
    localComponentProvidesAccessibleStatus(sourceFile, nestedName, visited),
  );
};

const fallbackProvidesAccessibleStatus = (root: ts.Node, sourceFile: ts.SourceFile) =>
  containsAccessibleStatus(root, sourceFile) ||
  localJsxComponentNames(root).some((componentName) =>
    localComponentProvidesAccessibleStatus(sourceFile, componentName),
  );

const isReactSuspenseTag = (tagName: string, sourceFile: ts.SourceFile) =>
  namedValueBindings(sourceFile, "react", "Suspense").includes(tagName) ||
  moduleObjectBindings(sourceFile, "react").some(
    (binding) => tagName === `${binding}.Suspense`,
  );

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
        isReactSuspenseTag(suspenseName, sourceFile) &&
        fallback?.initializer &&
        rendersComponent &&
        fallbackProvidesAccessibleStatus(fallback.initializer, sourceFile)
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
    const clause = declaration.importClause;
    if (clause?.name) bindings.push(clause.name.text);
    const namedBindings = clause?.namedBindings;
    if (!namedBindings || !ts.isNamedImports(namedBindings)) continue;
    for (const element of namedBindings.elements) {
      const sourceName = element.propertyName?.text ?? element.name.text;
      if (!element.isTypeOnly && sourceName === importedName) {
        bindings.push(element.name.text);
      }
    }
  }
  return bindings;
};

const dynamicImportCalls = (root: ts.Node, modulePath: string) => {
  const calls: ts.CallExpression[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length === 1 &&
      ts.isStringLiteralLike(node.arguments[0]) &&
      node.arguments[0].text === modulePath
    ) {
      calls.push(node);
    }
    ts.forEachChild(node, visit);
  };
  visit(root);
  return calls;
};

const functionBoundToName = (
  sourceFile: ts.SourceFile,
  bindingName: string,
): ts.ArrowFunction | ts.FunctionExpression | ts.FunctionDeclaration | null => {
  let result: ts.ArrowFunction | ts.FunctionExpression | ts.FunctionDeclaration | null = null;
  const visit = (node: ts.Node) => {
    if (result) return;
    if (ts.isFunctionDeclaration(node) && node.name?.text === bindingName) {
      result = node;
      return;
    }
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === bindingName) {
      const initializer = node.initializer;
      if (initializer && (ts.isArrowFunction(initializer) || ts.isFunctionExpression(initializer))) {
        result = initializer;
        return;
      }
      if (initializer && ts.isCallExpression(initializer)) {
        const callback = initializer.arguments.find(
          (argument): argument is ts.ArrowFunction | ts.FunctionExpression =>
            ts.isArrowFunction(argument) || ts.isFunctionExpression(argument),
        );
        if (callback) {
          result = callback;
          return;
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return result;
};

const isTrueExpression = (node: ts.Expression) =>
  unwrapLoaderExpression(node).kind === ts.SyntaxKind.TrueKeyword;

const objectSetsUploadingTrue = (node: ts.ObjectLiteralExpression) =>
  node.properties.some(
    (property) =>
      ts.isPropertyAssignment(property) &&
      property.name.getText() === "isUploading" &&
      isTrueExpression(property.initializer),
  );

const isUploadStartCall = (node: ts.CallExpression) => {
  if (
    ts.isIdentifier(node.expression) &&
    node.expression.text === "setIsUploading" &&
    node.arguments[0] &&
    isTrueExpression(node.arguments[0])
  ) {
    return true;
  }
  return node.arguments.some(
    (argument) => ts.isObjectLiteralExpression(argument) && objectSetsUploadingTrue(argument),
  );
};

const dynamicImportsFollowUploadStart = (
  sourceFile: ts.SourceFile,
  modulePath: string,
  functionName: string,
) => {
  const uploadFunction = functionBoundToName(sourceFile, functionName);
  const uploadBody = uploadFunction?.body;
  if (!uploadBody) return false;
  const imports = dynamicImportCalls(sourceFile, modulePath);
  if (imports.length === 0) return false;

  const uploadStartCalls: ts.CallExpression[] = [];
  const visit = (node: ts.Node) => {
    if (node !== uploadBody && ts.isFunctionLike(node)) return;
    if (ts.isCallExpression(node) && isUploadStartCall(node)) uploadStartCalls.push(node);
    ts.forEachChild(node, visit);
  };
  visit(uploadBody);
  return imports.every(
    (importCall) =>
      nodeWithin(importCall, uploadBody) &&
      uploadStartCalls.some((uploadStart) => uploadStart.end <= importCall.pos),
  );
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
      {
        modulePath: "@/features/superchat/superchat-panel",
        guardNames: ["chatOpen", "open", "shouldRenderPanel", "panelVisible"],
      },
      { modulePath: "./commit/CommitDialog", guardNames: ["pushState", "commitDialog"] },
      {
        modulePath: "@/pipeline-import/CreateIdentityDialog",
        guardNames: ["createIdentitySource", "createIdentityNodeId"],
      },
      {
        modulePath: "@/pipeline-import/CompareDialog",
        guardNames: ["comparePair", "compareDialog"],
      },
      {
        modulePath: "@/pipeline-import/MaskEditor",
        guardNames: ["maskTarget", "maskEditorState"],
      },
    ];

    for (const { modulePath, guardNames } of optionalModules) {
      expect(productionValueImportsFrom(freezoneShell, modulePath), modulePath).toHaveLength(0);
      expect(hasDynamicImport(freezoneShell, modulePath), modulePath).toBe(true);

      const lazyBindings = lazyBindingsForModule(freezoneShell, modulePath);
      expect(lazyBindings.length, `${modulePath} should be loaded through React.lazy`).toBeGreaterThan(0);
      expect(
        lazyBindings.some(
          (binding) =>
            hasAccessibleSuspenseBoundary(freezoneShell, binding) &&
            jsxComponentMountsAreGuarded(freezoneShell, binding, guardNames),
        ),
        `${modulePath} should mount only after interaction with an accessible fallback`,
      ).toBe(true);
    }
  });

  it("uses a static canvas node domain import without lazy loading", () => {
    const freezoneShell = readSourceFile("src/features/freezone/FreezoneShell.tsx");
    const canvasNodeModule = "@/features/canvas/domain/canvasNodes";

    // This hot-path domain module must never be split behind import(), awaited or otherwise.
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
    expect(dynamicImportsFollowUploadStart(videoNode, transcodeModule, "processFile")).toBe(
      true,
    );
  });
});
