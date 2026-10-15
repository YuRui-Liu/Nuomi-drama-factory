import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterReferenceRepairDialog } from "@/components/episode/narrative-workbench/character-reference-repair-dialog";
import { openCharacterRepair, selectCharacterRepair, getCharacterRepair, bindCharacterRepair, type CharacterReferenceRepair } from "@/lib/queries/character-reference-repair";
vi.mock("@/lib/queries/character-reference-repair", async (load) => ({ ...await load<object>(), openCharacterRepair: vi.fn(), selectCharacterRepair: vi.fn(), getCharacterRepair: vi.fn(), bindCharacterRepair: vi.fn() }));
vi.mock("@/components/assets/character-casting-panel", () => ({ CharacterCastingPanel: (props: { onAdopted: () => void; identityId: string }) => <button onClick={props.onAdopted}>手动采用 {props.identityId}</button> }));
const context = { project: "demo", episode: 1, revision: "plan", referenceRevision: "refs", bindingId: "b", groupId: "g" };
const draft: CharacterReferenceRepair = { repair_id: "r", source_reference_revision: "refs", display_label: "阿青", character_name: "", identity_id: null, candidate_id: null, appearance: "", costume: "", evidence: ["阿青穿青衣走进门"], reuse_identity: false, status: "draft", existing_choices: [{ character_name: "青", identity_id: "i", label: "青·青年", has_image: true }] };
beforeEach(() => { vi.clearAllMocks(); vi.mocked(openCharacterRepair).mockResolvedValue(draft); });
const mount = (initialMode: "create" | "existing" = "create") => { const onBound = vi.fn().mockResolvedValue(undefined); return { ...render(<CharacterReferenceRepairDialog context={context} initialMode={initialMode} onClose={vi.fn()} onBound={onBound} />), onBound }; };
describe("character reference repair", () => {
  it("requires editable identity confirmation and manual casting adoption", async () => {
    vi.mocked(selectCharacterRepair).mockResolvedValue({ ...draft, status: "selected", character_name: "阿青", identity_id: "new" });
    mount();
    expect(await screen.findByDisplayValue("阿青")).toBeInTheDocument();
    expect(screen.getByText(draft.evidence[0])).toBeInTheDocument();
    expect(selectCharacterRepair).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /手动采用/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("外貌"), { target: { value: "黑发" } });
    fireEvent.click(screen.getByText("确认角色身份"));
    expect(await screen.findByText("手动采用 new")).toBeInTheDocument();
    expect(selectCharacterRepair).toHaveBeenCalledWith(context, draft, expect.objectContaining({ mode: "create", appearance: "黑发" }));
    expect(bindCharacterRepair).not.toHaveBeenCalled();
  });
  it("binds only the explicit existing identity choice", async () => {
    vi.mocked(selectCharacterRepair).mockResolvedValue({ ...draft, status: "bound" });
    const { onBound } = mount("existing");
    fireEvent.change(await screen.findByLabelText("搜索已有角色"), { target: { value: "不存在" } });
    expect(screen.queryByRole("option", { name: /青·青年/ })).not.toBeInTheDocument();
    expect(selectCharacterRepair).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("搜索已有角色"), { target: { value: "青" } });
    fireEvent.change(await screen.findByLabelText("已有角色身份"), { target: { value: "0" } });
    fireEvent.click(screen.getByText("确认角色身份"));
    await waitFor(() => expect(onBound).toHaveBeenCalledOnce());
    expect(selectCharacterRepair).toHaveBeenCalledWith(context, draft, { mode: "existing", character_name: "青", identity_id: "i" });
  });
  it("recovers missing image identity and retries binding independently", async () => {
    const selected = { ...draft, status: "binding_failed" as const, character_name: "青", identity_id: "i", candidate_id: "c", reuse_identity: true, error: "BIND_FAILED" };
    vi.mocked(openCharacterRepair).mockResolvedValue(selected);
    vi.mocked(bindCharacterRepair).mockResolvedValue({ ...selected, status: "bound" });
    const { onBound } = mount();
    fireEvent.click(await screen.findByText("重试绑定"));
    await waitFor(() => expect(onBound).toHaveBeenCalledOnce());
    expect(bindCharacterRepair).toHaveBeenCalledWith(context, selected);
    expect(selectCharacterRepair).not.toHaveBeenCalled();
  });
  it("refreshes after manual adoption and ignores completion after navigation", async () => {
    const selected = { ...draft, status: "selected" as const, character_name: "青", identity_id: "i", reuse_identity: true };
    vi.mocked(openCharacterRepair).mockResolvedValue(selected);
    let complete!: (value: CharacterReferenceRepair) => void;
    vi.mocked(getCharacterRepair).mockReturnValue(new Promise((resolve) => { complete = resolve; }));
    const { onBound, unmount } = mount();
    fireEvent.click(await screen.findByText("查看选角"));
    fireEvent.click(screen.getByText("手动采用 i"));
    unmount();
    await act(async () => complete({ ...selected, status: "bound" }));
    expect(onBound).not.toHaveBeenCalled();
  });
});
