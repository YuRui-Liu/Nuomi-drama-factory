import { readFileSync } from "node:fs";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BrandMark } from "@/components/brand/brand-mark";

describe("BrandMark", () => {
  it("renders an inline geometric mark and the NuomiDrama wordmark", () => {
    const { container } = render(<BrandMark />);

    expect(screen.getByRole("img", { name: "NuomiDrama" })).toBeInTheDocument();
    expect(container.querySelector("svg")).toHaveAttribute("viewBox", "0 0 24 24");
    expect(container.querySelector("svg")).toHaveClass("size-6");
    const editCuts = screen.getAllByTestId("nuomidrama-cut");
    expect(editCuts).toHaveLength(2);
    for (const editCut of editCuts) {
      expect(editCut).toHaveAttribute("data-kind", "edit-cut");
      expect(editCut).toHaveAttribute("fill", "var(--brand-accent)");
    }
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(screen.getByText("Nuomi")).toHaveClass("font-semibold");
    expect(screen.getByText("Drama")).toHaveClass("font-normal");
  });

  it("renders only the accessible symbol in compact mode", () => {
    const { container } = render(<BrandMark compact />);

    expect(screen.getByRole("img", { name: "NuomiDrama" })).toBeInTheDocument();
    expect(container).not.toHaveTextContent("NuomiDrama");
  });
});

describe("NuomiDrama page metadata", () => {
  const html = readFileSync("index.html", "utf8");
  const page = new DOMParser().parseFromString(html, "text/html");

  it("uses the NuomiDrama brand in search and social metadata", () => {
    expect(page.title).toContain("NuomiDrama");
    expect(page.querySelector('meta[name="description"]')?.getAttribute("content")).toContain("NuomiDrama");
    expect(page.querySelector('meta[property="og:site_name"]')?.getAttribute("content")).toContain("NuomiDrama");
    expect(page.querySelector('meta[property="og:title"]')?.getAttribute("content")).toContain("NuomiDrama");
    expect(page.querySelector('meta[property="og:description"]')?.getAttribute("content")).toContain("NuomiDrama");
    expect(page.querySelector('meta[name="twitter:title"]')?.getAttribute("content")).toContain("NuomiDrama");
    expect(page.querySelector('meta[name="twitter:description"]')?.getAttribute("content")).toContain("NuomiDrama");
    expect(html).not.toMatch(/DramaClaw|SuperTale/);
  });

  it("preserves the internal storage compatibility key", () => {
    expect(html).toContain('localStorage.getItem("supertale-app")');
  });
});
