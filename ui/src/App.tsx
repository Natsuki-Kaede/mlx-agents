import { MotionConfig, motion } from "motion/react";
import { Images, MessagesSquare, Moon, Settings, Sparkle, Sun, SunMoon, Wind } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, type OutputMeta, type Status } from "./api";
import { SettingsDialog } from "./components/SettingsDialog";
import { spring } from "./components/ui";
import { ChatPage } from "./pages/Chat";
import { GalleryPage } from "./pages/Gallery";

type Tab = "chat" | "gallery";
type Theme = "system" | "light" | "dark";

const TABS: { id: Tab; label: string; icon: typeof Images }[] = [
  { id: "chat", label: "对话", icon: MessagesSquare },
  { id: "gallery", label: "作品库", icon: Images },
];

const BUSY_LABEL: Record<string, string> = { agent: "执行中", image: "生图中" };
const MODEL_LABEL: Record<string, string> = { image: "生图模型", vlm: "对话模型" };

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
  const tone = offline ? "var(--danger)" : busy ? "var(--warn)" : "var(--ok)";
  const text = offline
    ? "后端未连接"
    : busy
      ? `${BUSY_LABEL[busy] ?? busy}${status!.pending > 1 ? ` · 排队 ${status!.pending - 1}` : ""}`
      : "空闲";
  return (
    <motion.div layout className="glass flex items-center gap-2 rounded-full px-3 py-1.5 text-xs">
      <span className="relative flex size-2.5">
        {(busy || offline) && <span className="absolute inline-flex size-full animate-ping rounded-full opacity-60" style={{ background: tone }} />}
        <span className="relative inline-flex size-2.5 rounded-full" style={{ background: tone }} />
      </span>
      <span className="font-medium">{text}</span>
      {status && (
        <span className="ink-soft hidden tabular-nums sm:inline">
          {status.loaded ? `${MODEL_LABEL[status.loaded]} · ` : ""}
          {status.active_gb.toFixed(1)} GB
        </span>
      )}
    </motion.div>
  );
}

function Page({ active, children }: { active: boolean; children: ReactNode }) {
  return (
    <motion.main
      hidden={!active}
      initial={false}
      animate={active ? { opacity: 1, y: 0, filter: "blur(0px)", transitionEnd: { filter: "none" } } : { opacity: 0, y: 14, filter: "blur(10px)" }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.main>
  );
}

export default function App() {
  // Older versions stored tabs that no longer exist; anything but the gallery opens the chat.
  const [tab, setTab] = useState<Tab>(() => (readPref<string>("tab", "chat") === "gallery" ? "gallery" : "chat"));
  const [theme, setTheme] = useState<Theme>(() => readPref<Theme>("theme", "system"));
  const [calm, setCalm] = useState(() => readPref<string>("calm", "0") === "1");
  const [prefill, setPrefill] = useState<OutputMeta | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const { status, offline } = useStatus();

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () =>
      document.documentElement.setAttribute("data-theme", theme === "system" ? (media.matches ? "dark" : "light") : theme);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);

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
  const paused = calm || !!status?.busy;

  return (
    <MotionConfig reducedMotion={calm ? "always" : "user"}>
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

      <div className="relative z-10 mx-auto max-w-[1440px] px-4 pb-6 sm:px-6">
        <header className="header-veil sticky top-0 z-40 isolate flex flex-wrap items-center gap-3 py-4">
          <div className="flex items-center gap-2 pr-2">
            <div className="accent-fill grid size-9 place-items-center rounded-2xl">
              <Sparkle className="size-5" />
            </div>
            <div className="hidden text-[15px] font-semibold md:block">MLX Studio</div>
          </div>

          <nav className="glass glass-refract flex gap-1 rounded-full p-1">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button key={id} type="button" onClick={() => go(id)} className="relative rounded-full px-3.5 py-2 text-sm font-medium sm:px-4">
                {tab === id && <motion.span layoutId="nav-pill" transition={spring} className="accent-fill absolute inset-0 rounded-full" />}
                <span className={`relative flex items-center gap-1.5 ${tab === id ? "text-white" : "ink-soft"}`}>
                  <Icon className="size-4" />
                  <span className="hidden sm:inline">{label}</span>
                </span>
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <StatusPill status={status} offline={offline} />
            <button
              type="button"
              title={calm ? "已减少动效" : "减少动效"}
              onClick={() => {
                setCalm(!calm);
                writePref("calm", calm ? "0" : "1");
              }}
              className={`glass grid size-9 place-items-center rounded-full ${calm ? "text-[var(--accent)]" : ""}`}
            >
              <Wind className="size-4" />
            </button>
            <button type="button" title={`主题：${theme === "system" ? "跟随系统" : theme === "light" ? "浅色" : "深色"}`} onClick={cycleTheme} className="glass grid size-9 place-items-center rounded-full">
              <ThemeIcon className="size-4" />
            </button>
            <button type="button" title="工作目录与长期记忆" onClick={() => setShowSettings(true)} className="glass grid size-9 place-items-center rounded-full">
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
      <SettingsDialog open={showSettings} onClose={() => setShowSettings(false)} />
    </MotionConfig>
  );
}
