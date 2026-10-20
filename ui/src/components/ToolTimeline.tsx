import { AnimatePresence, motion } from "motion/react";
import {
  AlertCircle,
  Ban,
  Bot,
  Brain,
  Check,
  ChevronDown,
  Download,
  FilePen,
  FilePlus,
  FileSearch,
  FileText,
  FolderOpen,
  FolderSearch,
  Globe,
  ImageIcon,
  ImagePlus,
  Images,
  Loader2,
  MessagesSquare,
  Repeat2,
  Search,
  ShieldAlert,
  Terminal,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { watchJob, type Job, type ToolEvent } from "../api";
import { useChatActions } from "./chatActions";
import { Markdown } from "./Markdown";
import { spring } from "./ui";

const host = (url?: string) => {
  try {
    return url ? new URL(url).hostname.replace(/^www\./, "") : "";
  } catch {
    return url ?? "";
  }
};
const clip = (s: unknown, n = 60) => {
  const text = String(s ?? "").replace(/\s+/g, " ");
  return text.length > n ? `${text.slice(0, n)}…` : text;
};

/** Live progress of the image job behind a generate_image call (the job runs inside the agent's task). */
function useJobProgress(jobId: string | undefined, running: boolean) {
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => {
    if (!jobId || !running) return;
    const controller = new AbortController();
    (async () => {
      try {
        for await (const state of watchJob(jobId, controller.signal)) setJob(state);
      } catch {
        /* stream closed: the tool event carries the final state */
      }
    })();
    return () => controller.abort();
  }, [jobId, running]);
  return job;
}

function StatusIcon({ status }: { status: ToolEvent["status"] }) {
  if (status === "running") return <Loader2 className="size-3.5 animate-spin text-[var(--accent)]" />;
  if (status === "awaiting_approval") return <ShieldAlert className="size-3.5 text-[var(--warn)]" />;
  if (status === "denied") return <Ban className="ink-soft size-3.5" />;
  if (status === "error") return <AlertCircle className="size-3.5 text-[var(--danger)]" />;
  return <Check className="size-3.5 text-[var(--ok)]" />;
}

const Pre = ({ children }: { children: ReactNode }) => (
  <pre className="scroll-soft mt-2 max-h-64 overflow-auto rounded-xl bg-[color-mix(in_srgb,var(--bg)_70%,transparent)] p-2.5 text-[11.5px] leading-relaxed whitespace-pre-wrap">
    {children}
  </pre>
);

/** Icon, title, one-line detail and optional expandable body for each tool. */
function describe(tool: ToolEvent, job: Job | null): { icon: typeof Search; title: string; detail: string; body?: ReactNode } {
  const a = tool.args;
  switch (tool.name) {
    case "web_search":
      return {
        icon: Search,
        title: `搜索「${clip(a.query, 40)}」`,
        detail: tool.results ? `${tool.results.length} 条结果` : "",
        body: tool.results?.length ? (
          <ol className="mt-2 space-y-1.5 border-l-2 border-[var(--glass-edge)] pl-3 text-xs">
            {tool.results.map((r, i) => (
              <li key={r.url + i}>
                <a href={r.url} target="_blank" rel="noreferrer" className="font-medium hover:underline">
                  {r.title || r.url}
                </a>
                <span className="ink-faint ml-1.5">{host(r.url)}</span>
              </li>
            ))}
          </ol>
        ) : undefined,
      };
    case "read_page":
      return {
        icon: Globe,
        title: `阅读 ${tool.title || host(String(a.url ?? tool.url ?? ""))}`,
        detail: tool.chars !== undefined ? `${host(tool.url)} · ${tool.chars} 字${tool.truncated ? "（已截断）" : ""}` : "",
      };
    case "generate_image":
      return {
        icon: ImageIcon,
        title: tool.status === "running" ? "正在生成图片" : "生成图片",
        detail:
          tool.status === "running"
            ? job && job.total > 0
              ? `${job.phase} ${job.step}/${job.total} 步 · ${Math.round(job.elapsed)} 秒`
              : job?.phase ?? "排队中"
            : tool.width
              ? `${tool.width}×${tool.height} · ${tool.steps} 步 · 种子 ${tool.seed} · ${tool.seconds} 秒${tool.reference ? ` · 图生图 强度 ${tool.strength}` : ""}`
              : "",
        body: (
          <div className="ink-soft mt-2 border-l-2 border-[var(--glass-edge)] pl-3 text-xs leading-relaxed">
            {String(a.prompt ?? "")}
            {a.aspect_ratio ? ` · ${String(a.aspect_ratio)}` : ""}
          </div>
        ),
      };
    case "list_dir":
      return { icon: FolderOpen, title: `列出 ${tool.path ?? a.path ?? "."}`, detail: tool.count !== undefined ? `${tool.count} 项` : "" };
    case "find_files":
      return {
        icon: FolderSearch,
        title: `查找文件 ${clip(a.pattern, 40)}`,
        detail: tool.count !== undefined ? `${tool.count} 个` : "",
        body: tool.output !== undefined ? <Pre>{tool.output}</Pre> : undefined,
      };
    case "search_files":
      return {
        icon: FileSearch,
        title: `搜索内容「${clip(a.query, 36)}」${a.glob ? ` · ${String(a.glob)}` : ""}`,
        detail: tool.count !== undefined ? `${tool.count} 处 · ${tool.files ?? 0} 个文件` : "",
        body: tool.output !== undefined ? <Pre>{tool.output}</Pre> : undefined,
      };
    case "read_file":
      return { icon: FileText, title: `读取 ${tool.path ?? a.path}`, detail: tool.lines !== undefined ? `${tool.lines} 行` : "" };
    case "write_file":
      return {
        icon: FilePlus,
        title: `写入 ${a.path}`,
        detail: `${String(a.content ?? "").length} 字符`,
        body: <Pre>{clip(a.content, 4000)}</Pre>,
      };
    case "edit_file":
      return {
        icon: FilePen,
        title: `修改 ${a.path}`,
        detail: a.replace_all ? "全部替换" : "",
        body: (
          <Pre>
            <span className="text-[var(--danger)]">- {String(a.old_text ?? "")}</span>
            {"\n"}
            <span className="text-[var(--ok)]">+ {String(a.new_text ?? "")}</span>
          </Pre>
        ),
      };
    case "run_command":
      return {
        icon: Terminal,
        title: `运行 ${clip(a.command, 50)}`,
        detail: tool.stopped ? tool.stopped : tool.exit_code !== undefined && tool.exit_code !== null ? `退出码 ${tool.exit_code} · ${tool.seconds} 秒` : "",
        body: (
          <Pre>
            <span className="ink-soft">$ {String(a.command ?? "")}</span>
            {tool.output !== undefined ? `\n${tool.output || "（无输出）"}` : ""}
          </Pre>
        ),
      };
    case "delegate_task":
      return {
        icon: Bot,
        title: `${tool.agent ? `${tool.agent.icon} ${tool.agent.name}` : "子 agent"}：${clip(a.task, 48)}`,
        detail: tool.status === "running" ? "子 agent 工作中" : "",
        body: (
          <div className="ink-soft mt-2 border-l-2 border-[var(--glass-edge)] pl-3 text-xs leading-relaxed whitespace-pre-wrap">
            {String(a.task ?? "")}
            {a.context ? `\n\n背景：${String(a.context)}` : ""}
          </div>
        ),
      };
    case "remember":
      return { icon: Brain, title: `记住：${clip(a.text, 50)}`, detail: "" };
    case "forget":
      return { icon: Brain, title: `删除记忆 ${String(a.id ?? "")}`, detail: "" };
    case "search_gallery":
    case "search_chats": {
      const gallery = tool.name === "search_gallery";
      return {
        icon: gallery ? Images : MessagesSquare,
        title: `搜索${gallery ? "作品库" : "历史对话"}「${clip(a.query, 30)}」`,
        detail: tool.results ? `${tool.results.length} 条` : "",
        body: tool.results?.length ? (
          <ol className="ink-soft mt-2 space-y-1 border-l-2 border-[var(--glass-edge)] pl-3 text-xs">
            {tool.results.map((r, i) => (
              <li key={i}>
                <span className="font-medium text-[var(--ink)]">{r.title}</span> {clip(r.snippet, 80)}
              </li>
            ))}
          </ol>
        ) : undefined,
      };
    }
    default:
      return { icon: Check, title: tool.name, detail: "" };
  }
}

function ImageActions({ tool }: { tool: ToolEvent }) {
  const { useAsBase, reuse } = useChatActions();
  const action = "ink-soft inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium hover:bg-[var(--glass-fill-strong)]";
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {tool.file && (
        <button type="button" className={action} onClick={() => useAsBase(tool.file!)}>
          <ImagePlus className="size-3.5" /> 用作底图
        </button>
      )}
      <button type="button" className={action} onClick={() => reuse(tool)}>
        <Repeat2 className="size-3.5" /> 复用参数
      </button>
      {tool.image && (
        <a href={tool.image} download className={action}>
          <Download className="size-3.5" /> 下载
        </a>
      )}
    </div>
  );
}

function Approval({ tool }: { tool: ToolEvent }) {
  const { approve } = useChatActions();
  const [sent, setSent] = useState(false);
  const decide = (d: "allow" | "always" | "deny") => {
    setSent(true);
    approve(tool.id, d);
  };
  const btn = "rounded-full px-3 py-1.5 text-xs font-medium transition-opacity disabled:opacity-50";
  return (
    <div className="mt-2 space-y-2">
      <div className="text-[13px] font-medium">{tool.approval?.summary}</div>
      {tool.approval?.detail && <Pre>{tool.approval.detail}</Pre>}
      <div className="flex flex-wrap gap-1.5">
        <button type="button" disabled={sent} onClick={() => decide("allow")} className={`accent-fill ${btn}`}>
          允许
        </button>
        <button type="button" disabled={sent} onClick={() => decide("always")} className={`glass ${btn}`}>
          本对话总是允许
        </button>
        <button type="button" disabled={sent} onClick={() => decide("deny")} className={`${btn} text-[var(--danger)] hover:bg-[var(--glass-fill)]`}>
          拒绝
        </button>
      </div>
    </div>
  );
}

function ToolCard({ tool, all }: { tool: ToolEvent; all: ToolEvent[] }) {
  const waiting = tool.status === "awaiting_approval";
  const [open, setOpen] = useState(false);
  const job = useJobProgress(tool.job, tool.status === "running");
  const { icon: Icon, title, detail, body } = describe(tool, job);
  const note =
    tool.status === "error"
      ? tool.error
      : tool.status === "denied"
        ? { deny: "已拒绝", cancelled: "任务已停止", timeout: "等待审批超时" }[tool.decision ?? "deny"] ?? "未执行"
        : detail;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={spring}
      className={`glass rounded-2xl px-3.5 py-2.5 ${waiting ? "ring-2 ring-[color-mix(in_srgb,var(--warn)_55%,transparent)]" : ""}`}
    >
      <button type="button" disabled={!body} onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 text-left text-xs">
        <Icon className="ink-soft size-3.5 shrink-0" />
        <span className="min-w-0 truncate font-medium">{title}</span>
        <span className="ink-faint min-w-0 flex-1 truncate tabular-nums">{waiting ? "等待你批准" : note}</span>
        <StatusIcon status={tool.status} />
        {body && (
          <motion.span animate={{ rotate: open ? 180 : 0 }} transition={spring}>
            <ChevronDown className="ink-soft size-3.5" />
          </motion.span>
        )}
      </button>
      {waiting && <Approval tool={tool} />}
      {tool.name === "generate_image" && tool.image && (
        <a href={tool.image} target="_blank" rel="noreferrer" className="mt-2 block">
          <motion.img
            initial={{ opacity: 0, filter: "blur(10px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            src={tool.image}
            className="max-h-80 rounded-xl"
            alt={String(tool.args.prompt ?? "")}
          />
        </a>
      )}
      {tool.name === "generate_image" && tool.status === "running" && job && job.total > 0 && (
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--glass-fill-strong)]">
          <div className="accent-fill h-full transition-[width] duration-500" style={{ width: `${(job.step / job.total) * 100}%` }} />
        </div>
      )}
      {tool.name === "generate_image" && tool.status === "done" && <ImageActions tool={tool} />}
      {tool.name === "delegate_task" && <SubAgent tool={tool} all={all} />}
      <AnimatePresence initial={false}>
        {open && body && !waiting && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            {body}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/** A sub-agent's own tool calls, nested under its card, and its report. */
function SubAgent({ tool, all }: { tool: ToolEvent; all: ToolEvent[] }) {
  const children = all.filter((t) => t.parent === tool.id);
  return (
    <div className="mt-2 space-y-1.5 border-l-2 border-[var(--glass-edge)] pl-2.5">
      {children.map((t) => (
        <ToolCard key={t.id} tool={t} all={all} />
      ))}
      {!!tool.output && (
        <div className="rounded-xl bg-[color-mix(in_srgb,var(--bg)_55%,transparent)] px-3 py-2 text-[13px]">
          <Markdown text={tool.output} streaming={tool.status === "running"} />
        </div>
      )}
    </div>
  );
}

/** The tool calls made while answering, in order (plan updates show as the plan card instead; a
 * sub-agent's calls show inside its delegate_task card). */
export function ToolTimeline({ tools }: { tools: ToolEvent[] }) {
  const shown = tools.filter((t) => !t.parent && (t.name !== "update_plan" || t.status === "error"));
  if (!shown.length) return null;
  return (
    <div className="flex w-full flex-col gap-1.5">
      {shown.map((t) => (
        <ToolCard key={t.id} tool={t} all={tools} />
      ))}
    </div>
  );
}
