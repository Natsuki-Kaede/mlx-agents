import { motion } from "motion/react";
import { Check, ChevronUp, Settings2 } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { openSettings, type AgentProfile } from "../api";
import { spring } from "./ui";
import { useDismiss } from "./useDismiss";

/** Which agent answers in this chat: its instructions, and its tools and model as starting switches. */
export function AgentMenu({
  agents,
  current,
  onChange,
  onOpen,
}: {
  agents: AgentProfile[];
  current: AgentProfile | undefined;
  onChange: (agent: AgentProfile) => void;
  onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useDismiss(open, useCallback(() => setOpen(false), []), root);

  return (
    <div ref={root} className="shrink-0 self-center">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Agent"
        onClick={() => {
          if (!open) onOpen();
          setOpen((o) => !o);
        }}
        className="glass flex h-9 items-center gap-1.5 rounded-full pr-2 pl-2.5 text-xs font-medium"
      >
        <span className="text-sm leading-none">{current?.icon ?? "✨"}</span>
        <span className="hidden max-w-28 truncate sm:inline">{current?.name ?? "通用助手"}</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={spring}>
          <ChevronUp className="ink-soft size-3.5" />
        </motion.span>
      </button>
      {open && (
        <motion.div
          role="menu"
          initial={{ y: 8, scale: 0.96 }}
          animate={{ y: 0, scale: 1 }}
          transition={spring}
          style={{ originX: 0, originY: 1 }}
          className="glass glass-solid scroll-soft absolute bottom-full left-2 z-30 mb-2 max-h-[max(12rem,calc(100dvh-15rem))] w-80 max-w-[calc(100%-1rem)] overflow-y-auto rounded-2xl p-1.5"
        >
          <div className="ink-faint px-2.5 pt-1 pb-1.5 text-[11px] font-medium">选择 agent（会套用它的工具和模型）</div>
          {agents.map((a) => (
            <button
              key={a.id}
              type="button"
              role="menuitemradio"
              aria-checked={a.id === current?.id}
              onClick={() => {
                onChange(a);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left transition-colors hover:bg-[var(--glass-fill-strong)] ${a.id === current?.id ? "bg-[var(--glass-fill)]" : ""}`}
            >
              <span className="text-lg leading-none">{a.icon}</span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium">{a.name}</div>
                <div className="ink-soft truncate text-[11px]">{a.description || "（没有简介）"}</div>
              </div>
              {a.id === current?.id && <Check className="size-4 shrink-0 text-[var(--accent)]" />}
            </button>
          ))}
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              openSettings("agents");
            }}
            className="ink-soft mt-1 flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-xs font-medium hover:bg-[var(--glass-fill-strong)]"
          >
            <Settings2 className="size-4" /> 管理和新建 agent
          </button>
        </motion.div>
      )}
    </div>
  );
}
