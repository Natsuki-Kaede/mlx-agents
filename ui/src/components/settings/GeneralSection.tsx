import { FileText, FolderOpen, SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { GenParams } from "../../api";
import { updateSettings, useAppSettings } from "../../settingsStore";
import { Button, Label, Slider } from "../ui";

const PARAMS: [keyof GenParams, string, number, number, number, (v: number) => string][] = [
  ["max_tokens", "每轮最大生成长度", 64, 4096, 64, (v) => `${v} tokens`],
  ["temperature", "温度", 0, 1.5, 0.05, (v) => v.toFixed(2)],
  ["repetition_penalty", "重复惩罚", 1, 1.5, 0.05, (v) => v.toFixed(2)],
  ["top_p", "Top-p", 0.5, 1, 0.01, (v) => v.toFixed(2)],
  ["top_k", "Top-k", 0, 100, 1, (v) => (v ? String(v) : "不限")],
];

/** Workspace folder, global instructions (AGENTS.md) and generation parameters. */
export function GeneralSection({ onError }: { onError: (e: string | null) => void }) {
  const settings = useAppSettings();
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [instructions, setInstructions] = useState<string | null>(null);
  const [params, setParams] = useState<GenParams | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const paramsTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const textTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Drafts start from the saved values once they have loaded.
  useEffect(() => {
    if (!settings) return;
    setWorkspace((w) => w ?? settings.workspace);
    setInstructions((t) => t ?? settings.instructions);
    setParams((p) => p ?? settings.params);
  }, [settings]);

  const save = async (patch: Parameters<typeof updateSettings>[0], what: string) => {
    onError(null);
    try {
      const s = await updateSettings(patch);
      if (patch.workspace !== undefined) setWorkspace(s.workspace);
      setSaved(what);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  // Text and sliders save shortly after the last change rather than on every keystroke or step of a drag.
  const changeInstructions = (text: string) => {
    setInstructions(text);
    setSaved(null);
    clearTimeout(textTimer.current);
    textTimer.current = setTimeout(() => save({ instructions: text }, "instructions"), 600);
  };

  const changeParam = (key: keyof GenParams, value: number) => {
    const next = { ...params!, [key]: value };
    setParams(next);
    clearTimeout(paramsTimer.current);
    paramsTimer.current = setTimeout(() => save({ params: next }, "params"), 400);
  };

  if (!settings || workspace === null || instructions === null || !params) return <div className="ink-faint py-6 text-center text-xs">加载中…</div>;

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <Label>
          <span className="inline-flex items-center gap-1.5">
            <FolderOpen className="size-4" /> 工作目录
          </span>
        </Label>
        <div className="flex gap-2">
          <input className="field font-mono text-[13px]" value={workspace} onChange={(e) => setWorkspace(e.target.value)} placeholder="~/MLX Agents" />
          <Button variant="primary" className="shrink-0" onClick={() => save({ workspace }, "workspace")}>
            保存
          </Button>
        </div>
        <div className="ink-faint text-xs leading-relaxed">
          Agent 的文件工具、命令和 MCP 服务都在这个目录里工作。当前：{settings.workspace_resolved}
          {saved === "workspace" && " · 已保存"}
        </div>
      </section>

      <section className="space-y-2">
        <Label hint={saved === "instructions" ? "已保存" : undefined}>
          <span className="inline-flex items-center gap-1.5">
            <FileText className="size-4" /> 全局指令（AGENTS.md）
          </span>
        </Label>
        <textarea
          className="field min-h-40 font-mono text-[13px] leading-relaxed"
          value={instructions}
          onChange={(e) => changeInstructions(e.target.value)}
        />
      </section>

      <section className="space-y-3">
        <Label hint={saved === "params" ? "已保存" : undefined}>
          <span className="inline-flex items-center gap-1.5">
            <SlidersHorizontal className="size-4" /> 生成参数
          </span>
        </Label>
        <div className="grid gap-4 sm:grid-cols-3">
          {PARAMS.map(([key, label, min, max, step, format]) => (
            <Slider key={key} label={label} value={params[key]} min={min} max={max} step={step} format={format} onChange={(v) => changeParam(key, v)} />
          ))}
        </div>
      </section>
    </div>
  );
}
