// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

function read(path: string) {
  return readFileSync(path, "utf8");
}

describe("script workflow canonical contract", () => {
  it("directs an empty Beat page back to screenplay semantics instead of generating line beats", () => {
    const route = read(
      "src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx",
    );

    expect(route).not.toContain("useGenerateScript");
    expect(route).not.toContain("useEpisodeDetail");
    expect(route).not.toContain("identityPlanReady");
    expect(route).toContain("返回剧本校对页完成导演拆解");
    expect(route).toContain('to="/projects/$project/episodes/$episode/script"');
    expect(route).not.toContain('"script_writer"');
    expect(route).not.toContain('"literal_script_writer"');
  });

  it("keeps director review reachable when no legacy Beats exist", () => {
    const route = read(
      "src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx",
    );

    expect(route).toContain("useDirectorPlans");
    expect(route).toContain("hasPendingDirectorPlan");
    expect(
      route.indexOf('if (workbenchMode === "director" ||'),
    ).toBeLessThan(
      route.lastIndexOf("if (beats.length === 0)"),
    );
  });

  it("enters narrative production from an active director plan without legacy Beats", () => {
    const route = read(
      "src/routes/_app/projects.$project/episodes.$episode/beats.lazy.tsx",
    );

    expect(route).toContain("useNarrativeGroups");
    expect(route).toContain("hasActiveDirectorPlan");
    expect(route).toContain("hasProductionGroups");
    expect(
      route.indexOf('if (workbenchMode === "groups" && hasProductionGroups)'),
    ).toBeLessThan(route.lastIndexOf("if (beats.length === 0)"));
  });

  it("contains no callable legacy script generation query", () => {
    const queries = read("src/lib/queries/scripts.ts");
    expect(queries).not.toContain("script/generate");
    expect(queries).not.toContain("useGenerateScript");
  });

  it("removes line-Beat product copy from both locale bundles", () => {
    for (const locale of ["zh", "en"]) {
      const messages = read(`public/locales/${locale}/translation.json`);
      expect(messages).not.toContain('"generateLineByLine"');
      expect(messages).not.toContain('"literal_script_writer"');
      expect(messages).not.toContain('"script_writer"');
      expect(messages).not.toContain("一行一个分镜");
      expect(messages).not.toContain("line-by-line beat generation");
    }
    const features = read("src/lib/feature-models.ts");
    expect(features).not.toContain("LITERAL_BEAT_META");
    expect(features).not.toContain("DC-literal-beat-meta-LLM");
  });

  it("exposes the v2-storage NiceGUI script workbench controls in the Script tab", () => {
    const route = read(
      "src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx",
    );

    expect(route).toContain("useEpisodeDetail");
    expect(route).toContain("useProject");
    expect(route).toContain("beat_source_text");
    expect(route).not.toContain("useGenerateScript");
    expect(route).not.toContain('useGenerationCreditCost("feature", "script_writer")');
    expect(route).toContain("useGenerateRewrite");
    expect(route).toContain('spine_template === "narrated"');
    expect(route).toContain("initializedSourceRef");
    expect(route).toContain("handleGenerateRewrite");
    expect(route).not.toContain("scriptTask.start");
    expect(route).not.toContain("scriptTask.stop");
    expect(route).toContain("identityTask = useTaskController");
    expect(route).toContain("TASK_TYPES.IDENTITY_PLANNER");
    expect(route).toContain("identityTask.start");
    expect(route).not.toContain("getScriptReviewFeedback");
    expect(route).not.toContain("generateScript");
    expect(route).toContain("generateRewrite");
    expect(route).not.toContain("handleRefreshScript");
    expect(route).not.toContain("handleLoadScript");
    expect(route).not.toContain("getScriptReloadFeedback");
    expect(route).not.toContain("refreshScript");
    expect(route).not.toContain("loadScript");
    expect(route).not.toContain("FolderOpen");
    expect(route).toContain("ScreenplayWorkbench");
    expect(route).not.toContain("modeLiteral");
    expect(route).not.toContain("useRawContent");
    expect(route).not.toContain("useAdaptedContent");
    expect(route).not.toContain("useGenerateStaging");
    expect(route).not.toContain("CONTENT_REWRITER");
    expect(route).not.toContain('value="json"');
  });

  it("keeps unsupported main-branch script endpoints out of the v2-storage query layer", () => {
    const queries = read("src/lib/queries/scripts.ts");
    const queryKeys = read("src/lib/query-keys.ts");

    expect(queries).not.toContain("useGenerateLiteralScript");
    expect(queries).not.toContain("usePolishPatches");
    expect(queries).not.toContain("useRawContent");
    expect(queries).not.toContain("useAdaptedContent");
    expect(queries).not.toContain("useSaveAdaptedContent");
    expect(queries).not.toContain("useDeleteAdaptedContent");
    expect(queries).not.toContain("useGenerateStaging");

    expect(queries).not.toContain("literal-script/generate");
    expect(queries).not.toContain("raw-content");
    expect(queries).not.toContain("adapted-content");
    expect(queries).toContain("useGenerateRewrite");
    expect(queries).toContain("rewrite/generate");
    expect(queries).not.toContain("staging/generate");
    expect(queries).not.toContain("polish-patches");

    expect(queryKeys).not.toContain("raw-content");
    expect(queryKeys).not.toContain("adapted-content");
  });

  it("uses the screenplay semantic workbench as the Script tab production entry", () => {
    const route = read(
      "src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx",
    );

    expect(route).toContain("ScreenplayWorkbench");
    expect(route).toContain("专业剧本语义模式");
    expect(route).not.toContain("getScriptReviewFeedback");
  });

  it("replaces the legacy empty Beat preview with the completed director plan summary", () => {
    const route = read(
      "src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx",
    );

    expect(route).toContain("useDirectorPlans");
    expect(route).toContain("directorPlan ?");
    expect(route).toContain("镜头方案已生成");
    expect(route).toContain("待人工审核");
    expect(route).toContain("审核镜头方案");
  });

  it("uses screenplay semantics for the episode header instead of legacy Beat counts", () => {
    const route = read("src/routes/_app/projects.$project/episodes.tsx");
    const zh = read("public/locales/zh/translation.json");
    const en = read("public/locales/en/translation.json");

    expect(route).toContain("useScreenplaySemantics");
    expect(route).toContain("semanticRevision");
    expect(route).toContain("beats: semanticBeatCount");
    expect(route).toContain("scenes: semanticSceneCount");
    expect(zh).toContain("{{beats}} 个戏剧节拍");
    expect(en).toContain("{{beats}} dramatic beats");
  });

  it("passes narrated rewrite line count and character range controls to the API", () => {
    const route = read(
      "src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx",
    );
    const zh = read("public/locales/zh/translation.json");
    const en = read("public/locales/en/translation.json");

    expect(route).toContain("rewriteTargetBeats");
    expect(route).toContain("rewriteBeatCharsMin");
    expect(route).toContain("rewriteBeatCharsMax");
    expect(route).toContain("episode.script.rewriteTargetBeats");
    expect(route).toContain("episode.script.rewriteBeatCharsMin");
    expect(route).toContain("episode.script.rewriteBeatCharsMax");
    expect(route).toContain("target_beats: rewriteTargetBeats");
    expect(route).toContain("beat_chars_min: rewriteBeatCharsMin");
    expect(route).toContain("beat_chars_max: rewriteBeatCharsMax");
    expect(route).not.toContain("generateRewrite.mutateAsync({})");
    expect(zh).toContain('"rewriteTargetBeats"');
    expect(zh).toContain('"rewriteBeatCharsMin"');
    expect(zh).toContain('"rewriteBeatCharsMax"');
    expect(en).toContain('"rewriteTargetBeats"');
    expect(en).toContain('"rewriteBeatCharsMin"');
    expect(en).toContain('"rewriteBeatCharsMax"');
  });

  it("surfaces backend task admission errors for script planning actions", () => {
    const route = read(
      "src/routes/_app/projects.$project/episodes.$episode/script.lazy.tsx",
    );

    expect(route).toContain("backendErrorToastMessage");
    expect(route).toMatch(
      /const handlePlanIdentities[\s\S]*catch \(err\)[\s\S]*toast\.error\(backendErrorToastMessage\(err, t\)\)/,
    );
    expect(route).toMatch(
      /const handlePlanScenes[\s\S]*catch \(err\)[\s\S]*toast\.error\(backendErrorToastMessage\(err, t\)\)/,
    );
    expect(route).toMatch(
      /const handlePlanProps[\s\S]*catch \(err\)[\s\S]*toast\.error\(backendErrorToastMessage\(err, t\)\)/,
    );
  });
});
