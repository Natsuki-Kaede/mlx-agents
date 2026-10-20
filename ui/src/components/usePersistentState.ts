import { useEffect, useState } from "react";

/** Object state mirrored to localStorage, for per-browser drafts. Saved fields are merged over `initial`, so
 * fields added later get their defaults; falls back to plain state when storage is unavailable. */
export function usePersistentState<T extends object>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(key);
      return saved === null ? initial : { ...initial, ...JSON.parse(saved) };
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* storage full or unavailable: the draft lasts for this session only */
    }
  }, [key, value]);
  return [value, setValue] as const;
}
