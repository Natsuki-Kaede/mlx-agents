import { useLayoutEffect, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

type Place = { left: number; bottom: number; maxHeight: number; width: number };

/**
 * A menu that opens just above the button that opened it, left-aligned with it and kept inside the window.
 * It is rendered at the top of the page: inside the glass input bar, whose backdrop-filter makes it the
 * containing block, fixed positioning would be relative to the bar instead of the window.
 */
export function MenuPanel({
  anchor,
  panelRef,
  width,
  className = "",
  children,
}: {
  anchor: RefObject<HTMLElement | null>;
  panelRef: RefObject<HTMLDivElement | null>;
  width: number;
  className?: string;
  children: ReactNode;
}) {
  const [place, setPlace] = useState<Place | null>(null);

  useLayoutEffect(() => {
    const measure = () => {
      const rect = anchor.current?.getBoundingClientRect();
      if (!rect) return;
      const w = Math.min(width, window.innerWidth - 16);
      setPlace({
        width: w,
        left: Math.min(Math.max(8, rect.left), window.innerWidth - w - 8),
        bottom: window.innerHeight - rect.top + 8,
        maxHeight: Math.max(160, rect.top - 16),
      });
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [anchor, width]);

  if (!place) return null;
  return createPortal(
    <div
      ref={panelRef}
      role="menu"
      style={{ position: "fixed", ...place, transformOrigin: "0 100%" }}
      className={`anim-pop glass glass-solid scroll-soft z-50 overflow-y-auto rounded-2xl ${className}`}
    >
      {children}
    </div>,
    document.body,
  );
}
