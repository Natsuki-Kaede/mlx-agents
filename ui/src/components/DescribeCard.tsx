import { ScanSearch, Wand2 } from "lucide-react";
import { FIELD_LABELS, type DescribeCardData } from "../api";
import { useChatActions } from "./chatActions";

/** A reverse-engineered prompt from the former "反推" mode, kept readable; its prompt can be handed to the agent. */
export function DescribeCard({ data }: { data: DescribeCardData }) {
  const { compose } = useChatActions();
  return (
    <div className="glass w-full space-y-2.5 rounded-3xl rounded-bl-lg px-4 py-3.5">
      <div className="flex items-center gap-2 text-xs font-medium">
        <ScanSearch className="size-4 text-[var(--accent)]" />
        反推提示词
      </div>
      {data.status === "error" && <div className="text-sm text-[var(--danger)]">{data.error}</div>}
      {data.status !== "done" && data.status !== "error" && <div className="ink-soft text-xs">这次反推没有完成。</div>}
      {data.fields && (
        <div className="ink-soft space-y-0.5 border-l-2 border-[var(--glass-edge)] pl-3 text-xs leading-relaxed">
          {Object.entries(data.fields).map(([k, v]) => (
            <div key={k}>
              <span className="font-medium">{FIELD_LABELS[k] ?? k}：</span>
              {v}
            </div>
          ))}
        </div>
      )}
      {data.prompt && (
        <>
          <div className="text-[13px] leading-relaxed">{data.prompt}</div>
          <button
            type="button"
            onClick={() => compose(`用这段提示词生成一张图，以附图为底图：\n${data.prompt}`, data.source.kind === "image" ? [data.source] : [])}
            className="accent-fill inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium"
          >
            <Wand2 className="size-3.5" /> 交给 agent 生图
          </button>
        </>
      )}
    </div>
  );
}
