// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState } from "react";

import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EXTENSION_STYLES } from "@/features/canvas/extension-styles/catalog";
import { ExtensionStyleChip } from "@/features/canvas/extension-styles/ExtensionStyleChip";
import { ExtensionStyleDrawer } from "@/features/canvas/extension-styles/ExtensionStyleDrawer";

const CEL_STYLE = EXTENSION_STYLES.find((style) => style.name === "日系赛璐璐")!;
const REALISTIC_STYLE = EXTENSION_STYLES.find((style) => style.name === "电影级写实")!;

function DrawerHarness({ initialValue = null }: { initialValue?: string | null }) {
  const [value, setValue] = useState<string | null>(initialValue);
  return (
    <>
      <output aria-label="当前已应用风格">{value ?? "无"}</output>
      <ExtensionStyleDrawer
        open
        value={value}
        onChange={setValue}
        onOpenChange={() => undefined}
      />
    </>
  );
}

describe("ExtensionStyleDrawer", () => {
  it("provides an accessible controlled sheet and reports close requests", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();

    render(
      <ExtensionStyleDrawer
        open
        value={null}
        onChange={vi.fn()}
        onOpenChange={onOpenChange}
      />,
    );

    expect(screen.getByRole("dialog", { name: "漫剧提示词库" })).toBeVisible();
    expect(screen.getByText("选择一个扩展风格，仅在生成请求中追加风格片段。")).toBeVisible();
    expect(screen.getByRole("searchbox", { name: "搜索扩展风格" })).toBeVisible();
    expect(screen.getByRole("group", { name: "扩展风格分类" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("keeps the drawer and its interactive backdrop above the operations panel", () => {
    const onOpenChange = vi.fn();
    render(
      <ExtensionStyleDrawer
        open
        value={null}
        onChange={vi.fn()}
        onOpenChange={onOpenChange}
      />,
    );

    expect(screen.getByRole("dialog", { name: "漫剧提示词库" })).toHaveClass("z-[70]");
    const backdrop = screen.getByTestId("extension-style-drawer-backdrop");
    expect(backdrop).toHaveClass("fixed", "inset-0", "-z-10");
    fireEvent.pointerDown(backdrop);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("searches names, summaries and use cases and shows an empty result", async () => {
    const user = userEvent.setup();
    render(
      <ExtensionStyleDrawer open value={null} onChange={vi.fn()} onOpenChange={vi.fn()} />,
    );

    const search = screen.getByRole("searchbox", { name: "搜索扩展风格" });
    await user.type(search, CEL_STYLE.summary);
    expect(screen.getByRole("button", { name: `查看风格：${CEL_STYLE.name}` })).toBeVisible();

    await user.clear(search);
    await user.type(search, REALISTIC_STYLE.use_cases[0]);
    expect(screen.getByRole("button", { name: `查看风格：${REALISTIC_STYLE.name}` })).toBeVisible();

    await user.clear(search);
    await user.type(search, "肯定不存在的风格关键词");
    expect(screen.getByText("没有找到匹配的扩展风格")).toBeVisible();
  });

  it("filters all five product categories with pressed-state buttons", async () => {
    const user = userEvent.setup();
    render(
      <ExtensionStyleDrawer open value={null} onChange={vi.fn()} onOpenChange={vi.fn()} />,
    );

    const categoryLabels = ["2D", "3D", "写实", "国风", "实验风格"];
    for (const label of categoryLabels) {
      const button = screen.getByRole("button", { name: `筛选分类：${label}` });
      expect(button).toHaveAttribute("aria-pressed", "false");
    }

    await user.click(screen.getByRole("button", { name: "筛选分类：写实" }));
    expect(screen.getByRole("button", { name: "筛选分类：写实" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: `查看风格：${REALISTIC_STYLE.name}` })).toBeVisible();
    expect(screen.queryByRole("button", { name: `查看风格：${CEL_STYLE.name}` })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "筛选分类：全部" }));
    expect(screen.getByRole("button", { name: `查看风格：${CEL_STYLE.name}` })).toBeVisible();
  });

  it("keeps pending selection separate until apply, then switches the applied value", async () => {
    const user = userEvent.setup();
    render(<DrawerHarness initialValue={CEL_STYLE.id} />);

    expect(screen.getByLabelText("当前已应用风格")).toHaveTextContent(CEL_STYLE.id);
    await user.click(screen.getByRole("button", { name: `查看风格：${REALISTIC_STYLE.name}` }));
    expect(screen.getByRole("heading", { name: REALISTIC_STYLE.name })).toBeVisible();
    expect(screen.getByLabelText("当前已应用风格")).toHaveTextContent(CEL_STYLE.id);

    await user.click(screen.getByRole("button", { name: "应用风格" }));
    expect(screen.getByLabelText("当前已应用风格")).toHaveTextContent(REALISTIC_STYLE.id);
    expect(screen.getByRole("button", { name: "已应用" })).toBeDisabled();
  });

  it("drops filtered pending details so a hidden style cannot be applied", async () => {
    const user = userEvent.setup();
    render(<DrawerHarness initialValue={CEL_STYLE.id} />);

    expect(screen.getByRole("heading", { name: CEL_STYLE.name })).toBeVisible();
    await user.type(
      screen.getByRole("searchbox", { name: "搜索扩展风格" }),
      "肯定不存在的风格关键词",
    );

    expect(screen.getByText("没有找到匹配的扩展风格")).toBeVisible();
    expect(screen.queryByRole("heading", { name: CEL_STYLE.name })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "已应用" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("extension-style-mobile-actions")).not.toBeInTheDocument();
  });

  it("keeps details sticky on desktop and exposes immediate mobile actions", async () => {
    const user = userEvent.setup();
    render(
      <ExtensionStyleDrawer open value={null} onChange={vi.fn()} onOpenChange={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: `查看风格：${REALISTIC_STYLE.name}` }));
    const desktopDetails = screen.getByTestId("extension-style-desktop-details");
    expect(desktopDetails).toHaveClass("sticky", "top-0");
    expect(within(desktopDetails).getByRole("heading", { name: REALISTIC_STYLE.name })).toBeVisible();

    const mobileActions = screen.getByTestId("extension-style-mobile-actions");
    expect(mobileActions).toHaveClass("sticky", "bottom-0", "md:hidden");
    expect(within(mobileActions).getByText(REALISTIC_STYLE.name)).toBeVisible();
    expect(within(mobileActions).getByRole("button", { name: "立即应用风格" })).toBeVisible();

    await user.click(within(mobileActions).getByRole("button", { name: "查看详情" }));
    const mobileDetails = screen.getByTestId("extension-style-mobile-details");
    expect(within(mobileDetails).getByRole("heading", { name: REALISTIC_STYLE.name })).toBeVisible();
    expect(within(mobileDetails).getByRole("button", { name: "关闭详情" })).toBeVisible();
  });

  it("shows card and detail preview fallbacks and resets detail fallback on switching", async () => {
    const user = userEvent.setup();
    render(
      <ExtensionStyleDrawer open value={null} onChange={vi.fn()} onOpenChange={vi.fn()} />,
    );

    fireEvent.error(screen.getByRole("img", { name: `${CEL_STYLE.name}预览` }));
    expect(screen.getByText(`${CEL_STYLE.name}预览暂不可用`)).toBeVisible();

    await user.click(screen.getByRole("button", { name: `查看风格：${CEL_STYLE.name}` }));
    fireEvent.error(screen.getByRole("img", { name: `${CEL_STYLE.name}详情预览` }));
    expect(screen.getByText("详情预览暂不可用")).toBeVisible();

    await user.click(screen.getByRole("button", { name: `查看风格：${REALISTIC_STYLE.name}` }));
    expect(screen.queryByText("详情预览暂不可用")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: `${REALISTIC_STYLE.name}详情预览` })).toBeVisible();
  });

  it("lazy-loads previews and announces applied versus pending selection", async () => {
    const user = userEvent.setup();
    render(
      <ExtensionStyleDrawer
        open
        value={CEL_STYLE.id}
        onChange={vi.fn()}
        onOpenChange={vi.fn()}
      />,
    );

    const appliedCard = screen.getByRole("button", {
      name: `查看风格：${CEL_STYLE.name}，已应用，已选中`,
    });
    expect(appliedCard).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("img", { name: `${CEL_STYLE.name}预览` })).toHaveAttribute(
      "loading",
      "lazy",
    );

    await user.click(screen.getByRole("button", { name: `查看风格：${REALISTIC_STYLE.name}` }));
    expect(
      screen.getByRole("button", { name: `查看风格：${REALISTIC_STYLE.name}，已选中` }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: `查看风格：${CEL_STYLE.name}，已应用` }),
    ).toHaveAttribute("aria-pressed", "false");
  });
});

describe("ExtensionStyleChip", () => {
  it("opens the library when empty, applies a style, and can switch it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(<ExtensionStyleChip value={null} onChange={onChange} />);

    const libraryTrigger = screen.getByRole("button", { name: "提示词库" });
    expect(libraryTrigger).toHaveAttribute("aria-haspopup", "dialog");
    expect(libraryTrigger).toHaveAttribute("aria-expanded", "false");
    await user.click(libraryTrigger);
    expect(libraryTrigger).toHaveAttribute("aria-expanded", "true");
    await user.type(screen.getByRole("searchbox", { name: "搜索扩展风格" }), "赛璐璐");
    await user.click(screen.getByRole("button", { name: `查看风格：${CEL_STYLE.name}` }));
    await user.click(screen.getByRole("button", { name: "应用风格" }));
    expect(onChange).toHaveBeenLastCalledWith(CEL_STYLE.id);

    await user.click(screen.getByRole("button", { name: "Close" }));
    rerender(<ExtensionStyleChip value={CEL_STYLE.id} onChange={onChange} />);
    const selectedStyleButton = await screen.findByRole("button", {
      name: `打开提示词库，当前风格：${CEL_STYLE.name}`,
    });
    expect(selectedStyleButton).toBeVisible();
    expect(selectedStyleButton).toHaveAttribute("aria-haspopup", "dialog");
    expect(selectedStyleButton).toHaveAttribute("aria-expanded", "false");
    await user.click(selectedStyleButton);
    const dialog = screen.getByRole("dialog", { name: "漫剧提示词库" });
    expect(within(dialog).getByRole("heading", { name: CEL_STYLE.name })).toBeVisible();
  });

  it("removes the applied style from an independent button", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ExtensionStyleChip value={CEL_STYLE.id} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: `移除扩展风格：${CEL_STYLE.name}` }));
    expect(onChange).toHaveBeenCalledWith(null);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("clears the open state callback when the node control unmounts", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    const { unmount } = render(
      <ExtensionStyleChip value={null} onChange={vi.fn()} onOpenChange={onOpenChange} />,
    );

    await user.click(screen.getByRole("button", { name: "提示词库" }));
    expect(onOpenChange).toHaveBeenLastCalledWith(true);
    unmount();
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
  });

  it("isolates trigger and remove clicks from the image node", async () => {
    const user = userEvent.setup();
    const parentClick = vi.fn();
    const onChange = vi.fn();
    const { rerender } = render(
      <div onClick={parentClick}>
        <ExtensionStyleChip value={null} onChange={onChange} />
      </div>,
    );

    await user.click(screen.getByRole("button", { name: "提示词库" }));
    expect(parentClick).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Close" }));

    rerender(
      <div onClick={parentClick}>
        <ExtensionStyleChip value={CEL_STYLE.id} onChange={onChange} />
      </div>,
    );
    await user.click(
      await screen.findByRole("button", { name: `移除扩展风格：${CEL_STYLE.name}` }),
    );
    expect(parentClick).not.toHaveBeenCalled();
  });
});

describe("ImageGenNode extension style UI wiring", () => {
  it("places the chip after StyleChip and only suppresses history while the drawer is open", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/features/canvas/nodes/ImageGenNode.tsx"),
      "utf8",
    );

    const styleChipPosition = source.indexOf("<StyleChip");
    const extensionChipPosition = source.indexOf("<ExtensionStyleChip");
    expect(styleChipPosition).toBeGreaterThan(-1);
    expect(extensionChipPosition).toBeGreaterThan(styleChipPosition);
    expect(source).toContain(
      "onChange={(nextId) => updateNodeData(id, { extensionStyleId: nextId })}",
    );
    expect(source).toContain("const [extensionStyleDrawerOpen, setExtensionStyleDrawerOpen]");

    const showOpsStart = source.indexOf("const showImageOpsPanel =");
    const showOpsEnd = source.indexOf("return (", showOpsStart);
    expect(source.slice(showOpsStart, showOpsEnd)).not.toContain("extensionStyleDrawerOpen");
    expect(source).toContain(
      "!stylePickerOpen && !extensionStyleDrawerOpen && hasCompletedHistoryRecords(historyRecords)",
    );
  });
});
