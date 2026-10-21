import { motion } from "motion/react";
import { ArrowDown, CircleHelp, ImageIcon, Loader2, MessageSquarePlus, MoreHorizontal, PauseCircle, ScanSearch, ShieldAlert, Target, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  api,
  runEvents,
  type ChatSummary,
  type ChatTurn,
  type Goal,
  type ModelList,
  type OutputMeta,
  type RunEvent,
  type RunInfo,
  type ToolEvent,
  type Upload,
} from "../api";
import { ChatActionsContext, type ChatActions } from "../components/chatActions";
import { Composer } from "../components/Composer";
import { DescribeCard } from "../components/DescribeCard";
import { Markdown, countChars } from "../components/Markdown";
import { PlanCard } from "../components/PlanCard";
import { ThinkingBlock } from "../components/ThinkingBlock";
import { ToolTimeline } from "../components/ToolTimeline";
import { usePersistentState } from "../components/usePersistentState";
import { useStickToBottom } from "../components/useStickToBottom";
import { Button, ErrorNote, Glass, spring } from "../components/ui";
import { GoalCard } from "../components/GoalCard";
import { COMMAND_PROMPTS, parseCommand } from "../components/slashCommands";
import { applySettings, updateSettings, useAppSettings } from "../settingsStore";

const ASPECTS: Record<string, number> = { "1:1": 1, "3:4": 3 / 4, "4:3": 4 / 3, "9:16": 9 / 16, "16:9": 16 / 9 };

const newId = () => crypto.randomUUID().replace(/-/g, "").slice(0, 16);
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function aspectOf(width?: number, height?: number) {
  if (!width || !height) return "1:1";
  const off = (k: string) => Math.abs(Math.log(ASPECTS[k] / (width / height)));
  return Object.keys(ASPECTS).reduce((best, k) => (off(k) < off(best) ? k : best));
}

/** A ready-to-send request that asks the agent to repeat an image with the same settings. */
function imageRequest(prompt: string, width?: number, height?: number, steps?: number, seed?: number | null) {
  const parts = [`画幅 ${aspectOf(width, height)}`, steps ? `${steps} 步` : "", seed != null ? `种子 ${seed}` : ""].filter(Boolean);
  return `按这个提示词重新生成一张图（${parts.join("，")}）：\n${prompt}`;
}

function when(ts: number) {
  const d = new Date(ts * 1000);
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

/** Fold one run event into the assistant turn being streamed. */
function applyEvent(turn: ChatTurn, ev: RunEvent): ChatTurn {
  switch (ev.type) {
    case "thinking":
      return { ...turn, thinking: (turn.thinking ?? "") + ev.text, tokens: ev.tokens };
    case "answer":
      return { ...turn, content: turn.content + ev.text, tokens: ev.tokens };
    case "tool": {
      const tools = turn.tools ?? [];
      const t = ev.tool;
      return { ...turn, tools: tools.some((x) => x.id === t.id) ? tools.map((x) => (x.id === t.id ? t : x)) : [...tools, t] };
    }
    case "plan":
      return { ...turn, plan: ev.plan };
    case "end":
      return { ...turn, stats: ev.stats };
    default:
      return turn;
  }
}

/** A chat's state in the sidebar: what its run is doing, or a goal that needs attention. */
function ChatStatus({ chat }: { chat: ChatSummary }) {
  if (chat.run === "queued") return <MoreHorizontal className="ink-soft size-3.5 shrink-0 animate-pulse" aria-label="排队中" />;
  if (chat.run === "approval") return <ShieldAlert className="size-3.5 shrink-0 text-[var(--warn)]" aria-label="等你批准" />;
  if (chat.run) return <Loader2 className="size-3.5 shrink-0 animate-spin text-[var(--accent)]" aria-label="生成中" />;
  if (chat.goal === "waiting") return <CircleHelp className="size-3.5 shrink-0 text-[var(--warn)]" aria-label="目标等你回复" />;
  if (chat.goal === "paused" || chat.goal === "stalled") return <PauseCircle className="ink-soft size-3.5 shrink-0" aria-label="目标已暂停" />;
  if (chat.goal === "active") return <Target className="size-3.5 shrink-0 text-[var(--accent)]" aria-label="目标" />;
  return null;
}

export function ChatPage({ prefill }: { prefill: OutputMeta | null }) {
  // Conversations, runs and settings live in the backend; only the open chat is remembered locally.
  const [ui, setUi] = usePersistentState("chat:ui", { chatId: null as string | null });
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Upload[]>([]);
  const [uploading, setUploading] = useState(false);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [goal, setGoal] = useState<Goal | null>(null);
  const settings = useAppSettings();
  const [showHistory, setShowHistory] = useState(false);
  const [models, setModels] = useState<ModelList | null>(null);
  const { ref: list, contentRef, atBottom, scrollToBottom } = useStickToBottom();
  const stream = useRef<AbortController | null>(null);
  const chatIdRef = useRef<string | null>(ui.chatId);
  const { chatId } = ui;
  const running = !!run && run.chat_id === chatId;

  const refreshChats = useCallback(() => api.chats().then(setChats).catch(() => undefined), []);
  const anyRunning = !!run || chats.some((c) => c.run);

  // While anything runs, keep the sidebar's status icons fresh (runs in other chats included).
  useEffect(() => {
    if (!anyRunning) return;
    const timer = setInterval(refreshChats, 2000);
    return () => clearInterval(timer);
  }, [anyRunning, refreshChats]);
  const refreshModels = useCallback(() => api.models().then(setModels).catch(() => undefined), []);

  /** Load a chat from the backend, which owns it; ignored if another chat was opened meanwhile. */
  const loadChat = useCallback(async (id: string) => {
    const chat = await api.chat(id);
    if (chatIdRef.current !== id) return;
    setTurns(chat.turns);
    setGoal(chat.goal ?? null);
  }, []);

  /** Follow a run: replay its events into a live assistant turn, then reload the chat it saved. */
  const follow = useCallback(
    async (info: RunInfo) => {
      stream.current?.abort();
      const controller = new AbortController();
      stream.current = controller;
      setRun(info);
      let live: ChatTurn = { role: "assistant", mode: "chat", content: "", tools: [], plan: [] };
      setTurns((ts) => [...ts, live]);
      try {
        for await (const ev of runEvents(info.id, 0, controller.signal)) {
          // Events already buffered when another chat was opened must not touch the new chat's turns.
          if (controller.signal.aborted) return;
          if (ev.type === "status") setRun((r) => r && { ...r, status: ev.status });
          if (ev.type === "status" && ev.note) setNotice(ev.note);
          if (ev.type === "auto_approve") applySettings({ auto_approve: ev.auto_approve });
          if (ev.type === "goal") setGoal(ev.goal);
          if (ev.type === "end" && ev.error) setError(ev.error);
          live = applyEvent(live, ev);
          setTurns((ts) => [...ts.slice(0, -1), live]);
        }
      } catch (e) {
        if (controller.signal.aborted) return; // another chat was opened; the run carries on in the backend
        setError(errorText(e));
      }
      if (stream.current !== controller) return;
      stream.current = null;
      setRun(null);
      await loadChat(info.chat_id).catch(() => undefined);
      refreshChats();
    },
    [loadChat, refreshChats],
  );

  const openChat = useCallback(
    async (id: string | null) => {
      stream.current?.abort();
      stream.current = null;
      chatIdRef.current = id;
      setRun(null);
      setError(null);
      setNotice(null);
      setUi((u) => ({ ...u, chatId: id }));
      setShowHistory(false);
      if (!id) {
        setTurns([]);
        setGoal(null);
        return;
      }
      try {
        await loadChat(id);
        requestAnimationFrame(() => scrollToBottom(false));
        const active = await api.chatRun(id);
        if (active && chatIdRef.current === id) follow(active);
      } catch (e) {
        chatIdRef.current = null;
        setTurns([]);
        setUi((u) => ({ ...u, chatId: null }));
        // A remembered chat that has since been deleted simply opens a new one.
        if (!errorText(e).startsWith("404")) setError(errorText(e));
      }
    },
    [setUi, scrollToBottom, loadChat, follow],
  );

  useEffect(() => {
    refreshChats();
    refreshModels();
    if (chatId) openChat(chatId);
    // Restore once on mount; later changes go through openChat.
  }, []);

  const attach = async (files: File[]) => {
    setUploading(true);
    setError(null);
    try {
      const uploaded = await Promise.all(files.map((f) => api.upload(f)));
      setAttachments((a) => [...a, ...uploaded]);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setUploading(false);
    }
  };

  /** Send the input, or `typed` (a command picked from the menu); slash commands are run here. */
  const send = (typed = input) => {
    const command = parseCommand(typed);
    if (!command) return sendMessage(typed.trim() || (attachments.length ? "请描述这些内容。" : ""), attachments, false);
    if (command.name === "goal") {
      if (!command.arg) return setError("用法：/goal 目标和完成标准，例如 /goal 给项目加上单元测试并全部跑通");
      return sendMessage(command.arg, attachments, true, typed);
    }
    if (command.name in COMMAND_PROMPTS) return sendMessage(COMMAND_PROMPTS[command.name], attachments, false, typed);
    setInput("");
    if (command.name === "new") return openChat(null);
    if (command.name === "auto") {
      const on = !settings?.auto_approve;
      updateSettings({ auto_approve: on })
        .then(() => setNotice(on ? "已开启自动批准：写文件、运行命令不再询问" : "已关闭自动批准"))
        .catch((e) => setError(errorText(e)));
    }
  };

  /** Start a run; `asGoal` makes the message the chat's new goal (an unfinished goal is continued anyway).
   * `typed` is what goes back into the input box if the run cannot start. */
  const sendMessage = async (text: string, files: Upload[], asGoal: boolean, typed = text) => {
    const id = chatIdRef.current ?? newId();
    if (!chatIdRef.current) {
      chatIdRef.current = id;
      setUi((u) => ({ ...u, chatId: id }));
    }
    setInput("");
    setAttachments([]);
    setError(null);
    setNotice(null);
    const before = turns;
    setTurns([...before, { role: "user", mode: asGoal ? "goal" : "chat", content: text, attachments: files }]);
    scrollToBottom();
    try {
      const info = await api.startRun({ chat_id: id, content: text, attachments: files.map((a) => a.id), goal: asGoal });
      follow(info);
      refreshChats();
    } catch (e) {
      setTurns(before);
      setInput(typed);
      setAttachments(files);
      setError(errorText(e));
    }
  };

  const actions: ChatActions = {
    useAsBase: (file) => {
      api
        .uploadFromOutput(file)
        .then((up) => setAttachments((a) => [...a, up]))
        .catch((e) => setError(errorText(e)));
    },
    reuse: (tool: ToolEvent) => setInput(imageRequest(String(tool.args.prompt ?? ""), tool.width, tool.height, tool.steps, tool.seed)),
    compose: (text, files) => {
      setInput(text);
      setAttachments(files ?? []);
    },
    approve: (toolId, decision) => {
      if (run) api.approve(run.id, toolId, decision).catch((e) => setError(errorText(e)));
    },
  };

  // "用这组参数再生成" in the gallery arrives as a ready-to-send request, with its base image attached.
  useEffect(() => {
    if (!prefill) return;
    setInput(imageRequest(prefill.prompt ?? "", prefill.width, prefill.height, prefill.steps, prefill.seed));
    setAttachments(prefill.init_image ? [{ id: prefill.init_image, kind: "image", url: `/media/uploads/${prefill.init_image}` }] : []);
  }, [prefill]);

  const remove = async (id: string) => {
    if (!window.confirm("删除这段对话？删除后无法恢复。")) return;
    try {
      await api.deleteChat(id);
      if (id === chatId) openChat(null);
      refreshChats();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const sidebar = (
    <Glass className="flex h-full flex-col gap-2 p-3">
      <Button variant="primary" className="w-full" icon={<MessageSquarePlus className="size-4" />} onClick={() => openChat(null)}>
        新对话
      </Button>
      <div className="scroll-soft -mx-1 flex-1 space-y-1 overflow-y-auto px-1">
        {chats.length === 0 && <div className="ink-faint px-2 py-6 text-center text-xs">还没有历史对话</div>}
        {chats.map((c) => (
          <div key={c.id} className="group relative">
            <button
              type="button"
              onClick={() => openChat(c.id)}
              className={`w-full rounded-2xl px-3 py-2.5 text-left transition-colors ${c.id === chatId ? "glass glass-strong" : "hover:bg-[var(--glass-fill)]"}`}
            >
              <div className="flex items-center gap-1.5 pr-6 text-[13px] font-medium">
                <ChatStatus chat={c} />
                <span className="truncate">{c.title}</span>
              </div>
              <div className="ink-faint mt-0.5 text-[11px] tabular-nums">
                {when(c.updated)} · {Math.ceil(c.turns / 2)} 轮
              </div>
            </button>
            <button
              type="button"
              title="删除"
              disabled={running && c.id === chatId}
              onClick={() => remove(c.id)}
              className="ink-soft absolute top-2.5 right-2 grid size-7 place-items-center rounded-full opacity-0 transition-opacity group-hover:opacity-100 hover:bg-[var(--glass-fill-strong)] focus:opacity-100"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
    </Glass>
  );

  return (
    <ChatActionsContext.Provider value={actions}>
      <div className="grid h-[calc(100dvh-90px)] gap-4 md:grid-cols-[264px_minmax(0,1fr)]">
        <div className="hidden min-h-0 md:block">{sidebar}</div>
        {createPortal(
          showHistory && (
            <div className="fixed inset-0 z-50 bg-black/30 p-4 backdrop-blur-sm md:hidden" onClick={() => setShowHistory(false)}>
              <motion.div initial={{ x: -24 }} animate={{ x: 0 }} className="h-full max-w-xs" onClick={(e) => e.stopPropagation()}>
                {sidebar}
              </motion.div>
            </div>
          ),
          document.body,
        )}

        <div className="flex min-h-0 min-w-0 flex-col gap-4">
          {goal && chatId && (
            <GoalCard
              goal={goal}
              running={running}
              onStop={() => run && api.cancelRun(run.id).catch((e) => setError(errorText(e)))}
              onContinue={() => sendMessage("继续推进目标。", [], false)}
              onClear={() =>
                api
                  .clearGoal(chatId)
                  .then(() => {
                    setGoal(null);
                    refreshChats();
                  })
                  .catch((e) => setError(errorText(e)))
              }
            />
          )}
          <div className="relative min-h-0 flex-1">
            <div ref={list} className="scroll-soft h-full overflow-y-auto px-1">
              <div ref={contentRef} className="min-h-full space-y-4 pb-2">
                {turns.length === 0 && (
                  <motion.div initial={{ y: 10 }} animate={{ y: 0 }} className="grid min-h-[60dvh] place-items-center">
                    <div className="max-w-md text-center">
                      <div className="bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] bg-clip-text text-3xl font-semibold text-transparent">
                        今天要做点什么？
                      </div>
                      <div className="ink-soft mt-2 text-sm leading-relaxed">
                        直接说需求：问问题、查资料、写代码、处理文件、画图。多步任务会先列计划再逐步执行；写文件、运行命令前会先请你批准。
                      </div>
                    </div>
                  </motion.div>
                )}
                {turns.map((turn, i) => {
                  const live = running && i === turns.length - 1 && turn.role === "assistant";
                  const idle = live && !turn.content && !turn.thinking && !turn.tools?.length && !turn.plan?.length;
                  return (
                    <motion.div
                      key={`${chatId}-${i}`}
                      initial={{ y: 14, scale: 0.98 }}
                      animate={{ y: 0, scale: 1 }}
                      transition={spring}
                      className={`flex ${turn.role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      <div className={`max-w-[85%] ${turn.role === "user" ? "items-end" : "w-full items-start"} flex flex-col gap-2`}>
                        {turn.role === "user" && turn.mode && turn.mode !== "chat" && (
                          <span className="ink-soft inline-flex items-center gap-1 text-[11px]">
                            {turn.mode === "goal" ? <Target className="size-3" /> : turn.mode === "image" ? <ImageIcon className="size-3" /> : <ScanSearch className="size-3" />}
                            {turn.mode === "goal" ? "目标" : turn.mode === "image" ? "生图" : "反推"}
                          </span>
                        )}
                        {!!turn.attachments?.length && (
                          <div className="flex flex-wrap justify-end gap-2">
                            {turn.attachments.map((a) =>
                              a.kind === "video" ? (
                                <video key={a.id} src={a.url} className="h-36 rounded-2xl" controls muted />
                              ) : (
                                <img key={a.id} src={a.url} className="h-36 rounded-2xl object-cover" alt="" />
                              ),
                            )}
                          </div>
                        )}
                        {turn.role === "user" ? (
                          !!turn.content && (
                            <div className="accent-fill rounded-3xl rounded-br-lg px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap">{turn.content}</div>
                          )
                        ) : turn.describe ? (
                          <DescribeCard data={turn.describe} />
                        ) : (
                          <>
                            {!!turn.plan?.length && <PlanCard plan={turn.plan} live={live} />}
                            {!!turn.thinking && (
                              <ThinkingBlock text={turn.thinking} tokens={turn.tokens?.thinking} active={live && !turn.content && !turn.tools?.length} />
                            )}
                            {!!turn.tools?.length && <ToolTimeline tools={turn.tools} />}
                            {(turn.content || idle) && (
                              <div className="glass rounded-3xl rounded-bl-lg px-4 py-3 text-[15px]">
                                {turn.content ? (
                                  <Markdown text={turn.content} streaming={live} />
                                ) : (
                                  <span className="shimmer-text">{run?.status === "queued" ? "排队中…" : "正在思考…"}</span>
                                )}
                              </div>
                            )}
                          </>
                        )}
                        {turn.role === "assistant" && !turn.describe && turn.mode !== "image" && (turn.content || turn.stats) && (
                          <div className="ink-faint flex flex-wrap gap-x-1.5 px-2 text-[11px] tabular-nums">
                            {live && <span className="shimmer-text font-medium">执行中</span>}
                            <span>
                              {turn.tokens ? `${turn.tokens.answer} tokens · ` : ""}
                              {countChars(turn.content)} 字
                            </span>
                            {turn.stats && (
                              <span>
                                · {turn.stats.seconds} 秒（含加载 {turn.stats.load_seconds} 秒）
                                {turn.stats.generation_tps ? ` · ${turn.stats.generation_tps} tok/s` : ""} · 峰值 {turn.stats.peak_mlx_gb} GB
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </motion.div>
                  );
                })}
              </div>
            </div>
            {!atBottom && turns.length > 0 && (
              <motion.button
                type="button"
                initial={{ y: 8, scale: 0.9 }}
                animate={{ y: 0, scale: 1 }}
                transition={spring}
                onClick={() => scrollToBottom()}
                className="glass glass-strong absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full px-3.5 py-2 text-xs font-medium"
              >
                <ArrowDown className="size-3.5" />
                {running ? "跟随最新进展" : "回到底部"}
              </motion.button>
            )}
          </div>

          <ErrorNote error={error} />
          {notice && <div className="ink-soft glass rounded-2xl px-4 py-2.5 text-xs">{notice}</div>}

          <Composer
            input={input}
            onInput={setInput}
            attachments={attachments}
            onRemoveAttachment={(id) => setAttachments((l) => l.filter((x) => x.id !== id))}
            uploading={uploading}
            onAttachFiles={attach}
            running={running}
            onSend={send}
            onStop={() => run && api.cancelRun(run.id).catch((e) => setError(errorText(e)))}
            onShowHistory={() => setShowHistory(true)}
            onError={setError}
            models={models?.models ?? []}
            defaultModel={models?.default_chat ?? null}
            onRefreshModels={refreshModels}
            goalOpen={!!goal && goal.status !== "done"}
          />
        </div>
      </div>
    </ChatActionsContext.Provider>
  );
}
