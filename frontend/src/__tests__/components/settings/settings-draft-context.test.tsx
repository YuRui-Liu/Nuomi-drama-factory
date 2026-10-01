import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { SettingsDraftProvider, useSettingsSnapshot } from "@/components/settings/settings-draft-context";

function Form({ id }: { id: string }) {
  const [value, setValue] = useState("saved");
  const snapshot = useSettingsSnapshot(id, { value });
  return <><input aria-label={id} value={value} onChange={e => setValue(e.target.value)} /><button onClick={() => snapshot.markSaved({ value })}>保存{id}</button></>;
}
it("keeps another section dirty when one section is successfully saved", () => {
  const dirty = vi.fn();
  render(<SettingsDraftProvider onDirtyChange={dirty}><Form id="runtime" /><Form id="media" /></SettingsDraftProvider>);
  fireEvent.change(screen.getByLabelText("runtime"), { target: { value: "new" } });
  fireEvent.change(screen.getByLabelText("media"), { target: { value: "other" } });
  fireEvent.click(screen.getByText("保存runtime"));
  expect(dirty).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByText("保存media"));
  expect(dirty).toHaveBeenLastCalledWith(false);
});
