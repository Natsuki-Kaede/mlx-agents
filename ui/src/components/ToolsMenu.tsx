import { AnimatePresence, motion } from "motion/react";
import { Bot, FolderOpen, Globe, ImageIcon, ShieldCheck, Terminal } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import type { ApprovalKind, ChatSettings, Permissions } from "../api";
import { Toggle, spring } from "./ui";
import { useDismiss } from "./useDismiss";

export const PERMISSION_ITEMS: { key: keyof Permissions; label: string; hint: string; icon: typeof Globe }[] = [
  { key: "web", label: "联网", hint: "DuckDuckGo 搜索并阅读网页；搜索词会发到外网", icon: Globe },
  { key: "image", label: "生图", hint: "调用本地生图模型，每张需几分钟", icon: ImageIcon },
  { key: "files", label: "文件", hint: "在工作目录里查找、搜索、读写文件；写入前需要你批准", icon: FolderOpen },
  { key: "exec", label: "命令", hint: "在工作目录里运行命令；运行前需要你批准", icon: Terminal },
  { key: "agents", label: "子 agent", hint: "把独立的子任务交给子 agent，在单独的上下文里完成后汇报", icon: Bot },
];

const AUTO: { kind: ApprovalKind; needs: keyof Permissions; label: string }[] = [
  { kind: "write", needs: "files", label: "自动批准写入文件" },
  { kind: "exec", needs: "exec", label: "自动批准运行命令" },
];

/** Per-chat permissions, opened from a pill in the input bar. */
export function ToolsMenu({ value, onChange }: { value: ChatSettings; onChange: (v: ChatSettings) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const auto = value.autoApprove ?? [];
  const enabled = PERMISSION_ITEMS.filter((i) => value[i.key]);

  useDismiss(open, useCallback(() => setOpen(false), []), root);

  // The wrapper is not positioned: the menu anchors to the input bar, so it stays on screen at narrow widths.
  return (
    <div ref={root} className="shrink-0 self-center">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        title="权限"
        onClick={() => setOpen((o) => !o)}
        className="glass flex h-9 items-center gap-1 rounded-full px-2.5 text-xs font-medium"
      >
        <ShieldCheck className="size-4 text-[var(--accent)]" />
        <span className="hidden items-center gap-0.5 sm:inline-flex">
          {enabled.length ? enabled.map(({ key, icon: Icon }) => <Icon key={key} className="ink-soft size-3.5" />) : "仅对话"}
        </span>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            role="menu"
            initial={{ opacity: 0, y: 8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.96 }}
            transition={spring}
            style={{ originX: 1, originY: 1 }}
            className="glass glass-solid scroll-soft absolute right-2 bottom-full z-30 mb-2 max-h-[max(12rem,calc(100dvh-15rem))] w-80 max-w-[calc(100%-1rem)] space-y-1 overflow-y-auto rounded-2xl p-2"
          >
            <div className="ink-faint px-2 pt-0.5 pb-1 text-[11px] font-medium">这段对话里 agent 可以使用</div>
            {PERMISSION_ITEMS.map(({ key, label, hint, icon: Icon }) => (
              <div key={key} className="flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-[var(--glass-fill)]">
                <Icon className="ink-soft size-4 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium">{label}</div>
                  <div className="ink-soft text-[11px] leading-snug">{hint}</div>
                </div>
                <Toggle checked={value[key]} onChange={(v) => onChange({ ...value, [key]: v })} label="" />
              </div>
            ))}
            {AUTO.some((a) => value[a.needs]) && (
              <div className="mt-1 space-y-2 border-t border-[var(--glass-edge-low)] px-2 pt-2.5 pb-1">
                {AUTO.filter((a) => value[a.needs]).map((a) => (
                  <Toggle
                    key={a.kind}
                    checked={auto.includes(a.kind)}
                    onChange={(v) => onChange({ ...value, autoApprove: v ? [...auto, a.kind] : auto.filter((k) => k !== a.kind) })}
                    label={a.label}
                  />
                ))}
                <div className="ink-faint text-[11px]">读取文件和列目录不需要审批。只建议在信任的任务里开启自动批准。</div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
