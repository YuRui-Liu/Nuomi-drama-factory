import { cn } from "@/lib/utils";

type BrandMarkProps = {
  compact?: boolean;
  className?: string;
};

export function BrandMark({ compact = false, className }: BrandMarkProps) {
  return (
    <span
      aria-label="NuomiDrama"
      className={cn("inline-flex items-center gap-2 text-foreground", className)}
      role="img"
    >
      <svg aria-hidden="true" className="size-6 shrink-0" viewBox="0 0 24 24">
        <path d="M4 19V5h4l8 10V5h4v14h-4L8 9v10H4Z" fill="currentColor" />
        <path data-kind="edit-cut" data-testid="nuomidrama-cut" d="M8 5h8l-2 3H6l2-3Z" fill="var(--brand-accent, #e5ff5c)" />
        <path data-kind="edit-cut" data-testid="nuomidrama-cut" d="M10 16h8l-2 3H8l2-3Z" fill="var(--brand-accent, #e5ff5c)" />
      </svg>
      {!compact ? (
        <span aria-hidden="true" className="text-[15px] tracking-[-0.02em]">
          <span className="font-semibold">Nuomi</span>
          <span className="font-normal">Drama</span>
        </span>
      ) : null}
    </span>
  );
}
