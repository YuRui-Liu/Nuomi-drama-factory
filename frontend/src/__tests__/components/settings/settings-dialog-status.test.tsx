import { describe, expect, it } from "vitest";
import { getSettingsConnectionState } from "@/components/settings/settings-dialog";

describe("settings connection state", () => {
  it("keeps unresolved and failed requests distinct from missing configuration", () => {
    expect(getSettingsConnectionState({ isPending: true }, false)).toBe("loading");
    expect(getSettingsConnectionState({ isError: true }, false)).toBe("error");
    expect(getSettingsConnectionState({ isError: true }, true)).toBe("error");
    expect(getSettingsConnectionState({}, false)).toBe("missing");
    expect(getSettingsConnectionState({}, true)).toBe("ready");
  });
  it("treats an unconfigured compatibility gateway as optional", () => {
    expect(getSettingsConnectionState({}, false, true)).toBe("optional");
    expect(getSettingsConnectionState({ isPending: true }, false, true)).toBe("loading");
    expect(getSettingsConnectionState({ isError: true }, false, true)).toBe("error");
  });
});
