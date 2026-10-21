import { motion } from "motion/react";
import { ChevronDown, Lightbulb } from "lucide-react";
import { useEffect, useState } from "react";
import { Markdown, countChars } from "./Markdown";
import { useStickToBottom } from "./useStickToBottom";
import { spring } from "./ui";

/** Collapsible reasoning trace: open while the model is thinking, folds away once the answer starts. */
export function ThinkingBlock({ text, tokens, active }: { text: string; tokens?: number; active: boolean }) {
  const [open, setOpen] = useState(active);
  const { ref, contentRef } = useStickToBottom();
  useEffect(() => setOpen(active), [active]);

  return (
    <div className="glass w-full rounded-2xl px-3.5 py-2.5">
      <button type="button" onClick={() => setOpen((o) => !o)} className="ink-soft flex w-full items-center gap-2 text-left text-xs font-medium">
        <Lightbulb className={`size-3.5 ${active ? "animate-pulse text-[var(--accent)]" : ""}`} />
        <span className={active ? "shimmer-text" : ""}>{active ? "思考中…" : "思考过程"}</span>
        <span className="ink-faint tabular-nums">
          {tokens !== undefined ? `${tokens} tokens · ` : ""}
          {countChars(text)} 字
        </span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={spring} className="ml-auto">
          <ChevronDown className="size-3.5" />
        </motion.span>
      </button>
      {open && (
        <div ref={ref} className="scroll-soft mt-2 max-h-72 overflow-y-auto border-l-2 border-[var(--glass-edge)] pl-3 text-[13px]">
          <div ref={contentRef}>
            <Markdown text={text} streaming={active} className="ink-soft" />
          </div>
        </div>
      )}
    </div>
  );
}
