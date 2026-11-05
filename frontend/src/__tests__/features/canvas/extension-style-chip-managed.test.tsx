// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getExtensionStyle } from "@/features/canvas/extension-styles/catalog";
import { ExtensionStyleChip } from "@/features/canvas/extension-styles/ExtensionStyleChip";

const styleState = vi.hoisted(() => ({
  current: {
    scope: null as { project: string; canvasId: string } | null,
    managed: false as boolean | null,
    fixed: null as { id?: string; title?: string; version?: string; state: string } | null,
  },
}));

vi.mock("@/features/direction-references/useFixedNodeStyle", () => ({
  fixedStyleVersionLabel: (version?: string) =>
    version ? `v${version.length > 10 ? `${version.slice(0, 6)}…` : version}` : "",
  useFixedNodeStyle: () => styleState.current,
}));

vi.mock("@/features/direction-references/DirectionReferenceEntry", async () => {
  const { createElement } = await import("react");
  return {
    useManagedDirectionProject: () => true,
    DirectionReferenceEntry: ({ label }: { label: string }) =>
      createElement("button", { type: "button" }, label),
  };
});

describe("managed extension style chip", () => {
  beforeEach(() => {
    styleState.current = {
      scope: { project: "project-1", canvasId: "canvas-1" },
      managed: true,
      fixed: { id: "fixed-direction-1", title: "固定镜头方向", version: "abc123456789", state: "enabled" },
    };
  });

  it("shows a saved extension style separately from the fixed style and marks it applied in the drawer", async () => {
    const user = userEvent.setup();
    const style = getExtensionStyle("drama_ext.guoman_3d_animation")!;
    const onChange = vi.fn();

    render(
      <ExtensionStyleChip
        ownerNodeId="node-1"
        value={style.id}
        onChange={onChange}
      />,
    );

    expect(screen.getByText(`附加提示词风格：${style.name}`)).toBeVisible();
    expect(screen.getByRole("button", { name: /固定风格：固定镜头方向 · vabc123…/ })).toBeVisible();

    await user.click(screen.getByRole("button", { name: /打开提示词库，当前扩展风格/ }));
    expect(
      await screen.findByRole("button", {
        name: `查看风格：${style.name}，已应用，已选中`,
      }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("keeps the saved extension style read-only until fixed style is configured separately", async () => {
    const user = userEvent.setup();
    const style = getExtensionStyle("drama_ext.guoman_3d_animation")!;
    const onChange = vi.fn();
    styleState.current.fixed = null;

    render(
      <ExtensionStyleChip
        ownerNodeId="node-1"
        value={style.id}
        onChange={onChange}
      />,
    );

    expect(screen.getByText(`当前风格：${style.name}`)).toBeVisible();
    const fixedStyleButton = screen.getByRole("button", { name: "风格配置（含自定义）" });
    expect(fixedStyleButton).toBeVisible();

    await user.click(screen.getByRole("button", { name: /打开提示词库，当前扩展风格/ }));
    const dialog = screen.getByRole("dialog", { name: "漫剧提示词库" });
    expect(
      within(dialog).getByRole("button", {
        name: `查看风格：${style.name}，已应用，已选中`,
      }),
    ).toBeVisible();
    expect(within(dialog).getAllByRole("note")[0]).toHaveTextContent(/管理画布中只读/);
    expect(within(dialog).queryByRole("button", { name: "配置固定风格" })).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
