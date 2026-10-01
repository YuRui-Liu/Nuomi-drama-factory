import { describe, expect, it } from "vitest";
import { directorPresets, directorSuggestions } from "./director-config";
describe("director suggestions", () => {
  it("only proposes camera and composition changes and preserves story", () => {
    const shots = [{ id: "s1", camera_motion: "static", composition: "center", action: "保留动作", dialogue_source_ids: ["d1"] }];
    const suggestions = directorSuggestions(shots, { camera_motion: "push", composition: "thirds", method: "改写对白", performance: "克制" });
    expect(suggestions).toEqual([{ shot_id: "s1", changes: { camera_motion: "push", composition: "thirds" } }]);
    expect(shots[0].action).toBe("保留动作");
  });
  it("skips unchanged and blank preferences", () => {
    expect(directorSuggestions([{ id: "s1", camera_motion: "static", composition: "center" }], { camera_motion: "static", composition: "" })).toEqual([]);
  });
  it("provides distinct editable genre configurations without opting into adaptation", () => {
    for (const name of ['TVC 广告', '悬疑叙事', '口播带货', '动画剧情', '纪录片', '卡点 MV']) {
      const preset = directorPresets.find(p => p.name === name);
      expect(preset).toBeDefined();
      expect(preset?.data.camera_motion).toBeTruthy();
      expect(preset?.data.composition).toBeTruthy();
      expect(preset?.data.method).toBeTruthy();
      expect(preset?.data.allow_adaptation).toBe(false);
    }
    expect(new Set(directorPresets.map(p => p.data.camera_motion)).size).toBe(directorPresets.length);
  });
});
