import { motion } from "motion/react";
import { Brain, Check, ChevronUp } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import type { Reasoning } from "../api";
import { spring } from "./ui";
import { MenuPanel } from "./MenuPanel";
import { useDismiss } from "./useDismiss";

export const REASONING: { value: Reasoning; label: string; hint: string }[] = [
  { value: "off", label: "关闭", hint: "直接回答，最快" },
  { value: "low", label: "低", hint: "最多约 512 tokens 的思考" },
  { value: "medium", label: "中", hint: "最多约 2048 tokens 的思考" },
  { value: "high", label: "高", hint: "最多约 8192 tokens 的思考，最慢" },
];

/** Pill button that opens a menu of reasoning levels above it. */
export function ReasoningMenu({ value, onChange }: { value: Reasoning; onChange: (v: Reasoning) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const current = REASONING.find((r) => r.value === value) ?? REASONING[0];

  const panel = useRef<HTMLDivElement>(null);
  useDismiss(open, useCallback(() => setOpen(false), []), root, panel);

  return (
    <div ref={root} className="shrink-0 self-center">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        title={`推理等级：${current.label}`}
        onClick={() => setOpen((o) => !o)}
        className={`flex h-9 items-center gap-1 rounded-full px-2.5 text-xs font-medium transition-colors ${
          value === "off" ? "ink-soft hover:bg-[var(--glass-fill)]" : "glass text-[var(--accent)]"
        }`}
      >
        <Brain className="size-4" />
        <span className="hidden sm:inline">推理 {current.label}</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={spring}>
          <ChevronUp className="size-3.5" />
        </motion.span>
      </button>
      {open && (
        <MenuPanel anchor={root} panelRef={panel} width={240} className="p-1.5">
          <div className="ink-faint px-2.5 pt-1 pb-1.5 text-[11px] font-medium">推理等级</div>
          {REASONING.map((r) => (
            <button
              key={r.value}
              type="button"
              role="menuitemradio"
              aria-checked={r.value === value}
              onClick={() => {
                onChange(r.value);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left transition-colors hover:bg-[var(--glass-fill-strong)] ${
                r.value === value ? "bg-[var(--glass-fill)]" : ""
              }`}
            >
              <div className="flex-1">
                <div className="text-[13px] font-medium">{r.label}</div>
                <div className="ink-soft text-[11px]">{r.hint}</div>
              </div>
              {r.value === value && <Check className="size-4 text-[var(--accent)]" />}
            </button>
          ))}
        </MenuPanel>
      )}
    </div>
  );
}
