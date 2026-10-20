import { useSyncExternalStore } from "react";
import { api, type AppSettings, type SettingsPatch } from "./api";

/** The app settings live in the backend; this keeps one shared copy for every component that shows them. */
let current: AppSettings | null = null;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

function merge(base: AppSettings, patch: SettingsPatch): AppSettings {
  return {
    ...base,
    ...patch,
    permissions: { ...base.permissions, ...patch.permissions },
    params: { ...base.params, ...patch.params },
  };
}

export function loadSettings(): Promise<void> {
  loading ??= api
    .settings()
    .then((s) => {
      current = s;
      emit();
    })
    .finally(() => {
      loading = null;
    });
  return loading;
}

/** Reflect a change the backend already made (e.g. auto-approve switched on from an approval card). */
export function applySettings(patch: SettingsPatch) {
  if (!current) return;
  current = merge(current, patch);
  emit();
}

/** Change settings: shown at once, then saved; the saved copy replaces the local one. */
export async function updateSettings(patch: SettingsPatch): Promise<AppSettings> {
  applySettings(patch);
  try {
    current = await api.saveSettings(patch);
    emit();
    return current;
  } catch (e) {
    await loadSettings();
    throw e;
  }
}

export function useAppSettings(): AppSettings | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      if (!current) loadSettings().catch(() => undefined);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}
