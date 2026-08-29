import { describe, expect, it } from "vitest";
import {
  FREEZONE_ENTRY_SOURCE,
  FREEZONE_MAX_BYTES,
  assertFreezoneBudget,
  findFreezoneEntry,
} from "../../../scripts/check-freezone-bundle-budget.mjs";

describe("freezone bundle budget", () => {
  it("finds the freezone route entry by source path", () => {
    const entry = { file: "assets/freezone.js", src: FREEZONE_ENTRY_SOURCE };

    expect(findFreezoneEntry({ freezone: entry })).toBe(entry);
  });

  it("finds the router-generated entry when the manifest omits src", () => {
    const entry = {
      file: "assets/freezone.lazy-hash.js",
      name: "freezone.lazy",
      isDynamicEntry: true,
    };

    expect(findFreezoneEntry({ "_freezone.lazy-hash.js": entry })).toBe(entry);
  });

  it("rejects an entry larger than the approved budget", () => {
    expect(() => assertFreezoneBudget(FREEZONE_MAX_BYTES + 1)).toThrow(/exceeds/);
    expect(() => assertFreezoneBudget(FREEZONE_MAX_BYTES)).not.toThrow();
  });
});
