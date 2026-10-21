import { useEffect, type RefObject } from "react";

/** Close a popover on a click outside all of `areas` (its button and its panel) or on Escape. */
export function useDismiss(open: boolean, close: () => void, ...areas: RefObject<HTMLElement | null>[]) {
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => !areas.some((a) => a.current?.contains(e.target as Node)) && close();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
    // `areas` are stable refs; listing them would re-subscribe on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, close]);
}
