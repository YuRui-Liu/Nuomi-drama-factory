import { encodeBriefSettings } from "./settings";
import type { ScriptDocumentKind, ScriptSettings } from "./types";

export interface StarterDocument {
  kind: ScriptDocumentKind;
  title: string;
  markdown: string;
  episode_number?: number;
}

const outline = [
  "# 故事大纲",
  "## Logline、核心看点与情绪曲线",
  "### Logline", "### 核心看点", "### 情绪曲线",
  "## 故事简述",
  "## 背景设定与世界规则",
  "## 全剧分段",
  "## 为什么能共情",
  "## 种子情节与人物变化",
  "## 爽感桥段",
  "## 反转桥段",
  "## 创作禁区",
].join("\n\n");

const people = [
  "# 人物小传",
  "## 新人物（请命名）",
  "### 类型", "### 人物设定", "### 在本剧的作用", "### 性格",
  "### 语言风格", "### 说话的破绽", "### 设定记忆点",
  "### 人物弧光", "起点：\n\n触发：\n\n考验：\n\n关键选择：\n\n终点：\n\n计划集数：\n\n已写集数：",
  "### 被逼急时怎么做", "### 称呼规则", "### 首次出场", "### 关键场次",
].join("\n\n");

const scenes = [
  "# 场景设计", "## 新场景（请命名）",
  "### 剧情作用", "### 空间布局", "### 视觉设计", "### 光线与氛围",
  "### 关键物件", "### 连续性约束", "### 首次出场", "### 关键场次",
].join("\n\n");

const props = [
  "# 道具设计", "## 新道具（请命名）",
  "### 剧情作用", "### 外观与材质", "### 持有与流转", "### 使用动作",
  "### 状态变化", "### 连续性约束", "### 首次出场", "### 关键场次",
].join("\n\n");

export function starterDocuments(settings: ScriptSettings): StarterDocument[] {
  const result: StarterDocument[] = [
    { kind: "brief", title: "创作简报", markdown: encodeBriefSettings(settings) },
    { kind: "outline", title: settings.mode === "single" ? "故事框架" : "故事大纲", markdown: outline },
    { kind: "people", title: "人物小传", markdown: people },
    { kind: "scenes", title: "场景设计", markdown: scenes },
    { kind: "props", title: "道具设计", markdown: props },
  ];
  if (settings.mode === "series") {
    result.push({ kind: "episode_synopsis", title: "分集梗概", markdown: "# 分集梗概\n\n## 第 1 集\n\n### 本集目标\n\n### 冲突推进\n\n### 结尾钩子" });
  }
  result.push({
    kind: "episode_script",
    title: settings.mode === "single" ? "剧本正文" : "第 1 集",
    episode_number: 1,
    markdown: settings.mode === "single"
      ? "# 剧本正文\n\n## 第一场｜地点 · 时间 · 内 / 外\n\n出场人物：\n\n动作：\n\n对白："
      : "# 第 1 集\n\n## 1-1｜地点 · 时间 · 内 / 外\n\n出场人物：\n\n动作：\n\n对白：",
  });
  return result;
}
