// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { splitLiteralSourceText } from "@/lib/literal-source-text";

describe("splitLiteralSourceText", () => {
  it("normalizes newlines, trims rows, and drops empty rows", () => {
    expect(splitLiteralSourceText(" 第一行\r\n\r\n第二行 \r 第三行\n   ")).toEqual([
      "第一行",
      "第二行",
      "第三行",
    ]);
  });

  it("drops imported document metadata and comments outside the screenplay", () => {
    const source = `---
episode: E001
title: 不要叫名字
duration_seconds: 110
rights_risk: low
pending_items: none
---
# E001 不要叫名字

第 · 不要叫名字
时长：110s

1-1 广播站灾变夜 日/夜 内/外
人物：周禾 梁真
△梁真守在直播台前。
周禾：声音只能争取时间。

<!-- main_change: 周禾发现感染者停住。; rights_risk: low -->`;

    expect(splitLiteralSourceText(source)).toEqual([
      "1-1 广播站灾变夜 日/夜 内/外",
      "人物：周禾 梁真",
      "△梁真守在直播台前。",
      "周禾：声音只能争取时间。",
    ]);
  });
});
