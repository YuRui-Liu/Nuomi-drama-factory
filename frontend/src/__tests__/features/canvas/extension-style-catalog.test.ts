import { describe, expect, it } from "vitest";

import {
  EXTENSION_STYLES,
  EXTENSION_STYLES_BY_ID,
  FRAGMENT_KEYS,
  getExtensionStyle,
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
});
