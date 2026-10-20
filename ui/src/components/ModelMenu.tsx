import { motion } from "motion/react";
import { Check, ChevronUp, Cpu, Eye } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { openSettings, type ModelInfo } from "../api";
import { spring } from "./ui";
import { useDismiss } from "./useDismiss";

export function modelMeta(m: ModelInfo) {
  return [m.vision ? "视觉" : "文本", m.bits ? `${m.bits} bit` : "", `${m.size_gb} GB`].filter(Boolean).join(" · ");
}

/** The chat model the agent uses (remembered; the same choice as the default in settings). */
export function ModelMenu({
  models,
  value,
  onChange,
  onOpen,
}: {
  models: ModelInfo[];
  value: string | null;
  onChange: (id: string) => void;
  onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useDismiss(open, useCallback(() => setOpen(false), []), root);
  const chat = models.filter((m) => m.kind === "chat");
  const current = value;
  const choose = (id: string) => {
    onChange(id);
    setOpen(false);
  };
  const item = "flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left transition-colors";

  return (
    <div ref={root} className="min-w-0 shrink self-center">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        title="对话模型"
        onClick={() => {
          if (!open) onOpen();
          setOpen((o) => !o);
        }}
        className="ink-soft flex h-9 max-w-full min-w-0 items-center gap-1 rounded-full px-2.5 text-xs font-medium hover:bg-[var(--glass-fill)]"
      >
        <Cpu className="size-4 shrink-0" />
        <span className="hidden truncate sm:inline">{current ?? "未选择模型"}</span>
        <motion.span className="shrink-0" animate={{ rotate: open ? 180 : 0 }} transition={spring}>
          <ChevronUp className="size-3.5" />
        </motion.span>
      </button>
      {open && (
        <motion.div
          role="menu"
          initial={{ y: 8, scale: 0.96 }}
          animate={{ y: 0, scale: 1 }}
          transition={spring}
          style={{ originX: 0.5, originY: 1 }}
          className="glass glass-solid scroll-soft absolute right-2 bottom-full z-30 mb-2 max-h-[max(12rem,calc(100dvh-15rem))] w-96 max-w-[calc(100%-1rem)] overflow-y-auto rounded-2xl p-1.5"
        >
          <div className="ink-faint px-2.5 pt-1 pb-1.5 text-[11px] font-medium">对话模型</div>
          {!chat.length && <div className="ink-soft px-2.5 py-2 text-xs">还没有可用的对话模型</div>}
          {chat.map((m) => (
            <button
              key={m.id}
              type="button"
              role="menuitemradio"
              aria-checked={m.id === value}
              disabled={!m.supported}
              title={m.note || undefined}
              onClick={() => choose(m.id)}
              className={`${item} enabled:hover:bg-[var(--glass-fill-strong)] disabled:opacity-45 ${m.id === value ? "bg-[var(--glass-fill)]" : ""}`}
            >
              {m.vision ? <Eye className="ink-soft size-4 shrink-0" /> : <Cpu className="ink-soft size-4 shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium">{m.id}</div>
                <div className="ink-soft truncate text-[11px]">{m.supported ? `${modelMeta(m)} · ${m.runtime}` : m.note}</div>
              </div>
              {m.id === value && <Check className="size-4 shrink-0 text-[var(--accent)]" />}
            </button>
          ))}
          <div className="ink-faint px-2.5 pt-1.5 pb-1 text-[11px] leading-relaxed">
            切换模型会卸载当前模型并加载新模型，首次加载需要十几秒。
            <button
              type="button"
              className="ml-1 underline underline-offset-2 hover:text-[var(--ink)]"
              onClick={() => {
                setOpen(false);
                openSettings("models");
              }}
            >
              下载更多模型
            </button>
          </div>
        </motion.div>
      )}
    </div>
  );
}
