import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("paid asset planning route gates", () => {
  it.each([
    "src/routes/_app/projects.$project/episodes.tsx",
    "src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx",
  ])("guards every mutation before submission in %s", (path) => {
    const source = readFileSync(path, "utf8");
    expect(source).toContain("assetPlanningBlockReason(directorPlans)");
    for (const kind of ["Identities", "Scenes", "Props"]) {
      const handler = source.slice(source.indexOf(`const handlePlan${kind} = async`));
      expect(handler.slice(0, handler.indexOf("mutateAsync"))).toContain("if (planningBlocked)");
    }
    expect(source).toContain("<AssetPlanningPrerequisite reason={planningBlocked}");
    expect(source).toContain("<AssetPlanningFailure");
    for (const kind of ["identity", "scene", "prop"]) expect(source).toContain(`${kind}Task.stream.error`);
    for (const kind of ["character", "scene", "prop"]) expect(source).toContain(`reportPlanningError("${kind}", res.error)`);
  });
  it("shares the list gate with asset-sheet actions and provides the script route", () => {
    const source = readFileSync("src/routes/_app/projects.$project/episodes.tsx", "utf8");
    expect(source.match(/<EpisodePlanShortcut disabled=\{!!planningBlocked\}/g)).toHaveLength(3);
    expect(source).toMatch(/actions=\{<EpisodeListItem[^\n]+actionsOnly/);
    expect(source).toContain('to: "/projects/$project/episodes/$episode/script"');
  });
});
