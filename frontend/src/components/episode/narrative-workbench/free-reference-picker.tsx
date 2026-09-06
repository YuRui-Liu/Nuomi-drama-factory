import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import type { NarrativeReferenceCandidate, NarrativeReferenceRequirement } from "@/lib/queries/narrative-groups";

export interface ReferenceUploadOptions {
  persist: boolean;
  assetKind?: NarrativeReferenceRequirement["kind"];
  targetEntityId?: string;
  baseEntityId?: string;
  variantId?: string;
}

export function FreeReferencePicker({ candidates, onAdd, onUpload, uploading = false }: {
  candidates: NarrativeReferenceCandidate[];
  onAdd: (assetId: string) => void;
  onUpload: (file: File, options: ReferenceUploadOptions) => void;
  uploading?: boolean;
}) {
  const [persist, setPersist] = useState(false);
  const [assetKind, setAssetKind] = useState<NarrativeReferenceRequirement["kind"]>("prop");
  const [targetEntityId, setTargetEntityId] = useState("");
  const [baseEntityId, setBaseEntityId] = useState("");
  const [variantId, setVariantId] = useState("");
  return <section className="space-y-3 rounded-lg border border-white/10 p-3">
    <div><h3 className="font-medium">自由追加参考</h3><p className="text-xs text-muted-foreground">可从项目资产选取，或上传仅用于本次生成的图片。</p></div>
    <div className="flex flex-wrap gap-2">{candidates.filter((item) => item.available).map((candidate) => <Button key={candidate.id} size="sm" variant="outline" aria-label={`追加 ${candidate.label}`} onClick={() => onAdd(candidate.id)}>{candidate.label}</Button>)}</div>
    <Input aria-label="上传本地参考图" type="file" accept="image/*" disabled={uploading} onChange={(event) => {
      const file = event.target.files?.[0];
      if (file) onUpload(file, {
        persist,
        assetKind: persist ? assetKind : undefined,
        targetEntityId: persist ? targetEntityId : undefined,
        baseEntityId: persist && assetKind === "scene_variant" ? baseEntityId : undefined,
        variantId: persist && assetKind === "scene_variant" ? variantId : undefined,
      });
      event.target.value = "";
    }} />
    <label className="flex items-center gap-2 text-xs"><Checkbox aria-label="保存到项目资产" checked={persist} onCheckedChange={(checked) => setPersist(checked === true)} />保存到项目资产</label>
    {persist ? <div className="grid gap-2 sm:grid-cols-2">
      <label className="space-y-1 text-xs">资产类型<select aria-label="资产类型" className="h-9 w-full rounded-md border border-input bg-background px-3" value={assetKind} onChange={(event) => setAssetKind(event.target.value as NarrativeReferenceRequirement["kind"])}><option value="character_identity">角色身份</option><option value="scene_base">场景</option><option value="scene_variant">场景变体</option><option value="prop">道具</option></select></label>
      <label className="space-y-1 text-xs">目标实体 ID<Input aria-label="目标实体 ID" value={targetEntityId} onChange={(event) => setTargetEntityId(event.target.value)} /></label>
      {assetKind === "scene_variant" ? <>
        <label className="space-y-1 text-xs">基础场景 ID<Input aria-label="基础场景 ID" value={baseEntityId} onChange={(event) => setBaseEntityId(event.target.value)} /></label>
        <label className="space-y-1 text-xs">变体 ID<Input aria-label="变体 ID" value={variantId} onChange={(event) => setVariantId(event.target.value)} /></label>
      </> : null}
    </div> : <p className="text-[11px] text-muted-foreground">默认作为临时引用，不写入项目资产。</p>}
  </section>;
}
