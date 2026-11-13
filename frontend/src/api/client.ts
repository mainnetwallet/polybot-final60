import type {
  Analytics,
  LogRow,
  Mode,
  OrderRow,
  PositionRow,
  SettingsState,
  Snapshot,
  TriggerRow,
} from "./types";

/** Optional API_TOKEN, stored once in the browser: localStorage.setItem("polybot_api_token", "...") */
export function apiToken(): string {
  try {
    return window.localStorage.getItem("polybot_api_token") ?? "";
  } catch {
    return "";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = apiToken();
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-API-Key": token } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  state: () => request<Snapshot>("/api/state"),
  health: () => request<Record<string, unknown>>("/api/health"),

  start: (confirmLive = false, phrase = "") =>
    request<Snapshot>("/api/bot/start", {
      method: "POST",
      body: JSON.stringify({ confirm_live: confirmLive, confirmation_phrase: phrase }),
    }),
  stop: () => request<Snapshot>("/api/bot/stop", { method: "POST" }),
  switchMode: (mode: Mode, confirmLive = false, phrase = "") =>
    request<Snapshot>("/api/mode", {
      method: "POST",
      body: JSON.stringify({ mode, confirm_live: confirmLive, confirmation_phrase: phrase }),
    }),
  killSwitch: (active: boolean) =>
    request<Snapshot>("/api/kill-switch", {
      method: "POST",
      body: JSON.stringify({ active }),
    }),

  settings: () => request<SettingsState>("/api/settings"),
  patchSettings: (changes: Partial<SettingsState>) =>
    request<SettingsState>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(changes),
    }),

  resetSettings: () => request<SettingsState>("/api/settings/reset", { method: "POST" }),

  orders: (mode: Mode, limit = 100) =>
    request<OrderRow[]>(`/api/orders?mode=${mode}&limit=${limit}`),
  positions: (mode: Mode, limit = 100) =>
    request<PositionRow[]>(`/api/positions?mode=${mode}&limit=${limit}`),
  analytics: (mode: Mode) => request<Analytics>(`/api/analytics?mode=${mode}`),
  triggers: (limit = 100) => request<TriggerRow[]>(`/api/triggers?limit=${limit}`),
  logs: (limit = 200) => request<LogRow[]>(`/api/logs?limit=${limit}`),
  resetPaper: (balance: number) =>
    request<Snapshot>("/api/paper/reset", {
      method: "POST",
      body: JSON.stringify({ balance, confirm: true }),
    }),
  resetDaily: () =>
    request<Snapshot>("/api/daily/reset", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    }),
  resetServer: (confirmationPhrase: string) =>
    request<Snapshot>("/api/server/reset", {
      method: "POST",
      body: JSON.stringify({ confirm: true, confirmation_phrase: confirmationPhrase }),
    }),
  validateLive: () =>
    request<{ ok: boolean; balance: number; allowance: number | null }>("/api/live/validate", {
      method: "POST",
    }),
};
