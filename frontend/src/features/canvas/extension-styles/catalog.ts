import generatedCatalog from "./catalog.generated.json";

import {
  FRAGMENT_KEYS,
  type ExtensionStyle,
  type ExtensionStyleCategory,
  type ExtensionStylePromptFragment,
  type ExtensionStyleSource,
} from "./types";

export { FRAGMENT_KEYS };
export type {
  ExtensionStyle,
  ExtensionStyleCategory,
  ExtensionStyleFragmentKey,
  ExtensionStylePromptFragment,
  ExtensionStyleSource,
} from "./types";

const CATEGORIES = new Set<ExtensionStyleCategory>([
  "2d",
  "3d",
  "realistic",
  "chinese",
  "experimental",
]);
const ENTRY_KEYS = new Set([
  "id",
  "name",
  "category",
  "summary",
  "prompt_fragment",
  "use_cases",
  "preview_asset",
  "source",
  "version",
]);

function fail(detail: string): never {
  throw new Error(`Invalid extension style catalog: ${detail}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) fail(`${field} must be a non-empty string`);
  return value;
}

function requireStringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    fail(`${field} must be an array of strings`);
  }
  return value;
}

function parseFragments(value: unknown, index: number): ExtensionStylePromptFragment {
  if (!isRecord(value)) fail(`entry ${index} prompt_fragment must be an object`);
  const keys = Object.keys(value);
  if (keys.length !== FRAGMENT_KEYS.length || keys.some((key) => !FRAGMENT_KEYS.includes(key as never))) {
    fail(`entry ${index} prompt_fragment must contain exactly the six fragment keys`);
  }
  return {
    medium: requireStringArray(value.medium, `entry ${index} prompt_fragment.medium`),
    rendering: requireStringArray(value.rendering, `entry ${index} prompt_fragment.rendering`),
    lighting: requireStringArray(value.lighting, `entry ${index} prompt_fragment.lighting`),
    color: requireStringArray(value.color, `entry ${index} prompt_fragment.color`),
    camera: requireStringArray(value.camera, `entry ${index} prompt_fragment.camera`),
    constraints: requireStringArray(value.constraints, `entry ${index} prompt_fragment.constraints`),
  };
}

function parseSource(value: unknown, index: number): ExtensionStyleSource {
  if (!isRecord(value)) fail(`entry ${index} source must be an object`);
  const repository = requireString(value.repository, `entry ${index} source.repository`);
  const sourceIds = requireStringArray(value.source_ids, `entry ${index} source.source_ids`);
  if (value.license_review !== "approved") fail(`entry ${index} source.license_review must be approved`);
  const revision = requireString(value.imported_revision, `entry ${index} source.imported_revision`);
  if (!/^[0-9a-f]{40}$/.test(revision)) fail(`entry ${index} source.imported_revision is invalid`);
  return { ...value, repository, source_ids: sourceIds, license_review: "approved", imported_revision: revision };
}

function parseEntry(value: unknown, index: number): ExtensionStyle {
  if (!isRecord(value)) fail(`entry ${index} must be an object`);
  const keys = Object.keys(value);
  if (keys.length !== ENTRY_KEYS.size || keys.some((key) => !ENTRY_KEYS.has(key))) {
    fail(`entry ${index} has invalid fields`);
  }
  const id = requireString(value.id, `entry ${index} id`);
  if (!id.startsWith("drama_ext.")) fail(`entry ${index} id has invalid namespace`);
  const category = requireString(value.category, `entry ${index} category`);
  if (!CATEGORIES.has(category as ExtensionStyleCategory)) fail(`entry ${index} category is invalid`);
  const previewAsset = requireString(value.preview_asset, `entry ${index} preview_asset`);
  if (!/^\/images\/extension-styles\/[^/]+\.webp$/.test(previewAsset)) fail(`entry ${index} preview_asset is invalid`);
  return {
    id: id as ExtensionStyle["id"],
    name: requireString(value.name, `entry ${index} name`),
    category: category as ExtensionStyleCategory,
    summary: requireString(value.summary, `entry ${index} summary`),
    prompt_fragment: parseFragments(value.prompt_fragment, index),
    use_cases: requireStringArray(value.use_cases, `entry ${index} use_cases`),
    preview_asset: previewAsset as ExtensionStyle["preview_asset"],
    source: parseSource(value.source, index),
    version: requireString(value.version, `entry ${index} version`),
  };
}

function deepFreeze<T>(value: T): T {
  if (typeof value === "object" && value !== null && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}

export function parseExtensionStyleCatalog(value: unknown): readonly ExtensionStyle[] {
  if (!Array.isArray(value)) fail("top level must be an array");
  const catalog = value.map(parseEntry);
  if (new Set(catalog.map((style) => style.id)).size !== catalog.length) fail("ids must be unique");
  return deepFreeze(catalog);
}

export const EXTENSION_STYLES = parseExtensionStyleCatalog(generatedCatalog);

export const EXTENSION_STYLES_BY_ID: Readonly<Record<string, ExtensionStyle>> =
  Object.freeze(Object.assign(
    Object.create(null) as Record<string, ExtensionStyle>,
    Object.fromEntries(EXTENSION_STYLES.map((style) => [style.id, style])),
  ));

export function getExtensionStyle(id: string): ExtensionStyle | undefined {
  return Object.prototype.hasOwnProperty.call(EXTENSION_STYLES_BY_ID, id)
    ? EXTENSION_STYLES_BY_ID[id]
    : undefined;
}
