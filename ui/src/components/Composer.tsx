import { motion } from "motion/react";
import { ArrowUp, Film, History, Paperclip, Square, Target, X } from "lucide-react";
import { useRef, useState } from "react";
import type { ModelInfo, Upload } from "../api";
import { updateSettings, useAppSettings } from "../settingsStore";
import { ModelMenu } from "./ModelMenu";
import { ReasoningMenu } from "./ReasoningMenu";
import { MenuPanel } from "./MenuPanel";
import { commandsFor, parseCommand, type SlashCommand } from "./slashCommands";
import { ToolsMenu } from "./ToolsMenu";
import { Button, Glass } from "./ui";

/** The single input box: everything goes to the agent, which decides which tools to use. */
export function Composer(props: {
  input: string;
  onInput: (v: string) => void;
  attachments: Upload[];
  onRemoveAttachment: (id: string) => void;
  uploading: boolean;
  onAttachFiles: (files: File[]) => void;
  running: boolean;
  onSend: (text?: string) => void; // text: a command picked from the menu, sent instead of the input
  onStop: () => void;
  onShowHistory: () => void;
  onError: (e: string) => void;
  models: ModelInfo[];
  defaultModel: string | null;
  onRefreshModels: () => void;
  goalOpen: boolean; // the chat has an unfinished goal: a message continues it
}) {
  const { input, attachments, running } = props;
  // Safari/WebKit ends an IME composition before the Enter keydown arrives, with isComposing already false;
  // track composition ourselves so the Enter that commits typed text does not also send the message.
  const composing = useRef(false);
  const composedAt = useRef(0);
  const settings = useAppSettings();
  const save = (patch: Parameters<typeof updateSettings>[0]) =>
    updateSettings(patch).catch((e) => props.onError(`设置未能保存：${e instanceof Error ? e.message : e}`));
  const fileInput = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const sendable = !!input.trim() || attachments.length > 0;
  const goalDraft = parseCommand(input)?.name === "goal";

  // The command menu shows while a command name is being typed; Escape hides it until the text changes.
  const [selected, setSelected] = useState(0);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const commands = dismissed === input ? [] : commandsFor(input);
  const current = Math.min(selected, commands.length - 1);
  const pick = (command: SlashCommand) => {
    if (command.args) props.onInput(`/${command.name} `);
    else if (!running) props.onSend(`/${command.name}`);
    textarea.current?.focus();
  };

  return (
    <Glass refract className="p-2.5">
      {!!attachments.length && (
        <div className="flex flex-wrap gap-2 px-1 pb-2">
          {attachments.map((a) => (
            <motion.div key={a.id} initial={{ scale: 0.85 }} animate={{ scale: 1 }} className="relative">
              {a.kind === "video" ? (
                <div className="glass grid size-16 place-items-center rounded-xl">
                  <Film className="size-5" />
                </div>
              ) : (
                <img src={a.url} className="size-16 rounded-xl object-cover" alt="" />
              )}
              <button
                type="button"
                onClick={() => props.onRemoveAttachment(a.id)}
                className="absolute -top-1.5 -right-1.5 grid size-5 place-items-center rounded-full bg-black/60 text-white"
              >
                <X className="size-3" />
              </button>
            </motion.div>
          ))}
        </div>
      )}
      <input
        ref={fileInput}
        type="file"
        hidden
        multiple
        accept="image/*,video/*"
        onChange={(e) => {
          props.onAttachFiles(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />
      <div ref={box}>
      {commands.length > 0 && (
        <MenuPanel anchor={box} panelRef={menu} width={440} className="p-1.5">
          {commands.map((c, i) => (
            <button
              key={c.name}
              type="button"
              role="menuitem"
              onMouseDown={(e) => e.preventDefault()} // keep the focus in the input box
              onMouseEnter={() => setSelected(i)}
              onClick={() => pick(c)}
              className={`flex w-full items-baseline gap-2 rounded-xl px-2.5 py-2 text-left ${i === current ? "bg-[var(--glass-fill-strong)]" : ""}`}
            >
              <span className="shrink-0 font-mono text-[13px] font-medium">/{c.name}</span>
              {c.args && <span className="ink-faint shrink-0 text-xs">{c.args}</span>}
              <span className="ink-soft min-w-0 truncate text-xs">{c.description}</span>
            </button>
          ))}
        </MenuPanel>
      )}
      <textarea
        ref={textarea}
        rows={1}
        value={input}
        onChange={(e) => {
          props.onInput(e.target.value);
          setSelected(0);
        }}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
          composedAt.current = Date.now();
        }}
        onKeyDown={(e) => {
          const ime = e.nativeEvent.isComposing || composing.current || e.keyCode === 229 || Date.now() - composedAt.current < 80;
          if (commands.length && !ime) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              setSelected((current + (e.key === "ArrowDown" ? 1 : commands.length - 1)) % commands.length);
              return;
            }
            if (e.key === "Escape") {
              setDismissed(input);
              return;
            }
            if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
              e.preventDefault();
              pick(commands[current]);
              return;
            }
          }
          if (e.key !== "Enter" || e.shiftKey || ime) return;
          e.preventDefault();
          if (sendable && !running) props.onSend();
        }}
        placeholder={props.goalOpen ? "回复或补充，agent 会继续推进目标" : "说说要做什么，Enter 发送；输入 / 查看命令"}
        className="scroll-soft block max-h-48 min-h-11 w-full resize-none bg-transparent px-2.5 py-2 text-[15px] outline-none placeholder:text-[var(--ink-faint)]"
      />
      </div>
      {/* The toolbar is a row of compact pills; menus open above it and anchor to the composer. */}
      <div className="flex min-w-0 items-center gap-1">
        <Button variant="ghost" className="px-2.5 md:hidden" title="历史对话" icon={<History className="size-5" />} onClick={props.onShowHistory} />
        <Button variant="ghost" className="px-2.5" title="附加图片或视频" loading={props.uploading} icon={<Paperclip className="size-5" />} onClick={() => fileInput.current?.click()} />
        {goalDraft && (
          <span className="accent-fill flex h-9 shrink-0 items-center gap-1 rounded-full px-2.5 text-xs font-medium" title="这条消息会设为目标，agent 自主循环执行直到完成并验证">
            <Target className="size-4" />
            <span className="hidden sm:inline">目标</span>
          </span>
        )}
        {settings && (
          <>
            <ToolsMenu value={settings} onChange={save} />
            <ModelMenu
              models={props.models}
              value={settings.chat_model ?? props.defaultModel}
              onChange={(chat_model) => save({ chat_model })}
              onOpen={props.onRefreshModels}
            />
            <ReasoningMenu value={settings.reasoning} onChange={(reasoning) => save({ reasoning })} />
          </>
        )}
        <div className="flex-1" />
        {running ? (
          <Button variant="primary" className="size-10 shrink-0 rounded-full p-0" title="停止任务" icon={<Square className="size-4" />} onClick={props.onStop} />
        ) : (
          <Button variant="primary" className="size-10 shrink-0 rounded-full p-0" title="发送" disabled={!sendable} icon={<ArrowUp className="size-5" />} onClick={() => props.onSend()} />
        )}
      </div>
    </Glass>
  );
}
