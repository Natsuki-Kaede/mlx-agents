import { CheckCircle2, Circle, ListChecks, Loader2 } from "lucide-react";
import type { PlanStep } from "../api";

/** The agent's step-by-step plan for a multi-step task. */
export function PlanCard({ plan, live }: { plan: PlanStep[]; live: boolean }) {
  const done = plan.filter((s) => s.status === "done").length;
  return (
    <div className="glass w-full rounded-2xl px-3.5 py-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium">
        <ListChecks className="size-3.5 text-[var(--accent)]" />
        计划
        <span className="ink-faint tabular-nums">
          {done}/{plan.length}
        </span>
        <div className="ml-auto h-1 w-24 overflow-hidden rounded-full bg-[var(--glass-fill-strong)]">
          <div className="accent-fill h-full transition-[width] duration-500" style={{ width: `${(done / Math.max(plan.length, 1)) * 100}%` }} />
        </div>
      </div>
      <ol className="space-y-1">
        {plan.map((step, i) => (
          <li key={i} className="flex items-start gap-2 text-[13px] leading-snug">
            {step.status === "done" ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[var(--ok)]" />
            ) : step.status === "in_progress" ? (
              live ? <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-[var(--accent)]" /> : <Circle className="mt-0.5 size-4 shrink-0 text-[var(--accent)]" />
            ) : (
              <Circle className="ink-faint mt-0.5 size-4 shrink-0" />
            )}
            <span className={step.status === "done" ? "ink-soft line-through decoration-[var(--ink-faint)]" : step.status === "in_progress" ? "font-medium" : "ink-soft"}>
              {step.text}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
