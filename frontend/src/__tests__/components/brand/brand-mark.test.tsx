import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BrandMark } from "@/components/brand/brand-mark";

describe("BrandMark", () => {
  it("renders an inline geometric mark and the NuomiDrama wordmark", () => {
    const { container } = render(<BrandMark />);

    expect(screen.getByRole("img", { name: "NuomiDrama" })).toBeInTheDocument();
    expect(container.querySelector("svg")).toHaveAttribute("viewBox", "0 0 24 24");
    expect(container.querySelector("svg")).toHaveClass("size-6");
    expect(screen.getAllByTestId("nuomidrama-cut")).toHaveLength(2);
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
