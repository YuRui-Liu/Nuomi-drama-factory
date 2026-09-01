import { ArrowDown, ArrowUp, Merge, Save, Scissors } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { DramaticBeat } from "@/lib/queries/screenplay-semantics";

export function BeatEditor({ beats, selectedId, disabled, onSelect, onSplit, onMerge, onReorder, onUpdate }: {
  beats: DramaticBeat[]; selectedId: string; disabled?: boolean;
  onSelect: (id: string) => void; onSplit: (beat: DramaticBeat) => void;
  onMerge: (first: DramaticBeat, second: DramaticBeat) => void;
  onReorder?: (beatIds: string[]) => void;
  onUpdate?: (beat: DramaticBeat, updates: Partial<DramaticBeat>) => void;
}) {
  return <section className="min-w-0 overflow-y-auto p-3" aria-label="戏剧节拍编辑">
    <h3 className="mb-3 text-xs font-semibold text-muted-foreground">Dramatic Beat</h3>
    <div className="space-y-3">{beats.map((beat, index) => <article key={beat.id} className={`rounded-xl border p-3 ${beat.id === selectedId ? "border-primary/60 bg-primary/[0.08]" : "border-white/10 bg-white/[0.02]"}`}>
      <button type="button" className="w-full text-left" onClick={() => onSelect(beat.id)}>
        <strong className="text-sm">{beat.ordinal}. {beat.turn || beat.action}</strong>
        <div className="mt-2 space-y-1 text-xs text-muted-foreground">
          <p><span className="text-foreground/70">目标：</span>{beat.goal}</p>
          <p><span className="text-foreground/70">阻碍：</span>{beat.obstacle}</p>
          <p><span className="text-foreground/70">行动 → 结果：</span>{beat.action} → {beat.result}</p>
          <p><span className="text-foreground/70">情绪：</span>{beat.emotional_shift} · {beat.estimated_duration_seconds}s</p>
        </div>
      </button>
      <div className="mt-2 flex gap-2">
        <Button size="sm" variant="outline" disabled={disabled} onClick={() => onSplit(beat)}><Scissors />拆分节拍</Button>
        {index < beats.length - 1 && <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onMerge(beat, beats[index + 1])}><Merge />合并下一节拍</Button>}
        <Button size="sm" variant="ghost" aria-label={`上移 ${beat.id}`} disabled={disabled || index === 0} onClick={() => {
          const ids = beats.map((item) => item.id);
          [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]];
          onReorder?.(ids);
        }}><ArrowUp /></Button>
        <Button size="sm" variant="ghost" aria-label={`下移 ${beat.id}`} disabled={disabled || index === beats.length - 1} onClick={() => {
          const ids = beats.map((item) => item.id);
          [ids[index], ids[index + 1]] = [ids[index + 1], ids[index]];
          onReorder?.(ids);
        }}><ArrowDown /></Button>
      </div>
      {beat.id === selectedId && <form key={beat.id} className="mt-3 grid gap-2 border-t border-white/10 pt-3 sm:grid-cols-2" onSubmit={(event) => {
        event.preventDefault();
        const values = new FormData(event.currentTarget);
        const text = (name: string) => String(values.get(name) ?? "").trim();
        onUpdate?.(beat, {
          goal: text("goal"), obstacle: text("obstacle"), action: text("action"),
          reaction: text("reaction"), turn: text("turn"), result: text("result"),
          emotional_shift: text("emotional_shift"),
          estimated_duration_seconds: Number(values.get("estimated_duration_seconds")),
        });
      }}>
        {(["goal", "obstacle", "action", "reaction", "turn", "result", "emotional_shift"] as const).map((field) => <label key={field} className="space-y-1 text-xs text-muted-foreground"><span>{{ goal: "目标", obstacle: "阻碍", action: "行动", reaction: "反应", turn: "转折", result: "结果", emotional_shift: "情绪变化" }[field]}</span><Input name={field} defaultValue={beat[field]} disabled={disabled} /></label>)}
        <label className="space-y-1 text-xs text-muted-foreground"><span>预计时长（秒）</span><Input name="estimated_duration_seconds" type="number" min={0.1} max={30} step={0.1} defaultValue={beat.estimated_duration_seconds} disabled={disabled} /></label>
        <div className="sm:col-span-2"><Button type="submit" size="sm" disabled={disabled}><Save />保存字段</Button></div>
      </form>}
    </article>)}</div>
  </section>;
}

