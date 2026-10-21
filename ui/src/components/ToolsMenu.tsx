import { FolderOpen, Globe, ImageIcon, Plug, ShieldCheck, ShieldOff, Terminal } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import type { AppSettings, Permissions, SettingsPatch } from "../api";
import { Toggle } from "./ui";
import { MenuPanel } from "./MenuPanel";
import { useDismiss } from "./useDismiss";

export const PERMISSION_ITEMS: { key: keyof Permissions; label: string; hint: string; icon: typeof Globe }[] = [
  { key: "web", label: "联网", hint: "DuckDuckGo 搜索并阅读网页；搜索词会发到外网", icon: Globe },
  { key: "image", label: "生图", hint: "调用本地生图模型，每张需几分钟", icon: ImageIcon },
  { key: "files", label: "文件", hint: "在工作目录里查找、搜索、读写文件；写入前需要你批准", icon: FolderOpen },
  { key: "exec", label: "命令", hint: "在工作目录里运行命令；运行前需要你批准", icon: Terminal },
  { key: "mcp", label: "MCP", hint: "调用你在设置里接入的 MCP 服务的工具；调用前需要你批准", icon: Plug },
];

/** The agent's tools and the auto-approve mode; they apply to every chat and are remembered. */
export function ToolsMenu({ value, onChange }: { value: AppSettings; onChange: (patch: SettingsPatch) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const enabled = PERMISSION_ITEMS.filter((i) => value.permissions[i.key]);
  const auto = value.auto_approve;

  const panel = useRef<HTMLDivElement>(null);
  useDismiss(open, useCallback(() => setOpen(false), []), root, panel);

  return (
    <div ref={root} className="shrink-0 self-center">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        title={auto ? "权限（自动批准已开启）" : "权限"}
        onClick={() => setOpen((o) => !o)}
        className={`glass flex h-9 items-center gap-1 rounded-full px-2.5 text-xs font-medium ${auto ? "ring-1 ring-[var(--warn)]" : ""}`}
      >
        {auto ? <ShieldOff className="size-4 text-[var(--warn)]" /> : <ShieldCheck className="size-4 text-[var(--accent)]" />}
        <span className="hidden items-center gap-0.5 sm:inline-flex">
          {enabled.length ? enabled.map(({ key, icon: Icon }) => <Icon key={key} className="ink-soft size-3.5" />) : "仅对话"}
        </span>
      </button>
      {open && (
        <MenuPanel anchor={root} panelRef={panel} width={320} className="space-y-1 p-2">
          <div className="ink-faint px-2 pt-0.5 pb-1 text-[11px] font-medium">Agent 可以使用</div>
          {PERMISSION_ITEMS.map(({ key, label, hint, icon: Icon }) => (
            <div key={key} className="flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-[var(--glass-fill)]">
              <Icon className="ink-soft size-4 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium">{label}</div>
                <div className="ink-soft text-[11px] leading-snug">{hint}</div>
              </div>
              <Toggle checked={value.permissions[key]} onChange={(v) => onChange({ permissions: { ...value.permissions, [key]: v } })} label="" />
            </div>
          ))}
          <div className="mt-1 flex items-center gap-3 border-t border-[var(--glass-edge-low)] px-2 pt-2.5 pb-1.5">
            <ShieldOff className={`size-4 shrink-0 ${auto ? "text-[var(--warn)]" : "ink-soft"}`} />
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-medium">自动批准</div>
              <div className="ink-soft text-[11px] leading-snug">写文件、运行命令、调用 MCP 工具时不再询问</div>
            </div>
            <Toggle checked={auto} onChange={(v) => onChange({ auto_approve: v })} label="" />
          </div>
        </MenuPanel>
      )}
    </div>
  );
}
