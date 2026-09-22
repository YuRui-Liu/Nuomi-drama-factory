import { describe, expect, it } from "vitest";
import { companionTopPx } from "@/features/companion/companion-position";

describe("companion navigation exclusion", () => {
  it("keeps defaults and saved top positions below desktop navigation", () => {
    expect(companionTopPx(8, 1280, 800)).toBe(64);
    expect(companionTopPx(-100, 1280, 800)).toBe(64);
  });
  it("protects the two-row narrow header", () => {
    expect(companionTopPx(0, 800, 600)).toBe(104);
  });
  it("retains valid positions and clamps the bottom", () => {
    expect(companionTopPx(300, 1280, 800)).toBe(300);
    expect(companionTopPx(900, 1280, 800)).toBe(720);
  });
  it("never moves over navigation in a tiny viewport", () => {
    expect(companionTopPx(0, 800, 90)).toBe(104);
  });
});
