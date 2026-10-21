import { ChevronDown, FileText, FolderOpen, SlidersHorizontal, Target } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type GenParams, type ModelList } from "../../api";
import { updateSettings, useAppSettings } from "../../settingsStore";
import { Button, Label, Slider, Toggle } from "../ui";

/** Sampling parameters, set by hand only when automatic (per-model recommended) sampling is off. */
const SAMPLING: [Exclude<keyof GenParams, "max_tokens">, string, number, number, number, (v: number) => string][] = [
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
  const [budget, setBudget] = useState<{ goal_max_rounds: number; goal_max_minutes: number } | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [models, setModels] = useState<ModelList | null>(null);
  const paramsTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const budgetTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const textTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    api.models().then(setModels).catch(() => undefined);
  }, []);

  // Drafts start from the saved values once they have loaded.
  useEffect(() => {
    if (!settings) return;
    setWorkspace((w) => w ?? settings.workspace);
    setInstructions((t) => t ?? settings.instructions);
    setParams((p) => p ?? settings.params);
    setBudget((b) => b ?? { goal_max_rounds: settings.goal_max_rounds, goal_max_minutes: settings.goal_max_minutes });
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

  const changeBudget = (key: "goal_max_rounds" | "goal_max_minutes", value: number) => {
    const next = { ...budget!, [key]: value };
    setBudget(next);
    clearTimeout(budgetTimer.current);
    budgetTimer.current = setTimeout(() => save(next, "goal"), 400);
  };

  const chatModel = models?.models.find((m) => m.id === (settings?.chat_model ?? models.default_chat));
  const recommended = chatModel?.sampling;

  if (!settings || workspace === null || instructions === null || !params || !budget) return <div className="ink-faint py-6 text-center text-xs">加载中…</div>;

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
        <Label hint={saved === "params" || saved === "sampling" ? "已保存" : undefined}>
          <span className="inline-flex items-center gap-1.5">
            <SlidersHorizontal className="size-4" /> 生成
          </span>
        </Label>
        <Slider label="每轮最大输出长度" value={params.max_tokens} min={64} max={4096} step={64} format={(v) => `${v} tokens`} onChange={(v) => changeParam("max_tokens", v)} />
        <div className="ink-faint text-xs">不含思考：思考的额度由推理等级决定，另外计算。</div>
        <button type="button" onClick={() => setAdvanced((a) => !a)} className="ink-soft flex items-center gap-1 text-xs font-medium">
          <ChevronDown className={`size-3.5 transition-transform duration-300 ${advanced ? "rotate-180" : ""}`} /> 高级
        </button>
        <div className={`reveal ${advanced ? "open" : ""}`}>
          <div className="space-y-3">
            <Toggle
              checked={settings.sampling_auto}
              onChange={(sampling_auto) => save({ sampling_auto }, "sampling")}
              label="采样参数按模型自动选择（推荐）"
            />
            {settings.sampling_auto ? (
              <div className="ink-soft text-xs leading-relaxed">
                {recommended
                  ? `${chatModel!.id} 推荐：温度 ${recommended.temperature} · Top-p ${recommended.top_p} · Top-k ${recommended.top_k} · 重复惩罚 ${recommended.repetition_penalty}`
                  : "使用模型自带的 generation_config.json，没有时按模型系列的推荐值。"}
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                {SAMPLING.map(([key, label, min, max, step, format]) => (
                  <Slider key={key} label={label} value={params[key]} min={min} max={max} step={step} format={format} onChange={(v) => changeParam(key, v)} />
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <Label hint={saved === "goal" ? "已保存" : undefined}>
          <span className="inline-flex items-center gap-1.5">
            <Target className="size-4" /> 目标模式预算（每次运行）
          </span>
        </Label>
        <div className="grid gap-4 sm:grid-cols-2">
          <Slider label="最多轮数" value={budget.goal_max_rounds} min={10} max={500} step={10} format={(v) => `${v} 轮`} onChange={(v) => changeBudget("goal_max_rounds", v)} />
          <Slider label="最长时间" value={budget.goal_max_minutes} min={5} max={240} step={5} format={(v) => `${v} 分钟`} onChange={(v) => changeBudget("goal_max_minutes", v)} />
        </div>
        <div className="ink-faint text-xs">用完后目标会暂停，可以在目标卡片上点「继续」。</div>
      </section>
    </div>
  );
}
