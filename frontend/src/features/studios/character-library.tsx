import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useCreateCharacter } from "@/lib/queries/characters";
import type { Character } from "@/types/character";
import { listStudioDocuments, saveStudioDocument } from "./studio-api";
import { characterCardCopy } from "./character-library-data";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";

export function CharacterLibrary({ project, character }: { project: string; character?: Character }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const cards = useQuery({ queryKey: ["studios", project, "character"], queryFn: () => listStudioDocuments<{ character: Character }>(project, "character") });
  const create = useCreateCharacter(project);
  const personal = useQuery({ queryKey: ["personal-character-library"], queryFn: () => api.get("api/v1/studios/character-library").json<{ data: Array<{ id: string; name: string; revision: number }> }>() });
  const save = useMutation({ mutationFn: () => { if (!character) throw new Error("请先选择角色"); return saveStudioDocument(project, "character", crypto.randomUUID(), { name: character.name, data: { character: { ...character }, source_project: project, saved_at: new Date().toISOString() }, expected_revision: 0 }); }, onSuccess: () => { setMessage("角色卡快照已存入项目库。"); void cards.refetch(); }, onError: (error) => setMessage(error.message) });
  const savePersonal = useMutation({ mutationFn: () => { if (!character) throw new Error("请先选择角色"); return jsonWithBackendError(api.post(p`api/v1/projects/${project}/studios/characters/${character.name}/library`)); }, onSuccess: () => { setMessage("已保存到个人角色库，可在其他项目复制使用。"); void personal.refetch(); }, onError: (error) => setMessage(error.message) });
  const copyPersonal = useMutation({ mutationFn: (card: string) => jsonWithBackendError<{ data: { media_copied: boolean } }>(api.post(p`api/v1/projects/${project}/studios/character-library/${card}/copy`, { json: { name } })), onSuccess: (result) => { setMessage(result.data.media_copied ? "已复制人物设计与已采纳图像到本项目，可独立使用；不含声音，来源项目保持不变。" : "此旧角色卡仅含设计资料，已创建独立副本；图像需要重新生成，不含声音。"); void queryClient.invalidateQueries({ queryKey: queryKeys.characters(project) }); }, onError: (error) => setMessage(error.message) });
  return <section aria-label="角色卡库" className="rounded-2xl border border-border/70 bg-card/40 p-5"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">角色卡库</h3><span className="text-xs text-muted-foreground">项目快照 / 跨项目复用</span></div><div className="mt-3 space-y-3">
    <p className="text-xs text-muted-foreground">个人库保存人物、服装设计及当前已采纳图像，跨项目复制为独立文件，修改副本不会覆盖来源。不包含声音、候选历史或镜头引用。</p>
    <Button variant="outline" disabled={!character || save.isPending} onClick={() => save.mutate()}>保存到项目库</Button>
    <Button variant="outline" disabled={!character || savePersonal.isPending} onClick={() => savePersonal.mutate()}>保存到个人库</Button>
    <label className="block text-sm">副本角色名称<input className="ml-2 rounded border bg-background p-2" value={name} onChange={(event) => setName(event.target.value)} /></label>
    {cards.data?.filter((card) => card.data.character?.name).map((card) => <div key={card.id} className="flex items-center gap-3 text-sm"><span>{card.name} · v{card.revision}</span><Button size="sm" variant="outline" disabled={!name.trim() || create.isPending} onClick={async () => { try { const result = await create.mutateAsync(characterCardCopy(card.data.character, name)); if (!result.ok) throw new Error("未能创建角色，可能存在同名角色"); setMessage("已从角色卡创建独立角色，素材需重新生成。"); } catch (error) { setMessage(`创建失败：${error instanceof Error ? error.message : String(error)}`); } }}>创建资料副本</Button></div>)}
    {cards.error && <p role="alert">角色卡读取失败：{cards.error.message}</p>}{message && <p role="status">{message}</p>}
    <h3 className="font-medium">我的个人角色卡</h3>
    {personal.isPending && <p className="text-sm text-muted-foreground">正在读取个人角色卡…</p>}
    {personal.data?.data.length === 0 && <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">还没有个人角色卡。选中角色并保存已采纳形象，就能在其他项目继续使用。</p>}
    {personal.data?.data.map((card) => <div key={card.id} className="flex items-center gap-3 text-sm"><span>{card.name} · v{card.revision}</span><Button variant="outline" size="sm" disabled={!name.trim() || copyPersonal.isPending} onClick={() => copyPersonal.mutate(card.id)}>复制到当前项目</Button></div>)}
    {personal.error && <p role="alert">个人库读取失败：{personal.error.message}</p>}
  </div></section>;
}
