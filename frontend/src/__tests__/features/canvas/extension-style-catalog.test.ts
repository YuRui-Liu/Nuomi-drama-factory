import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { FreezoneStyleTemplate } from "@/api/ops";
import {
  EXTENSION_STYLES,
  EXTENSION_STYLES_BY_ID,
  FRAGMENT_KEYS,
  getExtensionStyle,
  parseExtensionStyleCatalog,
} from "@/features/canvas/extension-styles/catalog";
import { describeStyleSelection } from "@/features/canvas/nodes/StylePickerPopover";

const EXTENSION_STYLE_MODULE_DIR = resolve(
  process.cwd(),
  "src/features/canvas/extension-styles",
);

function extensionStyleModuleSources(
  directory = EXTENSION_STYLE_MODULE_DIR,
): Array<[string, string]> {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) return extensionStyleModuleSources(path);
    if (!entry.isFile() || !/\.(?:json|ts|tsx)$/.test(entry.name)) return [];
    return [[path, readFileSync(path, "utf8")]];
  });
}

describe("extension style catalog", () => {
  it("exposes namespaced styles in the backend fragment order", () => {
    expect(EXTENSION_STYLES.length).toBeGreaterThan(0);
    expect(FRAGMENT_KEYS).toEqual([
      "medium",
      "rendering",
      "lighting",
      "color",
      "camera",
      "constraints",
    ]);
    expect(EXTENSION_STYLES.every(({ id }) => id.startsWith("drama_ext."))).toBe(true);
    expect(new Set(EXTENSION_STYLES.map(({ id }) => id)).size).toBe(
      EXTENSION_STYLES.length,
    );
  });

  it("preserves category, source, and local preview metadata", () => {
    for (const style of EXTENSION_STYLES) {
      expect(["2d", "3d", "realistic", "chinese", "experimental"]).toContain(style.category);
      expect(style.preview_asset).toMatch(/^\/images\/extension-styles\/[^/]+\.webp$/);
      expect(style.source.repository).toBe("freestylefly/awesome-gpt-image-2");
      expect(style.source.license_review).toBe("approved");
    }
  });

  it("supports lookup by id", () => {
    const id = "drama_ext.japanese_cel_animation";
    expect(getExtensionStyle(id)).toBe(EXTENSION_STYLES_BY_ID[id]);
    expect(getExtensionStyle(id)?.name).toBe("日系赛璐璐");
    const jinshi = getExtensionStyle("drama_ext.jinshi_ink_suspense");
    expect(jinshi?.name).toBe("金石证痕");
    expect(jinshi?.category).toBe("chinese");
    expect(jinshi?.preview_asset).toBe(
      "/images/extension-styles/jinshi-ink-suspense.webp",
    );
    expect(getExtensionStyle("drama_ext.missing")).toBeUndefined();
  });

  it("keeps the catalog bundle offline-only at runtime", () => {
    const forbiddenRuntimeDependencies = [
      ["fetch", /\bfetch\s*\(/i],
      ["absolute HTTP URL", /https?:\/\//i],
      ["GitHub URL", /(?:www\.)?github\.com/i],
      ["runtime API import", /\bfrom\s+["']@\/api(?:\/|["'])/i],
      ["dynamic runtime API import", /\bimport\s*\(\s*["']@\/api(?:\/|["'])/i],
      ["runtime API call", /\b(?:apiCall|listFreezoneStyleTemplates)\s*\(/],
      ["HTTP client", /\b(?:XMLHttpRequest|axios|ky)\b/],
    ] as const;

    for (const [fileName, source] of extensionStyleModuleSources()) {
      for (const [dependencyName, pattern] of forbiddenRuntimeDependencies) {
        expect(source, `${fileName} must not contain ${dependencyName}`).not.toMatch(pattern);
      }
    }
  });

  it("preserves the existing style-template interface and display name", () => {
    const existingStyle: FreezoneStyleTemplate = {
      id: "existing.ink-cinema",
      label: "水墨电影感",
      style_prompt: "cinematic ink wash",
      author: "Nuomi Drama Factory",
      category: "现有风格",
    };

    const selected = describeStyleSelection(existingStyle.id, [existingStyle]);

    expect(selected).toBe(existingStyle);
    expect(selected?.label).toBe("水墨电影感");
    expect(selected?.style_prompt).toBe("cinematic ink wash");
  });

  it("publishes a deeply frozen catalog view", () => {
    const style = EXTENSION_STYLES[0];
    expect(Object.isFrozen(EXTENSION_STYLES)).toBe(true);
    expect(Object.isFrozen(style)).toBe(true);
    expect(Object.isFrozen(style.prompt_fragment)).toBe(true);
    expect(Object.isFrozen(style.prompt_fragment.medium)).toBe(true);
    expect(Object.isFrozen(style.source)).toBe(true);
    expect(Object.isFrozen(style.source.source_ids)).toBe(true);
  });

  it.each([
    ["non-array catalog", {}],
    ["invalid category", [{ ...EXTENSION_STYLES[0], category: "photo" }]],
    ["extra fragment key", [{
      ...EXTENSION_STYLES[0],
      prompt_fragment: {
        ...EXTENSION_STYLES[0].prompt_fragment,
        extra: ["bad"],
      },
    }]],
    ["non-string fragment", [{
      ...EXTENSION_STYLES[0],
      prompt_fragment: {
        ...EXTENSION_STYLES[0].prompt_fragment,
        medium: [42],
      },
    }]],
    ["missing source field", [{ ...EXTENSION_STYLES[0], source: {} }]],
    ["non-string use case", [{ ...EXTENSION_STYLES[0], use_cases: [1] }]],
  ])("rejects malformed runtime data: %s", (_label, value) => {
    expect(() => parseExtensionStyleCatalog(value)).toThrow(/extension style catalog/i);
  });
});
