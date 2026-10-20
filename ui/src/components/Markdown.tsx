import { memo, useEffect, useRef, useState } from "react";
import "katex/dist/katex.min.css";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkCjkFriendly from "remark-cjk-friendly";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

/**
 * Reveal `text` gradually while it is streaming, so bursty token chunks read as a steady flow. The reveal speed
 * scales with how far behind it is, so it never lags more than a few frames; finished text shows at once.
 */
export function useSmoothText(text: string, streaming: boolean) {
  const [shown, setShown] = useState(0);
  const target = useRef(text.length);
  target.current = text.length;

  useEffect(() => {
    if (!streaming) return;
    let frame = 0;
    const tick = () => {
      setShown((n) => {
        const gap = target.current - n;
        return gap <= 0 ? n : n + Math.max(1, Math.ceil(gap / 6));
      });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [streaming]);

  return streaming ? text.slice(0, Math.min(shown, text.length)) : text;
}

/** Visible characters, as a reader would count 字 (whitespace excluded). */
export const countChars = (text: string) => text.replace(/\s/g, "").length;

/** Qwen often writes math as \(...\) and \[...\]; remark-math only understands $ delimiters. */
function normalizeMath(text: string) {
  return text
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, body) => `$$${body}$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, body) => `$${body}$`);
}

const MarkdownBody = memo(function MarkdownBody({ text }: { text: string }) {
  return (
    <ReactMarkdown
      // remark-cjk-friendly lets **bold** close next to Chinese punctuation, e.g. 这种**“刁钻”**的
      remarkPlugins={[remarkGfm, remarkCjkFriendly, remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false }]]}
      components={{
        a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
      }}
    >
      {normalizeMath(text)}
    </ReactMarkdown>
  );
});

/** Markdown with a smooth streaming reveal and a caret at the end while text is still arriving. */
export function Markdown({ text, streaming = false, className = "" }: { text: string; streaming?: boolean; className?: string }) {
  const shown = useSmoothText(text, streaming);
  return (
    <div className={`md ${streaming ? "md-streaming" : ""} ${className}`}>
      <MarkdownBody text={shown} />
    </div>
  );
}
