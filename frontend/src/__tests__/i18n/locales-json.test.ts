// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function collectKeys(value: unknown, prefix = ""): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return prefix ? [prefix] : [];
  }

  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    collectKeys(child, prefix ? `${prefix}.${key}` : key),
  );
}

function collectStrings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (!value || typeof value !== "object") return [];
  return Object.values(value as Record<string, unknown>).flatMap(collectStrings);
}

function collectPlaceholders(
  value: unknown,
  prefix = "",
): Record<string, string[]> {
  if (typeof value === "string") {
    const placeholders = [...value.matchAll(/{{\s*([^{}]+?)\s*}}/g)].map(
      ([, name]) => name.trim(),
    );
    return { [prefix]: [...new Set(placeholders)].sort() };
  }
  if (Array.isArray(value)) {
    return Object.fromEntries(
      value.flatMap((child, index) =>
        Object.entries(collectPlaceholders(child, prefix ? `${prefix}.${index}` : `${index}`)),
      ),
    );
  }
  if (!value || typeof value !== "object") return {};

  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
      Object.entries(collectPlaceholders(child, prefix ? `${prefix}.${key}` : key)),
    ),
  );
}

describe("locale translation files", () => {
  it.each(["en", "zh"])("%s translation JSON is valid", (language) => {
    const content = readFileSync(`public/locales/${language}/translation.json`, "utf8");

    expect(() => JSON.parse(content)).not.toThrow();
  });

  it("keeps zh and en translation key sets aligned", () => {
    const zh = JSON.parse(readFileSync("public/locales/zh/translation.json", "utf8"));
    const en = JSON.parse(readFileSync("public/locales/en/translation.json", "utf8"));
    const zhKeys = new Set(collectKeys(zh));
    const enKeys = new Set(collectKeys(en));

    expect([...zhKeys].filter((key) => !enKeys.has(key)).sort()).toEqual([]);
    expect([...enKeys].filter((key) => !zhKeys.has(key)).sort()).toEqual([]);
  });

  it("uses the NuomiDrama product name and the new navigation language", () => {
    const zh = JSON.parse(readFileSync("public/locales/zh/translation.json", "utf8"));
    const en = JSON.parse(readFileSync("public/locales/en/translation.json", "utf8"));

    expect(zh.app.title).toBe("NuomiDrama");
    expect(en.app.title).toBe("NuomiDrama");
    expect(zh.settings.aboutAppName).toBe("NuomiDrama");
    expect(en.settings.aboutAppName).toBe("NuomiDrama");

    expect(zh.nav).toMatchObject({
      xiaji: "项目中心",
      ingest: "剧本导入",
      assets: "资产中心",
      episodes: "剧集制作",
      freezone: "创作画布",
      styles: "视觉风格",
      tasks: "任务中心",
      aiAssistant: "糯米助手",
    });
    expect(en.nav).toMatchObject({
      xiaji: "Project Center",
      ingest: "Script Import",
      assets: "Asset Center",
      episodes: "Episode Production",
      freezone: "Creation Canvas",
      styles: "Visual Style",
      tasks: "Task Center",
      aiAssistant: "Nuomi Assistant",
    });
    expect(zh.auth.community.heading).toBe("作品广场");
    expect(en.auth.community.heading).toBe("Showcase");
  });

  it("does not expose legacy product names in locale strings", () => {
    const zh = JSON.parse(readFileSync("public/locales/zh/translation.json", "utf8"));
    const en = JSON.parse(readFileSync("public/locales/en/translation.json", "utf8"));
    const legacyProductLanguage =
      /DramaClaw|SuperTale|Xia Director|Freezone|XiPaint|\bDC\b|虾导|虾塘|虾画|虾镜|虾料|虾格|虾条|虾集/;

    expect(collectStrings(zh).filter((value) => legacyProductLanguage.test(value))).toEqual([]);
    expect(collectStrings(en).filter((value) => legacyProductLanguage.test(value))).toEqual([]);
  });

  it("deduplicates and sorts interpolation placeholders", () => {
    expect(collectPlaceholders({ sample: "{{ b }} {{a}} {{a}}" })).toEqual({
      sample: ["a", "b"],
    });
  });

  it("collects interpolation placeholders from array string leaves", () => {
    expect(
      collectPlaceholders({ waitingResponses: ["Waiting for {{name}}", "Done"] }),
    ).toEqual({
      "waitingResponses.0": ["name"],
      "waitingResponses.1": [],
    });
  });

  it("keeps zh and en interpolation placeholders aligned by key", () => {
    const zh = JSON.parse(readFileSync("public/locales/zh/translation.json", "utf8"));
    const en = JSON.parse(readFileSync("public/locales/en/translation.json", "utf8"));

    expect(collectPlaceholders(zh)).toEqual(collectPlaceholders(en));
  });

  it("defines episode import actions and task statuses in both locales", () => {
    const zh = JSON.parse(readFileSync("public/locales/zh/translation.json", "utf8"));
    const en = JSON.parse(readFileSync("public/locales/en/translation.json", "utf8"));

    for (const language of [zh, en]) {
      expect(language.ingest.episodeImport).toMatchObject({
        append: expect.any(String),
        batch: expect.any(String),
        accepted: expect.any(String),
        completed: expect.any(String),
      });
    }
  });

  it("uses the requested custom prompt label for Seedance2 guidance in Chinese", () => {
    const content = readFileSync("public/locales/zh/translation.json", "utf8");
    const translations = JSON.parse(content);

    expect(translations.episode.workbench.video.seedance2PromptGuidance).toBe("自定义提示词");
  });

  it("labels Seedance2 text-only reference fallbacks as missing reference images in Chinese", () => {
    const content = readFileSync("public/locales/zh/translation.json", "utf8");
    const translations = JSON.parse(content);

    expect(translations.episode.workbench.video.seedance2ReferenceFallback).toBe("缺参考图");
  });

  it("uses the requested default project queue full toast in Chinese", () => {
    const content = readFileSync("public/locales/zh/translation.json", "utf8");
    const translations = JSON.parse(content);

    expect(translations.common.projectDefaultQueueFull).toBe(
      "当前项目默认队列已满",
    );
  });

  it("defines project queue kind labels in Chinese", () => {
    const content = readFileSync("public/locales/zh/translation.json", "utf8");
    const translations = JSON.parse(content);

    expect(translations.common.projectQueueKinds).toMatchObject({
      video: "视频",
      world: "世界",
      ffmpeg: "合成",
    });
  });

  it("defines 3D director labels used without default values", () => {
    const zh = JSON.parse(readFileSync("public/locales/zh/translation.json", "utf8"));
    const en = JSON.parse(readFileSync("public/locales/en/translation.json", "utf8"));
    const keys = [
      "currentBackgroundSource",
      "downstreamCurrentBackground",
      "shapeHint",
      "saveScene",
      "clearScene",
    ];

    for (const language of [zh, en]) {
      for (const key of keys) {
        expect(language.viewer.threeD[key]).toEqual(expect.any(String));
        expect(language.viewer.threeD[key].length).toBeGreaterThan(0);
      }
    }
  });
});
