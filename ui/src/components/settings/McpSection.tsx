import { CircleAlert, CircleCheck, CircleDashed, CircleOff, Loader2, Plug, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type McpServerStatus, type McpState } from "../../api";
import { Button, Label } from "../ui";

const EXAMPLE = `{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents"]
    },
    "remote-example": {
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer …" },
      "disabled": true
    }
  }
}`;

const STATE: Record<McpServerStatus["state"], { label: string; icon: typeof Plug; tone: string }> = {
  idle: { label: "未连接（首次使用时连接）", icon: CircleDashed, tone: "ink-soft" },
  connecting: { label: "连接中", icon: Loader2, tone: "text-[var(--accent)]" },
  connected: { label: "已连接", icon: CircleCheck, tone: "text-[var(--ok)]" },
  error: { label: "连接失败", icon: CircleAlert, tone: "text-[var(--danger)]" },
  stopped: { label: "已停止", icon: CircleDashed, tone: "ink-soft" },
  disabled: { label: "已停用", icon: CircleOff, tone: "ink-faint" },
};

/** MCP servers in the common {"mcpServers": …} JSON, with each server's connection state and tools. */
export function McpSection({ onError }: { onError: (e: string | null) => void }) {
  const [state, setState] = useState<McpState | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<"save" | "connect" | null>(null);

  const apply = useCallback((s: McpState) => {
    setState(s);
    setText(JSON.stringify(s.config, null, 2));
  }, []);

  useEffect(() => {
    api.mcp().then(apply).catch((e) => onError(String(e)));
  }, [apply, onError]);

  const run = async (kind: "save" | "connect") => {
    onError(null);
    setBusy(kind);
    try {
      if (kind === "save") {
        let config: unknown;
        try {
          config = JSON.parse(text);
        } catch (e) {
          throw new Error(`JSON 格式有误：${e instanceof Error ? e.message : e}`);
        }
        await api.saveMcp(config);
      }
      apply(await api.connectMcp());
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="ink-faint text-xs leading-relaxed">
        接入 Model Context Protocol 服务，给 agent 增加工具。格式与 Claude Desktop、Cursor 的配置相同：本地服务写 command / args / env（在工作目录里启动），远程服务写 url / headers；加 "disabled": true
        可暂时停用。对话里打开「MCP」开关后，agent 才能调用这些工具；没开自动批准时，每次调用前会请你批准。
      </div>
      <section className="space-y-2">
        <Label>
          <span className="inline-flex items-center gap-1.5">
            <Plug className="size-4" /> 服务配置
          </span>
        </Label>
        <textarea
          className="field min-h-56 font-mono text-[12.5px] leading-relaxed"
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={EXAMPLE}
        />
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" loading={busy === "save"} disabled={!!busy} onClick={() => run("save")}>
            保存并连接
          </Button>
          <Button icon={<RefreshCw className="size-4" />} loading={busy === "connect"} disabled={!!busy} onClick={() => run("connect")}>
            测试连接
          </Button>
          {!Object.keys(state?.config.mcpServers ?? {}).length && (
            <Button variant="ghost" onClick={() => setText(EXAMPLE)}>
              填入示例
            </Button>
          )}
        </div>
      </section>
      {!!state?.status.length && (
        <section className="space-y-2">
          <Label>状态</Label>
          {state.status.map((s) => {
            const { label, icon: Icon, tone } = STATE[s.state];
            return (
              <div key={s.name} className="glass space-y-1.5 rounded-xl px-3 py-2.5">
                <div className="flex items-center gap-2 text-[13px]">
                  <Icon className={`size-4 shrink-0 ${tone} ${s.state === "connecting" ? "animate-spin" : ""}`} />
                  <span className="font-medium">{s.name}</span>
                  <span className={`text-xs ${tone}`}>{label}</span>
                  {!!s.tools.length && <span className="ink-faint ml-auto text-xs">{s.tools.length} 个工具</span>}
                </div>
                {s.error && <div className="text-xs break-all text-[var(--danger)]">{s.error}</div>}
                {!!s.tools.length && (
                  <div className="flex flex-wrap gap-1">
                    {s.tools.map((t) => (
                      <span key={t} className="ink-soft rounded-full bg-[var(--glass-fill-strong)] px-2 py-0.5 font-mono text-[11px]">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </section>
      )}
    </div>
  );
}
