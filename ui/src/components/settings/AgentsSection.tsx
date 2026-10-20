import { ArrowLeft, Pencil, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AGENTS_CHANGED, DEFAULT_AGENT, NO_PERMISSIONS, api, type AgentProfile, type ModelInfo } from "../../api";
import { PERMISSION_ITEMS } from "../ToolsMenu";
import { Button, Label, Toggle } from "../ui";

type Draft = Omit<AgentProfile, "id"> & { id?: string };
const BLANK: Draft = {
  name: "",
  icon: "🤖",
  description: "",
  instructions: "",
  permissions: NO_PERMISSIONS,
  model: null,
};

function Editor({ draft, models, onSave, onCancel, onDelete }: {
  draft: Draft;
  models: ModelInfo[];
  onSave: (d: Draft) => void;
  onCancel: () => void;
  onDelete?: () => void;
}) {
  const [d, setD] = useState(draft);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setD((x) => ({ ...x, [key]: value }));
  return (
    <div className="space-y-4">
      <button type="button" onClick={onCancel} className="ink-soft inline-flex items-center gap-1 text-xs font-medium hover:underline">
        <ArrowLeft className="size-3.5" /> 返回列表
      </button>
      <div className="grid grid-cols-[4.5rem_1fr] gap-3">
        <div>
          <Label>图标</Label>
          <input className="field text-center text-lg" value={d.icon} maxLength={8} onChange={(e) => set("icon", e.target.value)} />
        </div>
        <div>
          <Label>名称</Label>
          <input className="field" value={d.name} maxLength={40} onChange={(e) => set("name", e.target.value)} placeholder="例如：翻译官" />
        </div>
      </div>
      <div>
        <Label>简介</Label>
        <input className="field" value={d.description} maxLength={200} onChange={(e) => set("description", e.target.value)} placeholder="一句话说明它擅长什么" />
      </div>
      <div>
        <Label hint={`${d.instructions.length} / 8000`}>指令</Label>
        <textarea
          className="field min-h-36 font-mono text-[13px] leading-relaxed"
          value={d.instructions}
          maxLength={8000}
          onChange={(e) => set("instructions", e.target.value)}
          placeholder="它的角色、工作方式和回答要求，会放进系统提示词"
        />
      </div>
      <div>
        <Label>默认开启的工具</Label>
        <div className="grid gap-2 sm:grid-cols-2">
          {PERMISSION_ITEMS.map(({ key, label, icon: Icon }) => (
            <div key={key} className="glass flex items-center gap-2 rounded-xl px-3 py-2">
              <Icon className="ink-soft size-4" />
              <span className="flex-1 text-[13px] font-medium">{label}</span>
              <Toggle checked={!!d.permissions[key]} onChange={(v) => set("permissions", { ...d.permissions, [key]: v })} label="" />
            </div>
          ))}
        </div>
      </div>
      <div>
        <Label>模型</Label>
        <select className="field" value={d.model ?? ""} onChange={(e) => set("model", e.target.value || null)}>
          <option value="">跟随默认对话模型</option>
          {models.filter((m) => m.kind === "chat" && m.supported).map((m) => (
            <option key={m.id} value={m.id}>
              {m.id}
            </option>
          ))}
        </select>
      </div>
      <div className="flex gap-2">
        <Button variant="primary" disabled={!d.name.trim() || !d.icon.trim()} onClick={() => onSave(d)}>
          保存
        </Button>
        <Button onClick={onCancel}>取消</Button>
        <div className="flex-1" />
        {onDelete && (
          <Button variant="ghost" className="text-[var(--danger)]" icon={<Trash2 className="size-4" />} onClick={onDelete}>
            删除
          </Button>
        )}
      </div>
    </div>
  );
}

/** Agent presets: instructions plus the tools and model a chat starts with. */
export function AgentsSection({ onError }: { onError: (e: string | null) => void }) {
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [editing, setEditing] = useState<Draft | null>(null);
  const refresh = useCallback(() => api.agents().then(setAgents).catch((e) => onError(String(e))), [onError]);

  useEffect(() => {
    refresh();
    api.models().then((l) => setModels(l.models)).catch(() => undefined);
  }, [refresh]);

  const run = async (fn: () => Promise<unknown>) => {
    onError(null);
    try {
      await fn();
      await refresh();
      setEditing(null);
      window.dispatchEvent(new Event(AGENTS_CHANGED));
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  if (editing) {
    return (
      <Editor
        key={editing.id ?? "new"}
        draft={editing}
        models={models}
        onCancel={() => setEditing(null)}
        onSave={(d) => run(() => api.saveAgent(d))}
        onDelete={
          editing.id && editing.id !== DEFAULT_AGENT
            ? () => window.confirm(`删除 agent「${editing.name}」？`) && run(() => api.deleteAgent(editing.id!))
            : undefined
        }
      />
    );
  }

  return (
    <div className="space-y-2">
      <div className="ink-faint text-xs leading-relaxed">
        每个 agent 有自己的指令、默认工具和模型。在输入框左下角切换；对话里还可以再单独调整工具和模型。
      </div>
      {agents.map((a) => (
        <div key={a.id} className="glass flex items-center gap-3 rounded-xl px-3 py-2.5">
          <span className="text-xl leading-none">{a.icon}</span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium">{a.name}</div>
            <div className="ink-soft truncate text-[11px]">
              {[a.description, PERMISSION_ITEMS.filter((i) => a.permissions[i.key]).map((i) => i.label).join("、"), a.model].filter(Boolean).join(" · ")}
            </div>
          </div>
          <button type="button" title="编辑" onClick={() => setEditing({ ...a, permissions: { ...NO_PERMISSIONS, ...a.permissions } })} className="ink-soft grid size-8 shrink-0 place-items-center rounded-full hover:bg-[var(--glass-fill-strong)]">
            <Pencil className="size-3.5" />
          </button>
        </div>
      ))}
      <Button className="w-full" icon={<Plus className="size-4" />} onClick={() => setEditing(BLANK)}>
        新建 agent
      </Button>
    </div>
  );
}
