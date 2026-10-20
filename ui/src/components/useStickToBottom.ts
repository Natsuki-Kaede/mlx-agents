import { useCallback, useEffect, useRef, useState } from "react";

const NEAR_BOTTOM_PX = 6; // small, so even a short upward scroll counts as leaving the bottom

/**
 * Keep a scroll container pinned to its bottom while its content grows, as long as the reader is already at the
 * bottom. Scrolling up releases the pin, so earlier content can be read undisturbed; scrolling back down (or
 * calling `scrollToBottom`) re-engages it. `ref` goes on the scroll container, `contentRef` on its single
 * content wrapper, whose size changes drive the following. Both are callback refs, so containers that mount
 * later (e.g. inside a collapsible) are picked up.
 */
export function useStickToBottom<T extends HTMLElement = HTMLDivElement, C extends HTMLElement = HTMLDivElement>() {
  const [el, setEl] = useState<T | null>(null);
  const [content, setContent] = useState<C | null>(null);
  const pinned = useRef(true);
  const [atBottom, setAtBottom] = useState(true);

  useEffect(() => {
    if (!el || !content) return;
    const update = (value: boolean) => {
      pinned.current = value;
      setAtBottom(value);
    };
    // Being at the bottom (re-)engages the pin; otherwise only an upward move releases it. Following and smooth
    // scrolls only go down, and content shrinking (e.g. the thinking block folding) clamps to the bottom.
    let lastTop = el.scrollTop;
    const onScroll = () => {
      const top = el.scrollTop;
      if (el.scrollHeight - top - el.clientHeight <= NEAR_BOTTOM_PX) update(true);
      else if (top < lastTop - 1) update(false);
      lastTop = top;
    };
    // An upward wheel or drag releases the pin at once, before the next content growth can pull the view back.
    const onWheel = (e: WheelEvent) => e.deltaY < 0 && update(false);
    let touchY = 0;
    const onTouchStart = (e: TouchEvent) => (touchY = e.touches[0].clientY);
    const onTouchMove = (e: TouchEvent) => e.touches[0].clientY > touchY && update(false);
    const follow = () => {
      if (pinned.current) el.scrollTop = el.scrollHeight;
    };
    const observer = new ResizeObserver(follow);
    observer.observe(content);
    el.addEventListener("scroll", onScroll, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: true });
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", onScroll);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
    };
  }, [el, content]);

  const scrollToBottom = useCallback(
    (smooth = true) => {
      pinned.current = true;
      setAtBottom(true);
      el?.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    },
    [el],
  );

  return { ref: setEl, contentRef: setContent, atBottom, scrollToBottom };
}
