import type { ComponentType } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const deferredModule = vi.hoisted(() => {
  let resolveModule!: (module: { ThreeDDirectorDialog: ComponentType<any> }) => void;
  const promise = new Promise<{ ThreeDDirectorDialog: ComponentType<any> }>((resolve) => {
    resolveModule = resolve;
  });
  return { load: vi.fn(() => promise), resolveModule, receivedProps: vi.fn() };
});

vi.mock("@/features/viewer-kit/three-d/ThreeDDirectorDialog", () => deferredModule.load());

import { LazyThreeDDirectorDialog } from "@/features/viewer-kit/three-d/LazyThreeDDirectorDialog";

describe("LazyThreeDDirectorDialog", () => {
  it("does not load while closed, then shows status and forwards props", async () => {
    const onOpenChange = vi.fn();
    const { rerender } = render(
      <LazyThreeDDirectorDialog open={false} onOpenChange={onOpenChange} manifest={null} title="导演台测试" />,
    );

    expect(deferredModule.load).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    rerender(
      <LazyThreeDDirectorDialog open onOpenChange={onOpenChange} manifest={null} title="导演台测试" />,
    );

    expect(await screen.findByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(deferredModule.load).toHaveBeenCalledTimes(1);

    deferredModule.resolveModule({
      ThreeDDirectorDialog: (props) => {
        deferredModule.receivedProps(props);
        return <div>3D director loaded</div>;
      },
    });

    expect(await screen.findByText("3D director loaded")).toBeInTheDocument();
    await waitFor(() => expect(deferredModule.receivedProps).toHaveBeenCalledWith(
      expect.objectContaining({ open: true, onOpenChange, manifest: null, title: "导演台测试" }),
    ));
  });
});
