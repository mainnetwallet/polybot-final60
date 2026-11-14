import type {
  Analytics,
  LogRow,
  Mode,
  OrderRow,
  PositionRow,
  SettingsState,
  Side,
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

export const UNAUTHORIZED_EVENT = "polybot:unauthorized";

export interface AuthStatus {
  auth_required: boolean;
  authenticated: boolean;
  username: string | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = apiToken();
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-API-Key": token } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    // the session expired or was revoked: send the dashboard back to the login page
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
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
  authMe: () => request<AuthStatus>("/api/auth/me"),
  login: (username: string, password: string) =>
    request<AuthStatus>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<AuthStatus>("/api/auth/logout", { method: "POST" }),

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
  /** Instant "sell now" for the shares one stage holds on one side of a market (no confirmation step). */
  sellNow: (marketId: string, side: Side, stage: number) =>
    request<Snapshot>("/api/orders/sell", {
      method: "POST",
      body: JSON.stringify({ market_id: marketId, side, stage }),
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
