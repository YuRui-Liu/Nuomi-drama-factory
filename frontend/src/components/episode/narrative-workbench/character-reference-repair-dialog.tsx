import { useEffect, useRef, useState } from "react";
import { CharacterCastingPanel } from "@/components/assets/character-casting-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { bindCharacterRepair, getCharacterRepair, openCharacterRepair, repairErrorMessage, selectCharacterRepair, type CharacterReferenceRepair, type RepairContext } from "@/lib/queries/character-reference-repair";

export function CharacterReferenceRepairDialog({ context, initialMode, onClose, onBound, onReloadSource }: {
  context: RepairContext; initialMode: "create" | "existing"; onClose: () => void; onBound: () => Promise<void>; onReloadSource?: () => Promise<void>;
}) {
  const [repair, setRepair] = useState<CharacterReferenceRepair | null>(null);
  const [mode, setMode] = useState(initialMode);
  const [name, setName] = useState("");
  const [appearance, setAppearance] = useState("");
  const [costume, setCostume] = useState("");
  const [choice, setChoice] = useState("");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [casting, setCasting] = useState(false);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const accept = async (next: CharacterReferenceRepair) => {
    if (!active.current) return;
    setRepair(next);
    if (next.status === "bound") await onBound();
  };
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (caught) { if (active.current) setError(repairErrorMessage(caught)); }
    finally { if (active.current) setBusy(false); }
  };
  const load = () => run(async () => {
    const next = await openCharacterRepair(context);
    if (!active.current) return;
    setName(next.character_name || next.display_label); setAppearance(next.appearance); setCostume(next.costume);
    await accept(next);
  });
  useEffect(() => { void load(); }, []); // parent keys this dialog by the exact source context
  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl" style={{ backgroundColor: "var(--popover)", opacity: 1, animation: "none" }}>
      <DialogHeader><DialogTitle>补齐角色引用</DialogTitle><DialogDescription>先确认角色身份，再生成候选并手动采用；采用后绑定回当前引用。</DialogDescription></DialogHeader>
      {error ? <p role="alert">{error}</p> : null}
      {!repair ? <Button disabled={busy} onClick={() => void load()}>{busy ? "正在读取…" : "重新读取"}</Button> : <>
        <h3>{repair.display_label}</h3>
        {repair.evidence.length ? <ul>{repair.evidence.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p>暂无剧情描述，请补充角色设定。</p>}
        {repair.status === "source_changed" ? <><p role="alert">规划引用已变化，请重新确认。已生成形象可从已有角色中选择，无需重新生成。</p>{onReloadSource ? <Button disabled={busy} onClick={() => void run(onReloadSource)}>按最新引用重新确认</Button> : null}</> : repair.status === "bound" ? <><p>角色形象已绑定，当前引用已就绪。</p><Button onClick={() => void run(onBound)} disabled={busy}>刷新引用</Button></> : <>
          {(repair.status === "draft" || repair.status === "creating") && !repair.reuse_identity ? <>
            <div className="flex gap-2"><Button onClick={() => setMode("create")}>生成角色形象</Button><Button onClick={() => setMode("existing")}>绑定已有角色</Button></div>
            {mode === "create" ? <>
              <label>角色名称<Input value={name} onChange={(e) => setName(e.target.value)} /></label>
              <label>外貌<Input value={appearance} onChange={(e) => setAppearance(e.target.value)} /></label>
              <label>服装<Input value={costume} onChange={(e) => setCostume(e.target.value)} /></label>
            </> : <><label>搜索已有角色<Input value={search} onChange={(e) => { setSearch(e.target.value); setChoice(""); }} /></label><label>已有角色身份<select aria-label="已有角色身份" value={choice} onChange={(e) => setChoice(e.target.value)}><option value="">请选择角色身份</option>{repair.existing_choices.map((item, i) => ({ item, i })).filter(({ item }) => `${item.label} ${item.character_name}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())).map(({ item, i }) => <option key={i} value={String(i)}>{item.label}{item.has_image ? "（已有形象）" : "（待生成形象）"}</option>)}</select></label></>}
            <Button disabled={busy || (mode === "create" ? !name.trim() : choice === "")} onClick={() => void run(async () => {
              const selected = repair.existing_choices[Number(choice)];
              const next = await selectCharacterRepair(context, repair, mode === "create" ? { mode, character_name: name.trim(), ...(repair.status === "creating" ? { identity_id: repair.identity_id } : {}), appearance, costume } : { mode, character_name: selected.character_name, identity_id: selected.identity_id });
              await accept(next); if (active.current && next.status === "selected") setCasting(true);
            })}>确认角色身份</Button>
          </> : <>
            <p>使用角色：{repair.character_name}；{repair.reuse_identity ? "沿用已有身份" : "身份已确认"}</p>
            {repair.error ? <p role="alert">{repairErrorMessage(repair.error)}</p> : null}
            <Button disabled={busy} onClick={() => setCasting(true)}>查看选角</Button>
            <Button disabled={busy} onClick={() => void run(async () => { await accept(await getCharacterRepair(context, repair.repair_id)); })}>刷新修复状态</Button>
            {repair.candidate_id ? <Button disabled={busy} onClick={() => void run(async () => { await accept(await bindCharacterRepair(context, repair)); })}>重试绑定</Button> : null}
            {casting ? <CharacterCastingPanel project={context.project} name={repair.character_name} identityId={repair.identity_id} onAdopted={async () => {
              if (!active.current) return;
              await accept(await getCharacterRepair(context, repair.repair_id));
            }} /> : null}
          </>}
        </>}
      </>}
    </DialogContent>
  </Dialog>;
}
