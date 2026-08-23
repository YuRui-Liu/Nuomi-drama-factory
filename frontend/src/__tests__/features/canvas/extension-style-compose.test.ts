import { describe, expect, it } from "vitest";

import { composeImagePrompt } from "@/features/canvas/extension-styles/composePrompt";

const STYLE_ID = "drama_ext.japanese_cel_animation";
const SUFFIX =
  "[Extension style: 日系赛璐璐]\n" +
  "Japanese cel animation medium, crisp inked contours, clean cel shading, graphic key light with controlled rim highlights, bright harmonized palette, precise color blocks, animation-grade composition, clean lens separation, high-fidelity finish, preserve identity and wardrobe/props/action/setting from base prompt";

describe("composeImagePrompt", () => {
  it("appends the style header and fragments in fixed order", () => {
    expect(composeImagePrompt("一名演员站在雨中", STYLE_ID)).toBe(
      `一名演员站在雨中\n\n${SUFFIX}`,
    );
  });

  it.each([null, undefined, "", "drama_ext.unknown"])(
    "returns the prompt byte-for-byte for absent or unknown id %s",
    (id) => {
      const prompt = "  leading\nbody\n\n";
      expect(composeImagePrompt(prompt, id)).toBe(prompt);
    },
  );

  it("does not trim or normalize prompt whitespace", () => {
    const prompt = "\n  leading and trailing  \n";
    expect(composeImagePrompt(prompt, STYLE_ID)).toBe(`${prompt}\n\n${SUFFIX}`);
  });

  it("omits meaningless leading blank lines for an empty prompt", () => {
    expect(composeImagePrompt("", STYLE_ID)).toBe(SUFFIX);
  });
});
