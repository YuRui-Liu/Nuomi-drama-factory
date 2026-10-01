import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, params, search, hash, children }: { to: string; params: Record<string, string>; search: Record<string, string>; hash?: string; children: ReactNode }) => (
    <a href={`${to.replace("$project", params.project).replace("$episode", params.episode)}?${new URLSearchParams(search)}${hash ? `#${hash}` : ""}`}>{children}</a>
  ),
}));

import { AssetBeatReferences } from "@/components/assets/asset-beat-references";

describe("asset reference navigation", () => {
  it("links group references to the selected group and bindings to episode assets", () => {
    render(<AssetBeatReferences project="demo" references={[
      { episode: 2, groupId: "group-03", groupOrdinal: 3 },
      { episode: 4, binding: true },
      { episode: 1, beatNumber: 7 },
    ]} />);
    expect(screen.getByRole("link", { name: "第 2 集 · 叙事组 3" })).toHaveAttribute("href", "/projects/demo/episodes/2/beats?group=group-03&sub=render");
    expect(screen.getByRole("link", { name: "第 4 集 · 资产绑定" })).toHaveAttribute("href", "/projects/demo/episodes/4/script?#script-assets");
    expect(screen.getAllByRole("link")[0]).toHaveAttribute("href", "/projects/demo/episodes/1/beats?beat=7#beat-7");
    expect(screen.queryByText(/beat-undefined/)).not.toBeInTheDocument();
  });
});
