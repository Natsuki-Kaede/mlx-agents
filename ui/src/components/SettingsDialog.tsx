import { AnimatePresence, motion } from "motion/react";
import { Brain, FolderOpen, Plus, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, type AppSettings, type Memory } from "../api";
import { Button, ErrorNote, Glass, Label, spring } from "./ui";

/** Workspace folder and long-term memory. */
export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [workspace, setWorkspace] = useState("");
  const [memories, setMemories] = useState<Memory[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSaved(false);
    api
      .settings()
      .then((s) => {
        setSettings(s);
        setWorkspace(s.workspace);
      })
      .catch((e) => setError(String(e)));
    api.memories().then(setMemories).catch((e) => setError(String(e)));
  }, [open]);

  const run = async (fn: () => Promise<void>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

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
            className="glass-solid scroll-soft max-h-[88dvh] w-full max-w-xl space-y-6 overflow-y-auto p-6"
          >
            <div className="flex items-center justify-between">
              <div className="text-lg font-semibold">设置</div>
              <button type="button" onClick={onClose} className="grid size-8 place-items-center rounded-full hover:bg-[var(--glass-fill)]">
                <X className="size-4" />
              </button>
            </div>

            <section className="space-y-2">
              <Label>
                <span className="inline-flex items-center gap-1.5">
                  <FolderOpen className="size-4" /> 工作目录
                </span>
              </Label>
              <div className="flex gap-2">
                <input className="field font-mono text-[13px]" value={workspace} onChange={(e) => setWorkspace(e.target.value)} placeholder="~/MLX Agents" />
                <Button
                  variant="primary"
                  onClick={() =>
                    run(async () => {
                      const s = await api.saveSettings({ workspace });
                      setSettings(s);
                      setWorkspace(s.workspace);
                      setSaved(true);
                    })
                  }
                >
                  保存
                </Button>
              </div>
              <div className="ink-faint text-xs leading-relaxed">
                Agent 的文件工具和命令只能在这个目录里工作。{settings && `当前：${settings.workspace_resolved}`}
                {saved && " · 已保存"}
              </div>
            </section>

            <section className="space-y-2">
              <Label hint={`${memories.length} 条`}>
                <span className="inline-flex items-center gap-1.5">
                  <Brain className="size-4" /> 长期记忆
                </span>
              </Label>
              <div className="ink-faint text-xs">每段对话都会带上这些内容。Agent 也会在你表达偏好时自己记住。</div>
              <div className="space-y-1.5">
                {memories.map((m) => (
                  <motion.div key={m.id} layout className="glass flex items-start gap-2 rounded-xl px-3 py-2 text-[13px]">
                    <span className="flex-1 leading-relaxed">{m.text}</span>
                    <button
                      type="button"
                      title="删除"
                      onClick={() => run(async () => {
                        await api.deleteMemory(m.id);
                        setMemories((l) => l.filter((x) => x.id !== m.id));
                      })}
                      className="ink-soft grid size-7 shrink-0 place-items-center rounded-full hover:bg-[var(--glass-fill-strong)]"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </motion.div>
                ))}
                {!memories.length && <div className="ink-faint py-2 text-center text-xs">还没有记忆</div>}
              </div>
              <div className="flex gap-2">
                <input className="field text-[13px]" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="例如：生图默认用 16:9、偏好水彩风格" />
                <Button
                  icon={<Plus className="size-4" />}
                  disabled={!draft.trim()}
                  onClick={() =>
                    run(async () => {
                      const m = await api.addMemory(draft.trim());
                      setMemories((l) => [...l, m]);
                      setDraft("");
                    })
                  }
                >
                  添加
                </Button>
              </div>
            </section>
            <ErrorNote error={error} />
          </Glass>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
