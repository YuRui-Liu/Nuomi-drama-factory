import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useCharacterDraft, useCharacterSwitch } from "./character-draft-bridge";

function Editor({ source, dirty, save }: { source: string; dirty: boolean; save: () => void }) {
  useCharacterDraft(source, dirty, save);
  return null;
}
describe("character draft bridge", () => {
  it("keeps another source dirty and saves only dirty sources", () => {
    const events: boolean[] = []; const listener = (event: Event) => events.push((event as CustomEvent).detail.dirty);
    window.addEventListener("studio-dirty", listener);
    const save = vi.fn(); const cleanSave = vi.fn();
    const view = render(<><Editor source="one" dirty save={save}/><Editor source="two" dirty={false} save={cleanSave}/></>);
    expect(events[events.length - 1]).toBe(true);
    act(() => { window.dispatchEvent(new Event("studio-save-character")); });
    expect(save).toHaveBeenCalledOnce(); expect(cleanSave).not.toHaveBeenCalled();
    view.unmount(); expect(events[events.length - 1]).toBe(false);
    window.removeEventListener("studio-dirty", listener);
  });
  it("requires save or discard before switching and stays on save failure", () => {
    const changed = vi.fn();
    function Subject() { const guard = useCharacterSwitch(); return <><button onClick={() => guard.request(changed)}>switch</button>{guard.dialog}</>; }
    const view = render(<><Editor source="draft" dirty save={() => window.dispatchEvent(new Event("studio-save-failed-character"))}/><Subject/></>);
    fireEvent.click(screen.getByText("switch")); expect(changed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("保存后切换")); expect(changed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("放弃修改")); expect(changed).toHaveBeenCalledOnce(); view.unmount();
  });
});
