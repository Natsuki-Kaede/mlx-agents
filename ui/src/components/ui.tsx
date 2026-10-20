import { motion, type HTMLMotionProps } from "motion/react";
import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export const spring = { type: "spring", stiffness: 380, damping: 32, mass: 0.8 } as const;

export function Glass({
  className = "",
  refract = false,
  strong = false,
  children,
  ...rest
}: HTMLMotionProps<"div"> & { refract?: boolean; strong?: boolean }) {
  return (
    <motion.div
      className={`glass ${refract ? "glass-refract" : ""} ${strong ? "glass-strong" : ""} rounded-[28px] ${className}`}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

type ButtonProps = HTMLMotionProps<"button"> & {
  variant?: "primary" | "ghost" | "glass";
  loading?: boolean;
  icon?: ReactNode;
};

export function Button({ variant = "glass", loading, icon, children, className = "", disabled, ...rest }: ButtonProps) {
  const look =
    variant === "primary" ? "accent-fill" : variant === "glass" ? "glass rounded-2xl" : "hover:bg-[var(--glass-fill)]";
  return (
    <motion.button
      whileHover={disabled ? undefined : { y: -1, scale: 1.015 }}
      whileTap={disabled ? undefined : { scale: 0.97 }}
      transition={spring}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-2xl px-4 py-2.5 text-sm font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-50 ${look} ${className}`}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" /> : icon}
      {children as ReactNode}
    </motion.button>
  );
}

export function Label({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between text-[13px] font-medium">
      <span>{children}</span>
      {hint !== undefined && <span className="ink-soft tabular-nums">{hint}</span>}
    </div>
  );
}

export function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  format = (v) => String(v),
}: {
  label: ReactNode;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
}) {
  const fill = `${((value - min) / (max - min)) * 100}%`;
  return (
    <label className="block">
      <Label hint={format(value)}>{label}</Label>
      <input
        type="range"
        className="slider"
        style={{ ["--fill" as string]: fill }}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex gap-1 rounded-2xl bg-[var(--glass-fill)] p-1 shadow-[inset_0_1px_2px_rgb(0_0_0/0.08)]">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className="relative flex-1 rounded-xl px-3 py-1.5 text-[13px] font-medium"
        >
          {value === o.value && <span className="glass glass-strong absolute inset-0 rounded-xl" />}
          <span className={`relative ${value === o.value ? "" : "ink-soft"}`}>{o.label}</span>
        </button>
      ))}
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode }) {
  return (
    <button type="button" onClick={() => onChange(!checked)} className="flex items-center gap-3 text-[13px] font-medium">
      <span
        className={`relative h-6 w-10 rounded-full transition-colors ${checked ? "accent-fill" : "bg-[var(--glass-fill-strong)]"}`}
      >
        <span className="absolute top-0.5 size-5 rounded-full bg-white shadow" style={{ left: checked ? 18 : 2 }} />
      </span>
      {label}
    </button>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <motion.div
      initial={{ y: -4 }}
      animate={{ y: 0 }}
      className="rounded-2xl border border-[color-mix(in_srgb,var(--danger)_40%,transparent)] bg-[color-mix(in_srgb,var(--danger)_12%,transparent)] px-4 py-3 text-sm"
    >
      {error}
    </motion.div>
  );
}
