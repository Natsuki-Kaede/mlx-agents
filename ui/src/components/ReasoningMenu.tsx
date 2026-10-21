import { motion } from "motion/react";
import { ChevronUp, Lightbulb } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import type { Reasoning } from "../api";
import { spring } from "./ui";
import { MenuPanel } from "./MenuPanel";
import { useDismiss } from "./useDismiss";

/** Effort levels as in Claude; budgets match backend/engine.py REASONING_BUDGETS. The cost is the most thinking
 * a reply may add, with time estimated at about 15 tokens/s (a 4-bit 9B model on a 16 GB Mac). */
export const REASONING: { value: Reasoning; label: string; cost: string }[] = [
  { value: "low", label: "Low", cost: "不思考，直接回答" },
  { value: "medium", label: "Medium", cost: "思考 ≤ 1k tokens · 约 1 分钟" },
  { value: "high", label: "High", cost: "思考 ≤ 4k tokens · 约 4 分钟" },
  { value: "xhigh", label: "XHigh", cost: "思考 ≤ 8k tokens · 约 9 分钟" },
  { value: "max", label: "Max", cost: "思考 ≤ 32k tokens · 约 35 分钟" },
];

/** Pill button that opens a slider of reasoning levels above it. */
export function ReasoningMenu({ value, onChange }: { value: Reasoning; onChange: (v: Reasoning) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const index = Math.max(0, REASONING.findIndex((r) => r.value === value));
  const current = REASONING[index];

  const panel = useRef<HTMLDivElement>(null);
  useDismiss(open, useCallback(() => setOpen(false), []), root, panel);

  return (
    <div ref={root} className="shrink-0 self-center">
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        title={`推理等级：${current.label}`}
        onClick={() => setOpen((o) => !o)}
        className={`flex h-9 items-center gap-1 rounded-full px-2.5 text-xs font-medium transition-colors ${
          index === 0 ? "ink-soft hover:bg-[var(--glass-fill)]" : "glass text-[var(--accent)]"
        }`}
      >
        <Lightbulb className="size-4" />
        <span className="hidden sm:inline">推理 {current.label}</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={spring}>
          <ChevronUp className="size-3.5" />
        </motion.span>
      </button>
      {open && (
        <MenuPanel anchor={root} panelRef={panel} width={232} className="px-3.5 pt-3 pb-3">
          <div className="flex items-baseline justify-between">
            <span className="ink-faint text-[11px] font-medium">推理等级</span>
            <span className="text-[13px] font-semibold text-[var(--accent)]">{current.label}</span>
          </div>
          <input
            type="range"
            aria-label="推理等级"
            aria-valuetext={current.label}
            className="slider mt-3"
            style={{ ["--fill" as string]: `${(index / (REASONING.length - 1)) * 100}%` }}
            min={0}
            max={REASONING.length - 1}
            step={1}
            value={index}
            onChange={(e) => onChange(REASONING[Number(e.target.value)].value)}
          />
          {/* Each label sits under its stop: the thumb's centre runs from half a thumb in at each end. */}
          <div className="relative mx-2.5 mt-1.5 h-4 text-[10px]">
            {REASONING.map((r, i) => (
              <button
                key={r.value}
                type="button"
                onClick={() => onChange(r.value)}
                style={{ left: `${(i / (REASONING.length - 1)) * 100}%` }}
                className={`absolute -translate-x-1/2 ${i === index ? "font-semibold text-[var(--accent)]" : "ink-faint"}`}
              >
                {r.label}
              </button>
            ))}
          </div>
          <div className="ink-soft mt-2 text-[11px] tabular-nums" title="思考上限，时间按约 15 tokens/秒估算">
            {current.cost}
          </div>
        </MenuPanel>
      )}
    </div>
  );
}
