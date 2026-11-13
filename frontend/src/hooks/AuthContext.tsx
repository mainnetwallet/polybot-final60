import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, UNAUTHORIZED_EVENT, type AuthStatus } from "../api/client";

export type AuthPhase = "checking" | "login" | "authed" | "unreachable";

interface AuthContextValue {
  phase: AuthPhase;
  username: string | null;
  authRequired: boolean;
  /** Verifies the credentials (sets the session cookie). Call `enter` afterwards to open the dashboard. */
  login: (username: string, password: string) => Promise<AuthStatus>;
  enter: (status: AuthStatus) => void;
  logout: () => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<AuthPhase>("checking");
  const [username, setUsername] = useState<string | null>(null);
  const [authRequired, setAuthRequired] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const check = () => {
      api
        .authMe()
        .then((status) => {
          if (!alive) return;
          setAuthRequired(status.auth_required);
          setUsername(status.username);
          setPhase(status.authenticated ? "authed" : "login");
        })
        .catch(() => {
          if (!alive) return;
          setPhase("unreachable");
          timer = window.setTimeout(check, 2500); // backend still starting: keep trying quietly
        });
    };
    check();
    return () => {
      alive = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [attempt]);

  useEffect(() => {
    const onUnauthorized = () => setPhase((current) => (current === "authed" ? "login" : current));
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const login = useCallback((user: string, password: string) => api.login(user, password), []);

  const enter = useCallback((status: AuthStatus) => {
    setUsername(status.username);
    setAuthRequired(status.auth_required);
    setPhase("authed");
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setPhase("login");
    }
  }, []);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const value = useMemo(
    () => ({ phase, username, authRequired, login, enter, logout, retry }),
    [phase, username, authRequired, login, enter, logout, retry],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
