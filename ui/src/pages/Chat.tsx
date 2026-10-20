import { AnimatePresence, motion } from "motion/react";
import { ArrowDown, ImageIcon, MessageSquarePlus, ScanSearch, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AGENTS_CHANGED,
  DEFAULT_AGENT,
  api,
  runEvents,
  type AgentProfile,
  type ChatSettings,
  type ChatSummary,
  type ChatTurn,
  type ModelList,
  type OutputMeta,
  type RunEvent,
  type RunInfo,
  type ToolEvent,
  type Upload,
  type VlmParams,
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
import { Button, ErrorNote, Glass, Label, Slider, spring } from "../components/ui";

const DEFAULT_PARAMS: VlmParams = {
  max_tokens: 1024,
  temperature: 0.7,
  repetition_penalty: 1.1,
  fps: 2,
  max_frames: 32,
  reasoning: "off",
};

// Web, files and commands are opt-in per chat; image generation is offered unless switched off.
const DEFAULT_SETTINGS: ChatSettings = { web: false, image: true, files: false, exec: false, autoApprove: [], model: null, agent: null };
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

export function ChatPage({ prefill }: { prefill: OutputMeta | null }) {
  // Conversations and runs live in the backend; the open chat and the generation knobs are remembered locally.
  const [ui, setUi] = usePersistentState("chat:ui", { chatId: null as string | null, params: DEFAULT_PARAMS, system: "" });
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [settings, setSettings] = useState<ChatSettings>(DEFAULT_SETTINGS);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Upload[]>([]);
  const [uploading, setUploading] = useState(false);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showPanel, setShowPanel] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [models, setModels] = useState<ModelList | null>(null);
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const { ref: list, contentRef, atBottom, scrollToBottom } = useStickToBottom();
  const stream = useRef<AbortController | null>(null);
  const chatIdRef = useRef<string | null>(ui.chatId);
  const { system, chatId } = ui;
  const params = { ...DEFAULT_PARAMS, ...ui.params };
  const setParam = <K extends keyof VlmParams>(key: K, value: VlmParams[K]) =>
    setUi((u) => ({ ...u, params: { ...DEFAULT_PARAMS, ...u.params, [key]: value } }));
  const running = !!run && run.chat_id === chatId;

  const refreshChats = useCallback(() => api.chats().then(setChats).catch(() => undefined), []);
  const refreshModels = useCallback(() => api.models().then(setModels).catch(() => undefined), []);
  const refreshAgents = useCallback(() => api.agents().then(setAgents).catch(() => undefined), []);
  const agentOf = (id?: string | null) => agents.find((a) => a.id === (id ?? DEFAULT_AGENT)) ?? agents.find((a) => a.id === DEFAULT_AGENT);
  const agent = agentOf(settings.agent);

  useEffect(() => {
    window.addEventListener(AGENTS_CHANGED, refreshAgents);
    return () => window.removeEventListener(AGENTS_CHANGED, refreshAgents);
  }, [refreshAgents]);

  /** Load a chat from the backend, which owns it; ignored if another chat was opened meanwhile. */
  const loadChat = useCallback(async (id: string) => {
    const chat = await api.chat(id);
    if (chatIdRef.current !== id) return;
    setTurns(chat.turns);
    setSettings({ ...DEFAULT_SETTINGS, ...chat.settings });
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
          if (ev.type === "status") setRun((r) => r && { ...r, status: ev.status });
          if (ev.type === "status" && ev.note) setNotice(ev.note);
          if (ev.type === "auto_approve") setSettings((s) => ({ ...s, autoApprove: ev.auto_approve }));
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
        setSettings(DEFAULT_SETTINGS);
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
    refreshAgents();
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

  const send = async () => {
    const files = attachments;
    const text = input.trim() || (files.length ? "请描述这些内容。" : "");
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
    setTurns([...before, { role: "user", mode: "chat", content: text, attachments: files }]);
    scrollToBottom();
    try {
      const { autoApprove, model, agent: agentId, ...permissions } = settings;
      const info = await api.startRun({
        model: model ?? null,
        agent: agentId ?? null,
        chat_id: id,
        content: text,
        attachments: files.map((a) => a.id),
        params,
        permissions,
        auto_approve: autoApprove ?? [],
        system,
      });
      follow(info);
      refreshChats();
    } catch (e) {
      setTurns(before);
      setInput(text);
      setAttachments(files);
      setError(errorText(e));
    }
  };

  // Switches belong to the chat: a saved conversation stores them now, a new one with its first message.
  const changeSettings = (next: ChatSettings) => {
    setSettings(next);
    if (chatId && turns.length) api.patchChatSettings(chatId, next).catch((e) => setError(`设置未能保存：${errorText(e)}`));
  };

  // An agent brings its own tools and model; auto-approvals already granted in this chat stay.
  const chooseAgent = (a: AgentProfile) => changeSettings({ ...settings, ...a.permissions, agent: a.id, model: a.model ?? settings.model ?? null });

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
          <motion.div key={c.id} layout transition={spring} className="group relative">
            <button
              type="button"
              onClick={() => openChat(c.id)}
              className={`w-full rounded-2xl px-3 py-2.5 text-left transition-colors ${c.id === chatId ? "glass glass-strong" : "hover:bg-[var(--glass-fill)]"}`}
            >
              <div className="truncate pr-6 text-[13px] font-medium">
                {c.agent && c.agent !== DEFAULT_AGENT && <span className="mr-1">{agentOf(c.agent)?.icon}</span>}
                {c.title}
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
          </motion.div>
        ))}
      </div>
    </Glass>
  );

  return (
    <ChatActionsContext.Provider value={actions}>
      <div className="mx-auto grid h-[calc(100dvh-112px)] max-w-6xl gap-4 md:grid-cols-[250px_minmax(0,1fr)]">
        <div className="hidden min-h-0 md:block">{sidebar}</div>
        {createPortal(
          <AnimatePresence>
            {showHistory && (
              <motion.div
                className="fixed inset-0 z-50 bg-black/30 p-4 backdrop-blur-sm md:hidden"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setShowHistory(false)}
              >
                <motion.div initial={{ x: -24 }} animate={{ x: 0 }} exit={{ x: -24 }} className="h-full max-w-xs" onClick={(e) => e.stopPropagation()}>
                  {sidebar}
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>,
          document.body,
        )}

        <div className="flex min-h-0 min-w-0 flex-col gap-4">
          <div className="relative min-h-0 flex-1">
            <div ref={list} className="scroll-soft h-full overflow-y-auto px-1">
              <div ref={contentRef} className="min-h-full space-y-4 pb-2">
                {turns.length === 0 && (
                  <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="grid min-h-[60dvh] place-items-center">
                    <div className="max-w-md text-center">
                      <div className="bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] bg-clip-text text-3xl font-semibold text-transparent">
                        今天要做点什么？
                      </div>
                      <div className="ink-soft mt-2 text-sm leading-relaxed">
                        选一个 agent，直接说需求。多步任务会先列计划再逐步执行；写文件、运行命令前会先请你批准。
                      </div>
                      <div className="mt-6 grid grid-cols-2 gap-2 text-left">
                        {agents.map((a) => (
                          <button
                            key={a.id}
                            type="button"
                            onClick={() => chooseAgent(a)}
                            className={`glass rounded-2xl px-3.5 py-3 text-left transition-shadow ${a.id === agent?.id ? "ring-2 ring-[var(--accent)]" : "hover:bg-[var(--glass-fill-strong)]"}`}
                          >
                            <div className="flex items-center gap-2 text-[13px] font-medium">
                              <span className="text-base leading-none">{a.icon}</span>
                              <span className="truncate">{a.name}</span>
                            </div>
                            <div className="ink-soft mt-1 line-clamp-2 text-[11px] leading-snug">{a.description}</div>
                          </button>
                        ))}
                      </div>
                    </div>
                  </motion.div>
                )}
                <AnimatePresence initial={false}>
                  {turns.map((turn, i) => {
                    const live = running && i === turns.length - 1 && turn.role === "assistant";
                    const idle = live && !turn.content && !turn.thinking && !turn.tools?.length && !turn.plan?.length;
                    return (
                      <motion.div
                        key={`${chatId}-${i}`}
                        layout="position"
                        initial={{ opacity: 0, y: 14, scale: 0.98 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        transition={spring}
                        className={`flex ${turn.role === "user" ? "justify-end" : "justify-start"}`}
                      >
                        <div className={`max-w-[85%] ${turn.role === "user" ? "items-end" : "w-full items-start"} flex flex-col gap-2`}>
                          {turn.role === "user" && turn.mode && turn.mode !== "chat" && (
                            <span className="ink-soft inline-flex items-center gap-1 text-[11px]">
                              {turn.mode === "image" ? <ImageIcon className="size-3" /> : <ScanSearch className="size-3" />}
                              {turn.mode === "image" ? "生图" : "反推"}
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
                </AnimatePresence>
              </div>
            </div>
            <AnimatePresence>
              {!atBottom && turns.length > 0 && (
                <motion.button
                  type="button"
                  initial={{ opacity: 0, y: 8, scale: 0.9 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 8, scale: 0.9 }}
                  transition={spring}
                  onClick={() => scrollToBottom()}
                  className="glass glass-strong absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full px-3.5 py-2 text-xs font-medium"
                >
                  <ArrowDown className="size-3.5" />
                  {running ? "跟随最新进展" : "回到底部"}
                </motion.button>
              )}
            </AnimatePresence>
          </div>

          <ErrorNote error={error} />
          {notice && <div className="ink-soft glass rounded-2xl px-4 py-2.5 text-xs">{notice}</div>}

          <AnimatePresence>
            {showPanel && (
              <Glass
                initial={{ opacity: 0, y: 12, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 12, scale: 0.98 }}
                transition={spring}
                className="grid gap-4 p-5 sm:grid-cols-2"
              >
                <div className="sm:col-span-2">
                  <Label>额外要求（每条消息都会带上，可留空）</Label>
                  <textarea className="field min-h-16" value={system} onChange={(e) => setUi((u) => ({ ...u, system: e.target.value }))} />
                </div>
                {(
                  [
                    ["max_tokens", "每轮最大生成长度", 64, 4096, 64, (v: number) => `${v} tokens`],
                    ["temperature", "温度", 0, 1.5, 0.05, (v: number) => v.toFixed(2)],
                    ["repetition_penalty", "重复惩罚", 1, 1.5, 0.05, (v: number) => v.toFixed(2)],
                    ["fps", "视频采样帧率", 0.5, 4, 0.5, (v: number) => `${v} fps`],
                    ["max_frames", "视频最多帧数", 4, 64, 4, (v: number) => `${v} 帧`],
                  ] as const
                ).map(([key, label, min, max, step, format]) => (
                  <Slider key={key} label={label} value={params[key]} min={min} max={max} step={step} format={format} onChange={(v) => setParam(key, v)} />
                ))}
              </Glass>
            )}
          </AnimatePresence>

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
            settings={settings}
            onSettings={changeSettings}
            reasoning={params.reasoning}
            onReasoning={(v) => setParam("reasoning", v)}
            onTogglePanel={() => setShowPanel((v) => !v)}
            onShowHistory={() => setShowHistory(true)}
            models={models?.models ?? []}
            defaultModel={models?.default_chat ?? null}
            onRefreshModels={refreshModels}
            agents={agents}
            agent={agent}
            onAgent={chooseAgent}
            onRefreshAgents={refreshAgents}
          />
        </div>
      </div>
    </ChatActionsContext.Provider>
  );
}
