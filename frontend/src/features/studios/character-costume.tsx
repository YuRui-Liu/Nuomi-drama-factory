import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { CharacterStateVersions } from "@/components/assets/character-state-versions";
import { useCharacterIdentities, useCreateIdentity, useGenerateIdentityImageAsync, useUploadCostumeImage } from "@/lib/queries/characters";
import { useTasks } from "@/lib/queries/tasks";
import { queryKeys } from "@/lib/query-keys";
import { CharacterCostumeBindings } from "./character-costume-bindings";
import { useCharacterDraft, useCharacterSwitch } from "./character-draft-bridge";
import { resolveMediaUrl } from '@/lib/media-url';

export function CharacterCostume({ project, name }: { project: string; name: string }) {
  const queryClient = useQueryClient();
  const identities = useCharacterIdentities(project, name);
  const create = useCreateIdentity(project, name);
  const generate = useGenerateIdentityImageAsync(project, name);
  const upload = useUploadCostumeImage(project, name);
  const tasks = useTasks({ project });
  const [identityId, setIdentityId] = useState("");
  const [preview, setPreview] = useState({ identityId: "", url: "" });
  const previewImage = preview.identityId === identityId ? preview.url : "";
  const setPreviewImage = useCallback((url: string) => setPreview({ identityId, url }), [identityId]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dirty, setDirty] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [message, setMessage] = useState("");
  const switching = useCharacterSwitch();
  const signature = JSON.stringify({ title, description });
  const liveSignature = useRef(signature); liveSignature.current = signature;
  const selected = identities.data?.data.find((item) => item.identity_id === identityId);
  const identityTasks = tasks.data?.data.filter((task) => task.task_type === "identity_image" && task.scope === `character:${name}:identity:${selected?.identity_name}`) ?? [];
  const finishedTasks = identityTasks.filter((task) => task.status === "completed" || task.status === "failed").map((task) => `${task.scope}/${task.status}`).join("|");
  useEffect(() => { if (!finishedTasks || !identityId) return; void queryClient.invalidateQueries({ queryKey: queryKeys.productionAssetSlot(project, `character:${name}:state:${identityId}`) }); void queryClient.invalidateQueries({ queryKey: queryKeys.identities(project, name) }); }, [finishedTasks, identityId, name, project, queryClient]);
  async function saveVersion() {
    if (create.isPending) { window.dispatchEvent(new Event("studio-save-failed-character")); return; }
    const submittedSignature = signature;
    try {
      const response = await create.mutateAsync({ identity_name: title, appearance_details: description });
      if (!response.ok) throw new Error("服装版本保存失败");
      setIdentityId(response.data.identity_id); setDirty(liveSignature.current !== submittedSignature); setSubmitted(false);
      if (liveSignature.current !== submittedSignature) window.dispatchEvent(new Event("studio-save-failed-character"));
      setMessage("已保存独立服装身份版本；可上传服装参考并主动生成候选。");
    } catch (error) { setMessage(`保存失败：${error instanceof Error ? error.message : String(error)}`); window.dispatchEvent(new Event("studio-save-failed-character")); }
  }
  useCharacterDraft(`costume/${project}/${name}`, dirty, () => { if (title.trim() && description.trim()) void saveVersion(); else { setMessage("请填写服装名称与造型要求后保存。"); window.dispatchEvent(new Event("studio-save-failed-character")); } });
  return <section className="studio-casting">
    <aside className="studio-casting-config space-y-3">
    <h3 className="font-medium">服装造型版本</h3>
    <p className="text-sm text-muted-foreground">基础人物身份保持不变。每次保存新建独立服装身份；生成时沿用基础面部，已有服装与素材保留。</p>
    <label className="block text-sm">已保存造型<select className="ml-2 rounded border bg-background p-2" value={identityId} onChange={(event) => { const next = event.target.value; const item = identities.data?.data.find((value) => value.identity_id === next); switching.request(() => { setIdentityId(next); setTitle(item?.identity_name ?? ""); setDescription(item?.appearance_details ?? ""); setDirty(false); setSubmitted(false); }); }}><option value="">新建服装版本</option>{identities.data?.data.map((item) => <option key={item.identity_id} value={item.identity_id}>{item.identity_name}</option>)}</select></label>
    {switching.dialog}
    <label className="block text-sm">新版本名称<input className="ml-2 rounded border bg-background p-2" value={title} onChange={(event) => { setTitle(event.target.value); setDirty(true); }} /></label>
    <label className="block text-sm">服装、配饰与造型要求<textarea className="mt-2 min-h-24 w-full rounded border bg-background p-2" value={description} onChange={(event) => { setDescription(event.target.value); setDirty(true); }} /></label>
    <Button disabled={!title.trim() || !description.trim() || create.isPending} onClick={() => void saveVersion()}>保存为新服装版本</Button>
    <label className="block text-sm">服装参考图<input type="file" accept="image/png,image/jpeg,image/webp" disabled={!identityId || dirty || upload.isPending} onChange={async (event) => { const file = event.target.files?.[0]; event.target.value = ""; if (!file) return; try { await upload.mutateAsync({ identityId, file }); setMessage("服装参考已上传。"); } catch (error) { setMessage(`上传失败：${error instanceof Error ? error.message : String(error)}`); } }} /></label>
    {selected?.costume_image_url && <img src={selected.costume_image_url} alt={`${selected.identity_name}服装参考`} className="max-h-24 rounded border object-contain" />}
    <Button disabled={!identityId || dirty || submitted || generate.isPending} onClick={async () => { setSubmitted(true); try { const result = await generate.mutateAsync(identityId); if (!result.ok) throw new Error(result.error); setMessage("造型生成任务已提交，请查看任务进度；结果进入下方版本列表。"); } catch (error) { setMessage(`生成结果待核对：${error instanceof Error ? error.message : String(error)}。请先检查任务中心，避免重复付费。`); } }}>生成服装造型候选</Button>
    <p className="text-xs text-muted-foreground">生成使用项目图像模型配置，价格以实际任务费用为准。任务失败保留服装要求与已有版本。</p>
    {identityTasks.map((task) => <p className="text-xs" key={task.scope}>{task.status} · {task.current_task ?? ""}{task.error ? ` · ${task.error}` : ""}</p>)}
    {identityId && <CharacterCostumeBindings key={`bindings/${identityId}`} project={project} name={name} identityId={identityId} />}
    {message && <p role="status">{message}</p>}{identities.error && <p role="alert">服装列表读取失败：{identities.error.message}</p>}
    </aside>
    <div className="studio-casting-stage"><header className="border-b px-4 py-3 text-sm font-medium">{selected?.identity_name || '服装造型预览'}</header><div className="studio-casting-picture">{previewImage || selected?.image_url || selected?.costume_image_url ? <img alt="服装造型大图" src={resolveMediaUrl(previewImage || selected?.image_url || selected?.costume_image_url) ?? undefined} /> : <p className="text-sm text-muted-foreground">{identityId ? "此造型尚无可预览图片；生成完成后可在下方查看候选。" : "选择已保存造型，或在左侧创建新服装版本"}</p>}</div><div className="costume-versions">{identityId ? <CharacterStateVersions key={identityId} project={project} characterName={name} identityId={identityId} legacyAssetPath={selected?.image_path} onPreview={setPreviewImage} /> : <p className="p-3 text-xs text-muted-foreground">保存造型后，在这里查看候选、检查和采用版本。</p>}</div></div>
  </section>;
}
