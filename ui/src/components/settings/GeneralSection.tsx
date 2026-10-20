import { FolderOpen } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type AppSettings } from "../../api";
import { Button, Label } from "../ui";

/** The agent's workspace folder. */
export function GeneralSection({ onError }: { onError: (e: string | null) => void }) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [workspace, setWorkspace] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setSettings(s);
        setWorkspace(s.workspace);
      })
      .catch((e) => onError(String(e)));
  }, [onError]);

  const save = async () => {
    onError(null);
    try {
      const s = await api.saveSettings({ workspace });
      setSettings(s);
      setWorkspace(s.workspace);
      setSaved(true);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <section className="space-y-2">
      <Label>
        <span className="inline-flex items-center gap-1.5">
          <FolderOpen className="size-4" /> 工作目录
        </span>
      </Label>
      <div className="flex gap-2">
        <input className="field font-mono text-[13px]" value={workspace} onChange={(e) => setWorkspace(e.target.value)} placeholder="~/MLX Agents" />
        <Button variant="primary" className="shrink-0" onClick={save}>
          保存
        </Button>
      </div>
      <div className="ink-faint text-xs leading-relaxed">
        Agent 的文件工具、命令和 MCP 服务都在这个目录里工作。{settings && `当前：${settings.workspace_resolved}`}
        {saved && " · 已保存"}
      </div>
    </section>
  );
}
