import { decodeBriefSettings } from "./settings";

type MarkdownNode = {
  type: string;
  depth?: number;
  value?: string;
  children?: MarkdownNode[];
  position?: { start: { offset?: number }; end: { offset?: number } };
};

/** Hydrate legacy empty brief templates in the preview only. Keep source positions
 * on headings intact for sidebar navigation, and never replace authored prose. */
export function remarkBriefSettings(options: { enabled: boolean; title: string }) {
  return (tree: MarkdownNode, file: { value: unknown }) => {
    if (!options.enabled || !tree.children) return;
    const settings = decodeBriefSettings(String(file.value));
    if (!settings) return;
    const paragraph = (value: string): MarkdownNode => ({ type: "paragraph", children: [{ type: "text", value }] });
    const rows: [string, string][] = [
      ["题材", [settings.genrePrimary, settings.genreSecondary === "不融合" ? "" : settings.genreSecondary].filter(Boolean).join(" · ")],
      ["篇幅", settings.mode === "series" ? `连续短剧 · ${settings.episodeCount} 集 · 每集 ${settings.durationSeconds} 秒` : `单集短剧 · ${settings.durationSeconds} 秒`],
      ["人物关系", settings.roles.join("、")], ["时代背景", settings.era.join("、")],
      ["看点", settings.hooks.join("、")], ["视觉风格", settings.style.join("、")],
      ["叙事方式", settings.structure.join("、")],
    ];
    const summary: MarkdownNode = { type: "blockquote", children: rows.filter(([, value]) => value).map(([label, value]) => ({
      type: "paragraph", children: [{ type: "strong", children: [{ type: "text", value: label + "：" }] }, { type: "text", value }],
    })) };
    const fallbacks: Record<string, string> = {
      "故事想法": settings.idea.trim() || "尚未填写故事想法。",
      "目标观众": settings.audience.join("、") || "尚未选择目标观众。",
      "创作边界": "尚未填写创作边界，可在编辑 Markdown 中补充。",
    };
    const nodes = tree.children;
    tree.children = [summary, ...nodes.flatMap((node, index): MarkdownNode[] => {
      const title = node.children?.map(child => child.value ?? "").join("");
      if (node.type === "heading" && node.depth === 1 && title === options.title) return [];
      if (node.type !== "heading" || node.depth !== 2 || !title || !fallbacks[title]) return [node];
      let next = index + 1;
      while (next < nodes.length && nodes[next].type !== "heading") next++;
      const hasContent = nodes.slice(index + 1, next).some(child => child.type !== "html");
      return hasContent ? [node] : [node, paragraph(fallbacks[title])];
    })];
  };
}

/** Preview-only compatibility for Chinese labels such as **性格：**克制
 * and generated labels with trailing spaces such as **性格： **克制.
 * Transform text nodes rather than source so heading/evidence offsets stay intact.
 * Code, escaped markers and HTML are deliberately left alone.
 */
export function remarkDocumentLabels() {
  return (tree: MarkdownNode, file: { value: unknown }) => {
    const source = String(file.value);
    const visit = (node: MarkdownNode) => {
      if (!node.children) return;
      node.children = node.children.flatMap((child): MarkdownNode[] => {
        if (child.type !== "text" || !child.value) {
          visit(child);
          return [child];
        }
        const start = child.position?.start.offset;
        const end = child.position?.end.offset;
        if (start === undefined || end === undefined || source.slice(start, end) !== child.value) return [child];
        const pattern = /(^|\n)([\t ]*)\*\*([^*\n]+[：:])([\t ]*)\*\*/g;
        const parts: MarkdownNode[] = [];
        let offset = 0;
        for (const match of child.value.matchAll(pattern)) {
          const prefix = child.value.slice(offset, match.index) + match[1] + match[2];
          if (prefix) parts.push({ type: "text", value: prefix });
          parts.push({ type: "strong", children: [{ type: "text", value: match[3] }] });
          if (match[4]) parts.push({ type: "text", value: match[4] });
          offset = match.index + match[0].length;
        }
        if (!parts.length) return [child];
        if (offset < child.value.length) parts.push({ type: "text", value: child.value.slice(offset) });
        return parts;
      });
    };
    visit(tree);
  };
}
