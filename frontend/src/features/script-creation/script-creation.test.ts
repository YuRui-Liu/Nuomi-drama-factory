import { describe, expect, it, vi } from "vitest";

import { addChoice, removeChoice, defaultSettings, decodeBriefSettings, encodeBriefSettings } from "./settings";
import { DraftManager } from "./draft-manager";
import { treeForDocuments } from "./document-tree";
import { episodeScriptTemplate, starterDocuments } from "./templates";
import type { ScriptDocument } from "./types";

function document(id: string, kind: ScriptDocument["kind"], markdown: string, episode_number: number | null = null): ScriptDocument {
  return {
    id, kind, title: id, episode_number, current_revision_id: `${id}-r1`, adopted_revision_id: null,
    source_origin: null, created_at: "", updated_at: "",
    revision: { id: `${id}-r1`, document_id: id, parent_revision_id: null, markdown,
      blocks: [{ id: `${id}-b1`, markdown }], client_mutation_id: "create", created_at: "", restored_from_revision_id: null },
  };
}

describe("script settings", () => {
  it("keeps a custom era even when no suggestion matches, then deselects it", () => {
    const selected = addChoice([], "自定义极寒世纪");
    expect(selected).toEqual(["自定义极寒世纪"]);
    expect(removeChoice(selected, "自定义极寒世纪")).toEqual([]);
  });

  it("persists and reopens edited settings in the brief document", () => {
    const settings = { ...defaultSettings(), genrePrimary: "悬疑推理", era: ["自定义极寒世纪"], mode: "single" as const, durationSeconds: 126 };
    const markdown = encodeBriefSettings(settings);
    expect(decodeBriefSettings(markdown)).toEqual(settings);
  });

  it("hides episode synopsis and numbered episode branches in single mode", () => {
    const docs = [document("brief", "brief", ""), document("synopsis", "episode_synopsis", ""), document("script", "episode_script", "# 第一场", 1), document("script2", "episode_script", "# 第二场", 2)];
    const tree = treeForDocuments(docs, "single");
    expect(tree.some((entry) => entry.kind === "episode_synopsis")).toBe(false);
    expect(tree.find((entry) => entry.kind === "episode_script")?.label).toBe("剧本正文");
    expect(tree.filter((entry) => entry.kind === "episode_script")).toHaveLength(1);
  });
});

describe("document drafts", () => {
  it("retains failed edits for retry", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("offline")).mockImplementationOnce(async (_id, _revision, markdown) => document("one", "outline", markdown));
    const manager = new DraftManager(save);
    manager.load(document("one", "outline", "original"));
    manager.edit("one", "changed");
    await manager.flush("one");
    expect(manager.get("one")?.markdown).toBe("changed");
    expect(manager.get("one")?.status).toBe("error");
    await manager.retry("one");
    expect(manager.get("one")?.status).toBe("saved");
    manager.dispose();
  });

  it("does not replace newer typing with an older save response", async () => {
    let release!: (value: ScriptDocument) => void;
    const save = vi.fn().mockImplementation(() => new Promise<ScriptDocument>((resolve) => { release = resolve; }));
    const manager = new DraftManager(save);
    manager.load(document("one", "outline", "A"));
    manager.edit("one", "AB");
    const first = manager.flush("one");
    manager.edit("one", "ABC");
    release(document("one", "outline", "AB"));
    await first;
    expect(manager.get("one")?.markdown).toBe("ABC");
    expect(manager.get("one")?.status).toBe("dirty");
    manager.dispose();
  });

  it("reuses the save mutation after a lost response", async () => {
    let firstId = "";
    const save = vi.fn().mockImplementationOnce(async (_id: string, _base: string, _text: string, mutationId: string) => {
      expect(mutationId).toBeTruthy();
      firstId = mutationId;
      throw new Error("response lost");
    }).mockImplementationOnce(async (_id: string, _base: string, text: string, mutationId: string) => {
      expect(mutationId).toBe(firstId);
      return document("one", "outline", text);
    });
    const manager = new DraftManager(save);
    manager.load(document("one", "outline", "original"));
    manager.edit("one", "changed");
    await manager.flush("one");
    await manager.retry("one");
    expect(manager.get("one")?.status).toBe("saved");
    manager.dispose();
  });

  it("keeps a buffer when switching documents", () => {
    const manager = new DraftManager(vi.fn().mockResolvedValue(document("one", "outline", "AB")));
    manager.load(document("one", "outline", "A"));
    manager.edit("one", "AB");
    manager.load(document("two", "people", "B"));
    expect(manager.get("one")?.markdown).toBe("AB");
    manager.dispose();
  });
});

  it("nests character arc anchors beneath a named character", () => {
    const tree = treeForDocuments([document("people", "people", "# 人物小传\n\n## 林川\n\n### 人物弧光")], "series");
    expect(tree[0].children[0].label).toBe("林川");
    expect(tree[0].children[0].children[0].label).toBe("人物弧光");
  });

  it("starts with editable blank structure and no canned story", async () => {
    const { starterDocuments } = await import("./templates");
    const markdown = starterDocuments(defaultSettings()).map((entry) => entry.markdown).join("\n");
    expect(markdown).toContain("## Logline");
    expect(markdown).toContain("### 人物弧光");
    expect(markdown).not.toMatch(/林川|沈青|讨薪|宗门/);
  });


describe("blank creative templates", () => {
  it("uses one episode skeleton for series first/later and single scripts", () => {
    for (const [mode, number] of [["series", 1], ["series", 2], ["single", 1]] as const) {
      const template = episodeScriptTemplate(mode, number);
      expect(template.markdown).toContain("本集目标：");
      expect(template.markdown).toContain("场景名称 · 日/夜 · 内/外");
      expect(template.markdown).toContain("出场人物：");
      expect(template.markdown).toContain("动作描述：");
      expect(template.markdown).toContain("人物对白：");
      expect(template.markdown).toContain("必要语气提示");
      expect(template.markdown).toContain("结尾钩子：");
      expect(template.markdown).not.toMatch(/林川|沈青|讨薪/);
    }
    const first = starterDocuments(defaultSettings()).find((doc) => doc.kind === "episode_script");
    expect(first?.markdown).toBe(episodeScriptTemplate("series", 1).markdown);
  });

  it("distinguishes planned and written appearances in scene and prop designs", () => {
    const docs = starterDocuments(defaultSettings());
    const scene = docs.find((doc) => doc.kind === "scenes")!.markdown;
    const prop = docs.find((doc) => doc.kind === "props")!.markdown;
    expect(scene).toContain("### 场景类型");
    for (const markdown of [scene, prop]) {
      expect(markdown).toContain("### 首次出场（计划）");
      expect(markdown).toContain("### 首次出场（已写）");
      expect(markdown).toContain("### 关键场次（计划）");
      expect(markdown).toContain("### 关键场次（已写）");
    }
  });
});
