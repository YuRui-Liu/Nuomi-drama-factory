// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

interface ModelComboboxProps {
  /** 当前模型 ID（受控）。 */
  value: string;
  /** 该运行时下所有候选模型 ID；为空数组表示"无可候选，直接手输"。 */
  options: string[];
  /** 输入框占位（同时也是空值时按钮显示的内容）。 */
  placeholder?: string;
  /** 用于 a11y 的标签。 */
  ariaLabel: string;
  /** 任何用户确认的模型 ID 变更都通过这里回传。 */
  onChange: (next: string) => void;
  /** 只读模式：禁用触发按钮与编辑输入框。默认 false（可编辑）。 */
  disabled?: boolean;
}

/**
 * 模型下拉选择器：始终展示当前运行时所有候选模型，支持自由输入自定义 ID。
 *
 * 实现要点：
 * - 用按钮当触发器（样式仿照输入框），点开后弹出真正可编辑的输入框 + 候选列表。
 * - 关闭弹窗时若 draft 与外部 value 不同，会把 draft 提交上去（允许清空）。
 * - 用 ref 标记"主动 commit 后再由 onOpenChange 触发的关闭"，避免重复回传。
 * - 不走浏览器原生 <datalist>——后者会按当前 value 过滤候选，导致选了模型后下拉只显示自己。
 */
export function ModelCombobox({ value, options, placeholder, ariaLabel, onChange, disabled = false }: ModelComboboxProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  // 主动 commit 或 Esc 取消时标记，避免 onOpenChange 同步触发再次回传。
  const committedRef = useRef(false);

  // 外部 value 变化（如切 runtime 后默认填值）时同步 draft。
  useEffect(() => {
    setDraft(value);
  }, [value]);

  const commitDraft = (override?: string) => {
    committedRef.current = true;
    const next = (override ?? draft).trim();
    setDraft(next);
    onChange(next);
    setOpen(false);
  };

  const cancelDraft = () => {
    committedRef.current = true;
    setDraft(value);
    setOpen(false);
  };

  const handleOpenChange = (next: boolean) => {
    if (next) {
      setDraft(value);
      committedRef.current = false;
    } else if (!committedRef.current && draft !== value) {
      // 关闭时如未显式 commit，自动把 draft 提交（允许清空）。
      onChange(draft.trim());
    }
    setOpen(next);
  };

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger
        aria-label={ariaLabel}
        disabled={disabled}
        className={cn(
          "flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-input bg-transparent px-2.5 text-left text-sm transition-colors outline-none",
          "hover:bg-accent/30 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
          "data-[popup-open]:border-ring data-[popup-open]:ring-3 data-[popup-open]:ring-ring/50",
          disabled && "cursor-not-allowed opacity-60",
          !value && "text-muted-foreground",
        )}
      >
        <span className="truncate">{value || placeholder}</span>
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </PopoverTrigger>
      <PopoverContent align="start" alignOffset={0} sideOffset={6} className="w-80 p-1.5">
        <div className="flex items-center gap-1">
          <Input
            autoFocus
            aria-label={`${ariaLabel}编辑`}
            value={draft}
            placeholder={placeholder}
            disabled={disabled}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                commitDraft();
              } else if (event.key === "Escape") {
                event.preventDefault();
                cancelDraft();
              }
            }}
            className="h-7 text-xs"
          />
          <button
            type="button"
            aria-label="取消"
            onClick={cancelDraft}
            className="rounded px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-accent"
          >
            取消
          </button>
        </div>
        <p className="mt-1 px-1 text-[10px] text-muted-foreground">
          {options.length > 0
            ? `共 ${options.length} 个候选；可直接输入自定义 ID。`
            : "无候选模型，请直接输入自定义 ID。"}
        </p>
        {options.length > 0 ? (
          <ul
            className="mt-1 max-h-60 overflow-y-auto rounded border border-border/60 bg-background/40 py-0.5"
            role="listbox"
            aria-label={`${ariaLabel}候选`}
          >
            {options.map((option) => {
              const selected = option === value;
              return (
                <li key={option} role="option" aria-selected={selected}>
                  <button
                    type="button"
                    onClick={() => commitDraft(option)}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left text-xs hover:bg-accent",
                      selected && "bg-accent/70 font-medium text-accent-foreground",
                    )}
                  >
                    <span className="truncate">{option}</span>
                    {selected ? <Check className="size-3.5 shrink-0 text-primary" /> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
        {draft.trim() && !options.includes(draft.trim()) ? (
          <button
            type="button"
            onClick={() => commitDraft(draft.trim())}
            className="mt-1 flex w-full items-center rounded-sm border border-dashed border-border/80 px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-accent"
          >
            使用自定义值 “{draft.trim()}”
          </button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}