import { Download, Eye, ImageIcon, MessageSquareText } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type ModelInfo, type ModelList } from "../../api";
import { modelMeta } from "../ModelMenu";
import { Button, Label } from "../ui";

const gb = (bytes: number) => (bytes / 1e9).toFixed(1);

function ModelRow({ model, isDefault, onDefault }: { model: ModelInfo; isDefault: boolean; onDefault: () => void }) {
  const Icon = model.kind === "image" ? ImageIcon : model.vision ? Eye : MessageSquareText;
  return (
    <div className={`glass flex items-center gap-3 rounded-xl px-3 py-2.5 ${model.supported ? "" : "opacity-60"}`}>
      <Icon className="ink-soft size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium">{model.id}</div>
        <div className="ink-soft truncate text-[11px]">{model.supported ? `${model.kind === "image" ? `${model.size_gb} GB` : modelMeta(model)} · ${model.runtime}` : model.note}</div>
      </div>
      {isDefault ? (
        <span className="accent-fill shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium">默认</span>
      ) : (
        model.supported && (
          <button type="button" onClick={onDefault} className="ink-soft shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium hover:bg-[var(--glass-fill-strong)]">
            设为默认
          </button>
        )
      )}
    </div>
  );
}

/** Models in models/: the defaults for chat and image generation, and downloads from Hugging Face. */
export function ModelsSection({ onError }: { onError: (e: string | null) => void }) {
  const [list, setList] = useState<ModelList | null>(null);
  const [repo, setRepo] = useState("");
  const refresh = useCallback(() => api.models().then(setList).catch((e) => onError(String(e))), [onError]);
  const downloading = list?.downloads.some((d) => d.status === "running");

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while a download is running, so its progress and the new model show up.
  useEffect(() => {
    if (!downloading) return;
    const timer = setInterval(refresh, 1500);
    return () => clearInterval(timer);
  }, [downloading, refresh]);

  const run = async (fn: () => Promise<unknown>) => {
    onError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const group = (kind: "chat" | "image") => list?.models.filter((m) => m.kind === kind) ?? [];

  return (
    <div className="space-y-5">
      {(["chat", "image"] as const).map((kind) => (
        <section key={kind} className="space-y-2">
          <Label hint={`${group(kind).length} 个`}>{kind === "chat" ? "对话模型" : "生图模型"}</Label>
          {group(kind).map((m) => (
            <ModelRow
              key={m.id}
              model={m}
              isDefault={m.id === (kind === "chat" ? list?.default_chat : list?.default_image)}
              onDefault={() => run(() => api.saveSettings(kind === "chat" ? { chat_model: m.id } : { image_model: m.id }))}
            />
          ))}
          {!group(kind).length && <div className="ink-faint py-2 text-center text-xs">models/ 里还没有{kind === "chat" ? "对话" : "生图"}模型</div>}
        </section>
      ))}

      <section className="space-y-2">
        <Label>
          <span className="inline-flex items-center gap-1.5">
            <Download className="size-4" /> 从 Hugging Face 下载
          </span>
        </Label>
        <div className="flex gap-2">
          <input className="field font-mono text-[13px]" value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="mlx-community/Qwen3-8B-4bit" />
          <Button variant="primary" className="shrink-0" disabled={!repo.trim()} onClick={() => run(async () => {
            await api.downloadModel(repo.trim());
            setRepo("");
          })}>
            下载
          </Button>
        </div>
        <div className="ink-faint text-xs leading-relaxed">
          需要 MLX 格式的仓库（如 mlx-community 下的模型）：纯文本模型用 mlx-lm 运行，带视觉的用 mlx-vlm。下载到项目的 models/ 文件夹，16 GB 内存建议选 4 bit、12B 以内的模型。传输卡住会自动续传；没下完的模型再点一次下载即可接着下。
        </div>
        {list?.downloads.map((d) => (
          <div key={d.id} className="glass space-y-1.5 rounded-xl px-3 py-2.5 text-xs">
            <div className="flex justify-between gap-2">
              <span className="truncate font-medium">{d.repo}</span>
              <span className="ink-soft shrink-0 tabular-nums">
                {d.status === "running" ? `${gb(d.done)} / ${d.total ? gb(d.total) : "?"} GB` : d.status === "done" ? "已完成" : "失败"}
              </span>
            </div>
            {d.status === "running" && (
              <div className="h-1.5 overflow-hidden rounded-full bg-[var(--glass-fill-strong)]">
                <div className="accent-fill h-full transition-[width] duration-500" style={{ width: `${d.total ? (d.done / d.total) * 100 : 5}%` }} />
              </div>
            )}
            {d.status === "running" && d.note && <div className="ink-soft">{d.note}</div>}
            {d.error && <div className="text-[var(--danger)]">{d.error}</div>}
          </div>
        ))}
      </section>
    </div>
  );
}
