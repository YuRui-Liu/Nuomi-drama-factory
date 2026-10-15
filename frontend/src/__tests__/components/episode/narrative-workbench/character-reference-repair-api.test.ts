import { beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { openCharacterRepair, selectCharacterRepair, repairErrorMessage, type CharacterReferenceRepair } from "@/lib/queries/character-reference-repair";
vi.mock("@/lib/api", () => ({ api: { post: vi.fn(), get: vi.fn(), patch: vi.fn() } }));
const context = { project: "p/q", episode: 1, revision: "plan", groupId: "g", bindingId: "b", referenceRevision: "old" };
const repair = { repair_id: "r", source_reference_revision: "old" } as CharacterReferenceRepair;
const response = () => new Response(JSON.stringify({ ok: true, data: repair }));
beforeEach(() => { vi.clearAllMocks(); localStorage.clear(); });
it("reuses the request ID after an ambiguous network failure, then recovers by GET", async () => {
  vi.mocked(api.post).mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce(response());
  await expect(openCharacterRepair(context)).rejects.toThrow("角色引用处理失败");
  await openCharacterRepair(context);
  expect(vi.mocked(api.post).mock.calls[0][1]?.json).toEqual(vi.mocked(api.post).mock.calls[1][1]?.json);
  vi.mocked(api.get).mockResolvedValue(response());
  await openCharacterRepair(context);
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(api.get).toHaveBeenCalledWith(expect.stringContaining("p%2Fq/episodes/1/director-plans/plan/character-reference-repairs/r"), expect.anything());
  vi.mocked(api.post).mockResolvedValue(response());
  await openCharacterRepair({ ...context, referenceRevision: "new" });
  expect(api.post).toHaveBeenCalledTimes(3);
  expect(vi.mocked(api.post).mock.calls[2][1]?.json).not.toEqual(vi.mocked(api.post).mock.calls[0][1]?.json);
});
it("keeps the original source revision and translates nested backend errors", async () => {
  vi.mocked(api.patch).mockResolvedValue(new Response(JSON.stringify({ detail: { code: "CHARACTER_EXISTS_SELECT_EXPLICITLY", error: "raw english" } }), { status: 409 }));
  await expect(selectCharacterRepair({ ...context, referenceRevision: "new" }, repair, { mode: "create", character_name: "青" })).rejects.toThrow("请明确选择已有角色身份");
  expect(api.patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ json: expect.objectContaining({ expected_reference_revision: "old" }) }));
  expect(repairErrorMessage("CANDIDATE_NOT_ADOPTED")).toContain("先在选角中确认采用");
});
