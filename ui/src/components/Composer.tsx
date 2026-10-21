import { motion } from "motion/react";
import { ArrowUp, Film, History, Paperclip, Square, X } from "lucide-react";
import { useRef } from "react";
import type { ModelInfo, Upload } from "../api";
import { updateSettings, useAppSettings } from "../settingsStore";
import { ModelMenu } from "./ModelMenu";
import { ReasoningMenu } from "./ReasoningMenu";
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
  onSend: () => void;
  onStop: () => void;
  onShowHistory: () => void;
  onError: (e: string) => void;
  models: ModelInfo[];
  defaultModel: string | null;
  onRefreshModels: () => void;
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
  const sendable = !!input.trim() || attachments.length > 0;

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
      <textarea
        rows={1}
        value={input}
        onChange={(e) => props.onInput(e.target.value)}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
          composedAt.current = Date.now();
        }}
        onKeyDown={(e) => {
          if (e.key !== "Enter" || e.shiftKey) return;
          const ime = e.nativeEvent.isComposing || composing.current || e.keyCode === 229 || Date.now() - composedAt.current < 80;
          if (ime) return;
          e.preventDefault();
          if (sendable && !running) props.onSend();
        }}
        placeholder="说说要做什么，Enter 发送"
        className="scroll-soft block max-h-48 min-h-11 w-full resize-none bg-transparent px-2.5 py-2 text-[15px] outline-none placeholder:text-[var(--ink-faint)]"
      />
      {/* The toolbar is a row of compact pills; menus open above it and anchor to the composer. */}
      <div className="flex min-w-0 items-center gap-1">
        <Button variant="ghost" className="px-2.5 md:hidden" title="历史对话" icon={<History className="size-5" />} onClick={props.onShowHistory} />
        <Button variant="ghost" className="px-2.5" title="附加图片或视频" loading={props.uploading} icon={<Paperclip className="size-5" />} onClick={() => fileInput.current?.click()} />
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
          <Button variant="primary" className="size-10 shrink-0 rounded-full p-0" title="发送" disabled={!sendable} icon={<ArrowUp className="size-5" />} onClick={props.onSend} />
        )}
      </div>
    </Glass>
  );
}
