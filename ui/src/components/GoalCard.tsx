import { CheckCircle2, ChevronDown, CircleHelp, Loader2, PauseCircle, Play, Square, Target, X } from "lucide-react";
import { useState } from "react";
import type { Goal } from "../api";

const STATUS: Record<Goal["status"], { label: string; tone: string; icon: typeof Target }> = {
  active: { label: "进行中", tone: "text-[var(--accent)]", icon: Loader2 },
  waiting: { label: "等你回复", tone: "text-[var(--warn)]", icon: CircleHelp },
  paused: { label: "已暂停", tone: "ink-soft", icon: PauseCircle },
  stalled: { label: "停滞", tone: "text-[var(--warn)]", icon: PauseCircle },
  done: { label: "已完成", tone: "text-[var(--ok)]", icon: CheckCircle2 },
};

function duration(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`;
  const m = Math.floor(seconds / 60);
  return m < 60 ? `${m} 分钟` : `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}

/** The chat's goal: what it is, where it stands, and controls to stop, continue or end it. */
export function GoalCard({
  goal,
  running,
  onStop,
  onContinue,
  onClear,
}: {
  goal: Goal;
  running: boolean;
  onStop: () => void;
  onContinue: () => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const status = running ? "active" : goal.status;
  const { label, tone, icon: Icon } = STATUS[status];
  const action = "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium";

  return (
    <div className="glass rounded-2xl px-3.5 py-2.5">
      <div className="flex items-center gap-2 text-xs">
        <Target className="size-4 shrink-0 text-[var(--accent)]" />
        <span className="font-medium">目标</span>
        <span className={`inline-flex items-center gap-1 ${tone}`}>
          <Icon className={`size-3.5 ${status === "active" ? "animate-spin" : ""}`} />
          {label}
        </span>
        {/* Rounds and time are added up when a run ends, so they are shown between runs. */}
        {!running && (
          <span className="ink-faint tabular-nums">
            {goal.rounds} 轮 · {duration(goal.seconds)}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          {running && (
            <button type="button" onClick={onStop} className={`${action} hover:bg-[var(--glass-fill-strong)]`}>
              <Square className="size-3" /> 停止
            </button>
          )}
          {!running && (status === "paused" || status === "stalled") && (
            <button type="button" onClick={onContinue} className={`${action} accent-fill`}>
              <Play className="size-3" /> 继续
            </button>
          )}
          {!running && (
            <button type="button" onClick={onClear} className={`${action} ink-soft hover:bg-[var(--glass-fill-strong)]`} title={status === "done" ? "清除目标" : "结束这个目标"}>
              <X className="size-3" /> {status === "done" ? "清除" : "结束目标"}
            </button>
          )}
          <button type="button" onClick={() => setOpen((o) => !o)} className="ink-soft grid size-6 place-items-center rounded-full hover:bg-[var(--glass-fill-strong)]" title="详情">
            <ChevronDown className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>
      <div className={`mt-1.5 text-[13px] leading-relaxed whitespace-pre-wrap ${open ? "" : "line-clamp-2"}`}>{goal.text}</div>
      {!running && goal.status === "waiting" && goal.question && (
        <div className="mt-2 rounded-xl bg-[color-mix(in_srgb,var(--warn)_12%,transparent)] px-3 py-2 text-[13px]">
          <span className="font-medium">Agent 在问：</span>
          {goal.question}
          <div className="ink-soft mt-0.5 text-[11px]">在下方回复后会继续推进目标。</div>
        </div>
      )}
      {!running && goal.note && goal.status !== "waiting" && <div className="ink-soft mt-1.5 text-xs">{goal.note}</div>}
      {goal.status === "done" && goal.summary && (
        <div className="mt-2 space-y-1 text-xs">
          <div>
            <span className="font-medium">结果：</span>
            {goal.summary}
          </div>
          {goal.verification && (
            <div className="ink-soft">
              <span className="font-medium">验证：</span>
              {goal.verification}
            </div>
          )}
        </div>
      )}
      {open && goal.progress && (
        <div className="ink-soft mt-2 border-l-2 border-[var(--glass-edge)] pl-3 text-xs leading-relaxed whitespace-pre-wrap">
          <div className="mb-0.5 font-medium">进度摘要</div>
          {goal.progress}
        </div>
      )}
    </div>
  );
}
