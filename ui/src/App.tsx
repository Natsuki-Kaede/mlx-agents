import { MotionConfig } from "motion/react";
import { Images, MessagesSquare, Moon, Settings, Sun, SunMoon } from "lucide-react";
import { Logo } from "./components/Logo";
import { SlidePill } from "./components/ui";
import { onCommand, reportState, watchDragRegions } from "./native";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, type OutputMeta, type Status } from "./api";
import { SettingsDialog, type SettingsTab } from "./components/SettingsDialog";
import { ChatPage } from "./pages/Chat";
import { GalleryPage } from "./pages/Gallery";

type Tab = "chat" | "gallery";
type Theme = "system" | "light" | "dark";

const TABS: { id: Tab; label: string; icon: typeof Images }[] = [
  { id: "chat", label: "对话", icon: MessagesSquare },
  { id: "gallery", label: "作品库", icon: Images },
];

const BUSY_LABEL: Record<string, string> = { chat: "生成中", image: "生图中" };

function readPref<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T) || fallback;
  } catch {
    return fallback;
  }
}
function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: preference lasts for this session only */
  }
}

function useStatus() {
  const [status, setStatus] = useState<Status | null>(null);
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .status()
        .then((s) => alive && (setStatus(s), setOffline(false)))
        .catch(() => alive && setOffline(true));
    tick();
    const timer = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  return { status, offline };
}

function StatusPill({ status, offline }: { status: Status | null; offline: boolean }) {
  const busy = status?.busy;
  // Runs can be active while the model is idle: running a tool or waiting for an approval.
  const working = !busy && !!status?.runs;
  const tone = offline ? "var(--danger)" : busy || working ? "var(--warn)" : "var(--ok)";
  const text = offline
    ? "后端未连接"
    : busy
      ? `${BUSY_LABEL[busy] ?? busy}${status!.pending > 1 ? ` · 排队 ${status!.pending - 1}` : ""}`
      : working
        ? `执行中 · ${status!.runs} 个任务`
        : "空闲";
  return (
    <div className="glass flex items-center gap-2 rounded-full px-3 py-1.5 text-xs">
      <span className="relative flex size-2.5">
        {(busy || offline) && <span className="absolute inline-flex size-full animate-ping rounded-full opacity-60" style={{ background: tone }} />}
        <span className="relative inline-flex size-2.5 rounded-full" style={{ background: tone }} />
      </span>
      <span className="font-medium">{text}</span>
      {status && (
        <span className="ink-soft hidden tabular-nums sm:inline" title="整机 CPU 占用 · GPU 占用 · 当前模型占用的内存">
          CPU {status.cpu}%{status.gpu !== null && ` · GPU ${status.gpu}%`} · 模型 {status.active_gb.toFixed(1)} GB
        </span>
      )}
    </div>
  );
}

/** A tab's page; it stays mounted while hidden and rises in each time it is shown. */
function Page({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <main hidden={!active} className={active ? "anim-rise" : ""}>
      {children}
    </main>
  );
}

export default function App() {
  // Older versions stored tabs that no longer exist; anything but the gallery opens the chat.
  const [tab, setTab] = useState<Tab>(() => (readPref<string>("tab", "chat") === "gallery" ? "gallery" : "chat"));
  const [theme, setTheme] = useState<Theme>(() => readPref<Theme>("theme", "system"));
  const [prefill, setPrefill] = useState<OutputMeta | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab | undefined>();
  const { status, offline } = useStatus();

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () =>
      document.documentElement.setAttribute("data-theme", theme === "system" ? (media.matches ? "dark" : "light") : theme);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);

  useEffect(() => {
    const open = (e: Event) => {
      setSettingsTab((e as CustomEvent<SettingsTab | undefined>).detail);
      setShowSettings(true);
    };
    window.addEventListener("open-settings", open);
    return () => window.removeEventListener("open-settings", open);
  }, []);

  // The desktop client's native window: header drag area, appearance and menu check marks, menu commands.
  const header = useRef<HTMLElement>(null);
  useEffect(() => (header.current ? watchDragRegions(header.current) : undefined), []);
  useEffect(() => reportState({ theme }), [theme]);
  const commands = useRef<(command: string) => void>(() => undefined);
  commands.current = (command) => {
    const [name, value] = command.split(":");
    if (command === "settings") {
      setSettingsTab(undefined);
      setShowSettings(true);
    } else if (command === "tab-chat" || command === "new-chat") go("chat");
    else if (command === "tab-gallery") go("gallery");
    else if (name === "theme") {
      setTheme(value as Theme);
      writePref("theme", value);
    }
  };
  useEffect(() => onCommand((command) => commands.current(command)), []);

  const go = (t: Tab) => {
    setTab(t);
    writePref("tab", t);
  };
  const cycleTheme = () => {
    const next: Theme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
    setTheme(next);
    writePref("theme", next);
  };
  const ThemeIcon = theme === "system" ? SunMoon : theme === "light" ? Sun : Moon;
  // The GPU is shared with the models, so the ambient animation pauses while anything is running.
  const paused = !!status?.busy;

  return (
    <MotionConfig reducedMotion="user">
      <svg width="0" height="0" className="absolute" aria-hidden>
        <filter id="liquid-refract" x="0" y="0" width="100%" height="100%">
          <feTurbulence type="fractalNoise" baseFrequency="0.008 0.012" numOctaves="2" seed="7" result="noise" />
          <feGaussianBlur in="noise" stdDeviation="2" result="soft" />
          <feDisplacementMap in="SourceGraphic" in2="soft" scale="38" xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </svg>
      <div className={`ambient ${paused ? "paused" : ""}`}>
        <div className="blob" />
        <div className="blob" />
        <div className="blob" />
        <div className="blob" />
        <div className="grain" />
      </div>

      {/* Equal gutters on both sides: the logo lines up with the chat list, the settings button with the chat. */}
      <div className="relative z-10 mx-auto max-w-[1680px] px-4 pb-4 sm:px-5">
        <header ref={header} className="header-veil sticky top-0 z-40 isolate flex min-h-[var(--header-h)] flex-wrap items-center gap-3 ps-[var(--header-inset)]">
          <Logo className="size-9 shrink-0 drop-shadow-[0_4px_12px_rgba(91,108,255,0.35)]" />

          {/* Equal-width tabs; at md and up the nav is exactly as wide as the chat list under it. */}
          <nav className="glass glass-refract relative inline-grid grid-cols-2 gap-1 rounded-full p-1 md:w-[var(--nav-w)]">
            <SlidePill index={TABS.findIndex((t) => t.id === tab)} count={TABS.length} className="accent-fill rounded-full" />
            {TABS.map(({ id, label, icon: Icon }) => (
              <button key={id} type="button" onClick={() => go(id)} className="relative rounded-full px-3 py-2 text-sm font-medium whitespace-nowrap">
                <span className={`relative flex items-center justify-center gap-1.5 transition-colors duration-300 ${tab === id ? "text-white" : "ink-soft"}`}>
                  <Icon className="size-4" />
                  <span className="hidden sm:inline">{label}</span>
                </span>
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <StatusPill status={status} offline={offline} />
            <button type="button" title={`主题：${theme === "system" ? "跟随系统" : theme === "light" ? "浅色" : "深色"}`} onClick={cycleTheme} className="glass grid size-9 place-items-center rounded-full">
              <ThemeIcon className="size-4" />
            </button>
            <button type="button" title="设置" onClick={() => {
                setSettingsTab(undefined);
                setShowSettings(true);
              }} className="glass grid size-9 place-items-center rounded-full">
              <Settings className="size-4" />
            </button>
          </div>
        </header>

        {/* Every page stays mounted so switching tabs keeps in-progress chats, drafts and jobs. */}
        <Page active={tab === "chat"}>
          <ChatPage prefill={prefill} />
        </Page>
        <Page active={tab === "gallery"}>
          <GalleryPage
            active={tab === "gallery"}
            onReuse={(meta) => {
              setPrefill({ ...meta });
              go("chat");
            }}
          />
        </Page>
      </div>
      <SettingsDialog open={showSettings} initialTab={settingsTab} onClose={() => setShowSettings(false)} />
    </MotionConfig>
  );
}
