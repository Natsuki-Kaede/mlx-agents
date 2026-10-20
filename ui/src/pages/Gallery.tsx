import { motion } from "motion/react";
import { Copy, Download, RefreshCw, Repeat2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { FIELD_LABELS, api, type OutputMeta } from "../api";
import { Button, ErrorNote, Glass, spring } from "../components/ui";

export function GalleryPage({ active, onReuse }: { active: boolean; onReuse: (meta: OutputMeta) => void }) {
  const [items, setItems] = useState<OutputMeta[] | null>(null);
  const [open, setOpen] = useState<OutputMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api.gallery().then(setItems).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(() => {
    if (active) load(); // the page stays mounted, so refresh whenever it is shown
  }, [active, load]);

  const source = open?.source as { reference?: Record<string, string> | string; user_prompt?: string } | undefined;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="ink-soft text-sm">{items ? `${items.length} 张作品` : "加载中…"}</div>
        <Button icon={<RefreshCw className="size-4" />} onClick={load}>
          刷新
        </Button>
      </div>
      <ErrorNote error={error} />
      <div className="masonry">
        {items?.map((item, i) => (
          <motion.button
            key={item.file}
            type="button"
            initial={{ y: 16 }}
            animate={{ y: 0 }}
            transition={{ ...spring, delay: Math.min(i, 12) * 0.035 }}
            whileHover={{ y: -4 }}
            onClick={() => setOpen(item)}
            className="glass mb-4 block w-full break-inside-avoid overflow-hidden rounded-3xl p-1.5 text-left"
          >
            <img src={item.url} loading="lazy" className="w-full rounded-[20px]" alt="" />
            {item.prompt && <div className="ink-soft line-clamp-2 px-2 pt-2 pb-1 text-xs leading-relaxed">{item.prompt}</div>}
          </motion.button>
        ))}
      </div>

      {createPortal(
        open && (
          <div className="fixed inset-0 z-50 grid place-items-center bg-black/35 p-4 backdrop-blur-sm" onClick={() => setOpen(null)}>
            <Glass
              initial={{ y: 16, scale: 0.97 }}
              animate={{ y: 0, scale: 1 }}
              transition={spring}
              onClick={(e) => e.stopPropagation()}
              className="glass-solid grid max-h-[92dvh] w-full max-w-6xl gap-5 overflow-hidden p-4 md:grid-cols-[1.3fr_1fr]"
            >
              <img src={open.url} className="max-h-[84dvh] w-full rounded-2xl object-contain" alt="" />
              <div className="scroll-soft flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
                <div className="flex items-start justify-between gap-2">
                  <div className="ink-soft text-xs tabular-nums">
                    {open.created ?? open.file}
                    {open.width && ` · ${open.width}×${open.height} · ${open.steps} 步 · 种子 ${open.seed}`}
                    {open.seconds && ` · ${open.seconds} 秒`}
                    {open.init_image && ` · 图生图 参考强度 ${open.image_strength}`}
                  </div>
                  <button type="button" onClick={() => setOpen(null)} className="grid size-8 shrink-0 place-items-center rounded-full hover:bg-[var(--glass-fill)]">
                    <X className="size-4" />
                  </button>
                </div>
                {open.prompt && <p className="text-sm leading-relaxed whitespace-pre-wrap">{open.prompt}</p>}
                {source?.user_prompt && (
                  <div className="text-xs">
                    <div className="font-medium">复刻时你的要求</div>
                    <div className="ink-soft mt-1">{source.user_prompt}</div>
                  </div>
                )}
                {source?.reference && typeof source.reference === "object" && (
                  <div className="space-y-1.5 text-xs">
                    <div className="font-medium">反推的参考描述</div>
                    {Object.entries(source.reference).map(([k, v]) => (
                      <div key={k} className="ink-soft">
                        <span className="font-medium">{FIELD_LABELS[k] ?? k}：</span>
                        {v}
                      </div>
                    ))}
                  </div>
                )}
                <div className="mt-auto flex flex-wrap gap-2 pt-2">
                  {open.prompt && (
                    <Button variant="primary" icon={<Repeat2 className="size-4" />} onClick={() => onReuse(open)}>
                      用这组参数再生成
                    </Button>
                  )}
                  {open.prompt && (
                    <Button icon={<Copy className="size-4" />} onClick={() => navigator.clipboard.writeText(open.prompt ?? "")}>
                      复制提示词
                    </Button>
                  )}
                  <a href={open.url} download>
                    <Button icon={<Download className="size-4" />}>下载</Button>
                  </a>
                </div>
              </div>
            </Glass>
          </div>
        ),
        document.body,
      )}
    </div>
  );
}
