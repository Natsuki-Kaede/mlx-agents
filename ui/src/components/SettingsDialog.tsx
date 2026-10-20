import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AgentsSection } from "./settings/AgentsSection";
import { GeneralSection } from "./settings/GeneralSection";
import { McpSection } from "./settings/McpSection";
import { MemorySection } from "./settings/MemorySection";
import { ModelsSection } from "./settings/ModelsSection";
import { ErrorNote, Glass, Segmented, spring } from "./ui";

export type SettingsTab = "general" | "agents" | "models" | "mcp" | "memory";
const TABS: { value: SettingsTab; label: string; render: (onError: (e: string | null) => void) => ReactNode }[] = [
  { value: "general", label: "通用", render: (onError) => <GeneralSection onError={onError} /> },
  { value: "agents", label: "Agents", render: (onError) => <AgentsSection onError={onError} /> },
  { value: "models", label: "模型", render: (onError) => <ModelsSection onError={onError} /> },
  { value: "mcp", label: "MCP", render: (onError) => <McpSection onError={onError} /> },
  { value: "memory", label: "记忆", render: (onError) => <MemorySection onError={onError} /> },
];

/** App settings: workspace, agents, models, MCP servers, long-term memory. */
export function SettingsDialog({ open, initialTab, onClose }: { open: boolean; initialTab?: SettingsTab; onClose: () => void }) {
  const [tab, setTab] = useState<SettingsTab>("general");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) setError(null);
  }, [open, tab]);
  useEffect(() => {
    if (open && initialTab) setTab(initialTab);
  }, [open, initialTab]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 grid place-items-center bg-black/35 p-4 backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose}>
          <Glass
            initial={{ opacity: 0, y: 16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.97 }}
            transition={spring}
            onClick={(e) => e.stopPropagation()}
            className="glass-solid flex max-h-[88dvh] w-full max-w-2xl flex-col gap-4 p-6"
          >
            <div className="flex items-center justify-between">
              <div className="text-lg font-semibold">设置</div>
              <button type="button" onClick={onClose} className="grid size-8 place-items-center rounded-full hover:bg-[var(--glass-fill)]">
                <X className="size-4" />
              </button>
            </div>
            <Segmented id="settings-tab" options={TABS.map(({ value, label }) => ({ value, label }))} value={tab} onChange={setTab} />
            <div className="scroll-soft -mx-1 min-h-0 flex-1 overflow-y-auto px-1 pb-1">{TABS.find((t) => t.value === tab)!.render(setError)}</div>
            <ErrorNote error={error} />
          </Glass>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
