// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export function splitLiteralSourceText(sourceText: string): string[] {
  const rawLines = sourceText
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/<!--[\s\S]*?-->/g, "")
    .split("\n");

  const firstNonempty = rawLines.findIndex((line) => line.trim());
  if (firstNonempty >= 0 && rawLines[firstNonempty].replace(/^\uFEFF/, "").trim() === "---") {
    const closing = rawLines.findIndex(
      (line, index) => index > firstNonempty && line.trim() === "---",
    );
    if (closing > firstNonempty) rawLines.splice(firstNonempty, closing - firstNonempty + 1);
  }

  const lines = rawLines.map((line) => line.trim()).filter(Boolean);

  const sceneStart = lines.findIndex((line) =>
    /^(?:\d+\s*[-－]\s*\d+|(?:场次|第)?[（(]?\d+[）)]?\s*场(?:\s|[：:]|$)|(?:地点|环境|场景)[：:])/.test(line),
  );
  if (sceneStart >= 0) return lines.slice(sceneStart);

  const metadataLine = /^(?:#{1,6}\s*(?:E\d+|第\s*\d+\s*集)\b|(?:episode|title|duration_seconds|rights_risk|pending_items)\s*:|时长\s*[：:])/i;
  return lines.filter((line) => line !== "---" && !metadataLine.test(line));
}
