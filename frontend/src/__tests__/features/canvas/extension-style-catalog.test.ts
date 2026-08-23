import { describe, expect, it } from "vitest";

import {
  EXTENSION_STYLES,
  EXTENSION_STYLES_BY_ID,
  FRAGMENT_KEYS,
  getExtensionStyle,
  parseExtensionStyleCatalog,
} from "@/features/canvas/extension-styles/catalog";

describe("extension style catalog", () => {
  it("exposes exactly 18 namespaced styles in the backend fragment order", () => {
    expect(EXTENSION_STYLES).toHaveLength(18);
    expect(FRAGMENT_KEYS).toEqual([
      "medium",
      "rendering",
      "lighting",
      "color",
      "camera",
      "constraints",
    ]);
    expect(EXTENSION_STYLES.every(({ id }) => id.startsWith("drama_ext."))).toBe(true);
    expect(new Set(EXTENSION_STYLES.map(({ id }) => id)).size).toBe(18);
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
    expect(getExtensionStyle("drama_ext.missing")).toBeUndefined();
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
