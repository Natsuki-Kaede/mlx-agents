/** The desktop client's native window (desktop/native.py), when the page runs inside it. */
type NativeApi = {
  set_drag_regions: (height: number, controls: number[][]) => Promise<void>;
  ui_state: (state: { theme?: string; auto?: boolean }) => Promise<void>;
};

const nativeApi = () => (window as unknown as { pywebview?: { api?: Partial<NativeApi> } }).pywebview?.api;

function whenNative(fn: (api: Partial<NativeApi>) => void) {
  const api = nativeApi();
  if (api) fn(api);
  else window.addEventListener("pywebviewready", () => fn(nativeApi() ?? {}), { once: true });
}

/** The page's theme and switches, for the window's appearance and the menu bar's check marks. */
export function reportState(state: Parameters<NativeApi["ui_state"]>[0]) {
  whenNative((api) => api.ui_state?.(state).catch(() => undefined));
}

/** Keep the window told which part of the header drags it: the header minus its controls, and nothing while
 * a full-window overlay (a dialog) covers it. Returns a cleanup function. */
export function watchDragRegions(header: HTMLElement): () => void {
  let timer = 0;
  const report = () => {
    clearTimeout(timer);
    timer = window.setTimeout(() => {
      const overlay = document.querySelector("body > .fixed.inset-0"); // dialogs are portaled straight into body
      const height = overlay ? 0 : header.getBoundingClientRect().bottom;
      const controls = Array.from(header.querySelectorAll("button, a, input, select, [role=button]"), (el) => {
        const r = el.getBoundingClientRect();
        return [r.left, r.top, r.width, r.height];
      });
      whenNative((api) => api.set_drag_regions?.(height, controls).catch(() => undefined));
    }, 30);
  };
  const resize = new ResizeObserver(report);
  resize.observe(header);
  const changes = new MutationObserver(report);
  changes.observe(header, { childList: true, subtree: true });
  changes.observe(document.body, { childList: true });
  window.addEventListener("resize", report);
  report();
  return () => {
    clearTimeout(timer);
    resize.disconnect();
    changes.disconnect();
    window.removeEventListener("resize", report);
  };
}

/** Commands from the native menu bar ("new-chat", "theme:dark", ...). Returns a cleanup function. */
export function onCommand(handler: (command: string) => void): () => void {
  const listener = (e: Event) => handler((e as CustomEvent<string>).detail);
  window.addEventListener("app-command", listener);
  return () => window.removeEventListener("app-command", listener);
}
