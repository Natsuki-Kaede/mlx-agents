import { Brain, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Memory } from "../../api";
import { Button, Label } from "../ui";

/** Long-term memory shared by every chat. */
export function MemorySection({ onError }: { onError: (e: string | null) => void }) {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    api.memories().then(setMemories).catch((e) => onError(String(e)));
  }, [onError]);

  const run = async (fn: () => Promise<void>) => {
    onError(null);
    try {
      await fn();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <section className="space-y-2">
      <Label hint={`${memories.length} 条`}>
        <span className="inline-flex items-center gap-1.5">
          <Brain className="size-4" /> 长期记忆
        </span>
      </Label>
      <div className="ink-faint text-xs">每段对话都会带上这些内容。Agent 也会在你表达偏好时自己记住。</div>
      <div className="space-y-1.5">
        {memories.map((m) => (
          <div key={m.id} className="glass flex items-start gap-2 rounded-xl px-3 py-2 text-[13px]">
            <span className="flex-1 leading-relaxed">{m.text}</span>
            <button
              type="button"
              title="删除"
              onClick={() =>
                run(async () => {
                  await api.deleteMemory(m.id);
                  setMemories((l) => l.filter((x) => x.id !== m.id));
                })
              }
              className="ink-soft grid size-7 shrink-0 place-items-center rounded-full hover:bg-[var(--glass-fill-strong)]"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
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
  );
}
