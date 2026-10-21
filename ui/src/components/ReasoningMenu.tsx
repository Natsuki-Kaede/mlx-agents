import { motion } from "motion/react";
import { Brain, ChevronUp } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import type { Reasoning } from "../api";
import { spring } from "./ui";
import { MenuPanel } from "./MenuPanel";
import { useDismiss } from "./useDismiss";

/** Effort levels as in Claude; budgets match backend/engine.py REASONING_BUDGETS. */
export const REASONING: { value: Reasoning; label: string; hint: string }[] = [
  { value: "low", label: "Low", hint: "不思考，直接回答，最快" },
  { value: "medium", label: "Medium", hint: "最多约 1k tokens 的思考" },
  { value: "high", label: "High", hint: "最多约 4k tokens 的思考" },
  { value: "xhigh", label: "XHigh", hint: "最多约 8k tokens 的思考，可能要几分钟" },
  { value: "max", label: "Max", hint: "最多约 32k tokens 的思考，16 GB Mac 上可能要半小时" },
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
        <Brain className="size-4" />
        <span className="hidden sm:inline">推理 {current.label}</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={spring}>
          <ChevronUp className="size-3.5" />
        </motion.span>
      </button>
      {open && (
        <MenuPanel anchor={root} panelRef={panel} width={300} className="px-4 pt-3 pb-3.5">
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
          <div className="ink-soft mt-2.5 text-[11px] leading-snug">{current.hint}</div>
        </MenuPanel>
      )}
    </div>
  );
}
