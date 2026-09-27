import type { ScriptDocument, ScriptDocumentKind, ScriptMode } from "./types";

export interface TreeNode {
  id: string;
  label: string;
  kind: ScriptDocumentKind;
  documentId: string;
  anchor?: string;
  children: TreeNode[];
}

const labels: Record<ScriptDocumentKind, string> = {
  brief: "创作简报", outline: "故事大纲", people: "人物小传", scenes: "场景设计",
  props: "道具设计", episode_synopsis: "分集梗概", episode_script: "分集剧本",
};
const order: ScriptDocumentKind[] = ["brief", "outline", "people", "scenes", "props", "episode_synopsis", "episode_script"];

export function headingsForMarkdown(markdown: string): { title: string; anchor: string; level: number }[] {
  let offset = 0;
  const result: { title: string; anchor: string; level: number }[] = [];
  for (const line of markdown.split("\n")) {
    const match = line.match(/^(#{2,3})\s+(.+)/);
    if (match) result.push({ title: match[2].trim(), anchor: String(offset), level: match[1].length });
    offset += Array.from(line).length + 1;
  }
  return result;
}

export function treeForDocuments(documents: ScriptDocument[], mode: ScriptMode): TreeNode[] {
  return documents
    .filter((doc) => mode !== "single" || (doc.kind !== "episode_synopsis" && (doc.kind !== "episode_script" || (doc.episode_number ?? 1) === 1)))
    .sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || (a.episode_number ?? 0) - (b.episode_number ?? 0))
    .map((doc) => {
      const children: TreeNode[] = [];
      let currentSection: TreeNode | null = null;
      for (const heading of headingsForMarkdown(doc.revision.markdown)) {
        const node: TreeNode = {
          id: doc.id + ":" + heading.anchor, label: heading.title, kind: doc.kind,
          documentId: doc.id, anchor: heading.anchor, children: [],
        };
        if (heading.level === 2) { children.push(node); currentSection = node; }
        else if (currentSection) currentSection.children.push(node);
        else children.push(node);
      }
      return {
        id: doc.id,
        label: doc.kind === "episode_script" ? mode === "single" ? "剧本正文" : "第 " + (doc.episode_number ?? 1) + " 集" : labels[doc.kind],
        kind: doc.kind, documentId: doc.id, children,
      };
    });
}
