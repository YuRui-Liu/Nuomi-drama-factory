// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = process.cwd();
const read = (path: string) => readFileSync(resolve(root, path), "utf8");

describe("episode graph background indexing contract", () => {
  it("declares the episode_graph_index project task type", () => {
    const taskTypes = read("src/lib/task-types.ts");

    expect(taskTypes).toContain('EPISODE_GRAPH_INDEX: "episode_graph_index"');
  });

  it("labels graph indexing and tells users source import is already durable", () => {
    const zh = JSON.parse(read("public/locales/zh/translation.json"));
    const en = JSON.parse(read("public/locales/en/translation.json"));

    expect(zh.tasks.types.episode_graph_index).toBe("知识图谱后台索引");
    expect(en.tasks.types.episode_graph_index).toBe("Knowledge graph indexing");
    expect(zh.ingest.episodeImport.completed).toBe(
      "剧本已导入，知识图谱后台索引中",
    );
    expect(en.ingest.episodeImport.completed).toBe(
      "Scripts imported; knowledge graph indexing continues in the background",
    );
  });
});
