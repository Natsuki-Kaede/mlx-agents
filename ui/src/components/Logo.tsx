import { useId } from "react";

/** The app icon (desktop/make_icon.py): gradient squircle with a large and a small four-point sparkle. */
export function Logo({ className = "" }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 100 100" className={className} role="img" aria-label="MLX Agents">
      <defs>
        <linearGradient id={`${id}g`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5b6cff" />
          <stop offset="1" stopColor="#b25bff" />
        </linearGradient>
        <radialGradient id={`${id}s`} cx=".5" cy="0" r=".75">
          <stop offset="0" stopColor="#fff" stopOpacity=".22" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="100" height="100" rx="22.5" fill={`url(#${id}g)`} />
      <rect width="100" height="100" rx="22.5" fill={`url(#${id}s)`} />
      <path fill="#fff" fillOpacity=".97" d="M80 53Q49.33 55.33 47 86Q44.67 55.33 14 53Q44.67 50.67 47 20Q49.33 50.67 80 53Z" />
      <path fill="#fff" fillOpacity=".84" d="M86 25Q75.78 25.78 75 36Q74.22 25.78 64 25Q74.22 24.22 75 14Q75.78 24.22 86 25Z" />
    </svg>
  );
}
