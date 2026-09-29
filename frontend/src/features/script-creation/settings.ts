import type { ScriptSettings } from "./types";

const SETTINGS_OPEN = "<!-- nuomi-script-settings\n";
const SETTINGS_CLOSE = "\n-->";

export function defaultSettings(): ScriptSettings {
  return {
    genrePrimary: "都市现实", genreSecondary: "不融合", audience: [], roles: [], era: [],
    hooks: [], style: [], structure: [], mode: "series", episodeCount: 3,
    durationSeconds: 90, idea: "",
  };
}

export function addChoice(values: string[], input: string): string[] {
  const value = input.trim();
  return value && !values.includes(value) ? [...values, value] : values;
}

export function removeChoice(values: string[], value: string): string[] {
  return values.filter((item) => item !== value);
}

export function encodeBriefSettings(settings: ScriptSettings, priorMarkdown = ""): string {
  const marker = `${SETTINGS_OPEN}${JSON.stringify(settings, null, 2)}${SETTINGS_CLOSE}`;
  const prior = priorMarkdown.replace(/<!-- nuomi-script-settings\n[\s\S]*?\n-->/, "").trim();
  return `${marker}\n\n${prior || "# 创作简报\n\n## 故事想法\n\n## 目标观众\n\n## 创作边界"}`;
}

export function decodeBriefSettings(markdown: string): ScriptSettings | null {
  const raw = markdown.match(/<!-- nuomi-script-settings\n([\s\S]*?)\n-->/)?.[1];
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<ScriptSettings>;
    if (!value || typeof value !== "object") return null;
    const base = defaultSettings();
    const result = { ...base, ...value };
    if (!["single", "series"].includes(result.mode)) return null;
    for (const key of ["audience", "roles", "era", "hooks", "style", "structure"] as const) {
      if (!Array.isArray(result[key]) || !result[key].every((item) => typeof item === "string")) return null;
    }
    return result;
  } catch { return null; }
}
