import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { IconAlert, IconCheck, IconEye, IconEyeOff, IconLock, IconShield, IconUser } from "../components/Icons";
import { useAuth } from "../hooks/AuthContext";
import { cx } from "../lib/format";

const DEFAULT_USERNAME = "admin";

/** Animated "market" backdrop: drifting glow orbs, a perspective grid and a live-drawing price line. */
function Backdrop() {
  return (
    <div className="login-backdrop" aria-hidden="true">
      <div className="login-orb login-orb-a" />
      <div className="login-orb login-orb-b" />
      <div className="login-orb login-orb-c" />
      <div className="login-grid" />
      <svg className="login-chart" viewBox="0 0 1200 300" preserveAspectRatio="none">
        <defs>
          <linearGradient id="lc-stroke" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0" stopColor="#5cc8ff" stopOpacity="0" />
            <stop offset="0.35" stopColor="#5cc8ff" stopOpacity="0.8" />
            <stop offset="1" stopColor="#35e0a1" stopOpacity="1" />
          </linearGradient>
          <linearGradient id="lc-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#35e0a1" stopOpacity="0.18" />
            <stop offset="1" stopColor="#35e0a1" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path
          className="login-chart-fill"
          d="M0 230 L80 210 L150 222 L230 180 L300 196 L380 150 L450 168 L540 120 L620 142 L700 96 L790 118 L870 70 L960 92 L1050 48 L1130 62 L1200 30 L1200 300 L0 300 Z"
          fill="url(#lc-fill)"
        />
        <path
          className="login-chart-line"
          d="M0 230 L80 210 L150 222 L230 180 L300 196 L380 150 L450 168 L540 120 L620 142 L700 96 L790 118 L870 70 L960 92 L1050 48 L1130 62 L1200 30"
          fill="none"
          stroke="url(#lc-stroke)"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          pathLength={1}
        />
      </svg>
    </div>
  );
}

function Spinner() {
  return <span className="login-spinner" aria-hidden="true" />;
}

export function Login() {
  const { login, enter, phase } = useAuth();
  const [username, setUsername] = useState(DEFAULT_USERNAME);
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const shake = () => {
    formRef.current?.animate(
      [
        { transform: "translateX(0)" },
        { transform: "translateX(-8px)" },
        { transform: "translateX(7px)" },
        { transform: "translateX(-5px)" },
        { transform: "translateX(3px)" },
        { transform: "translateX(0)" },
      ],
      { duration: 420, easing: "ease-out" },
    );
  };

  useEffect(() => {
    document.title = "Sign in · Polybot";
    passwordRef.current?.focus(); // username is prefilled with "admin", so jump straight to the password
  }, []);

  const trackCaps = (event: KeyboardEvent<HTMLInputElement>) => setCapsLock(event.getModifierState("CapsLock"));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || done) return;
    if (!username.trim() || !password) {
      setError("Enter your username and password.");
      shake();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const status = await login(username.trim(), password);
      setDone(true); // let the success state and exit animation play before the dashboard opens
      window.setTimeout(() => enter(status), 750);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed.");
      shake();
      setPassword("");
      passwordRef.current?.focus();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={cx("login-root", done && "login-leaving")}>
      <Backdrop />

      <main className="relative z-10 flex min-h-full items-center justify-center px-4 py-10">
        <div className="login-stage w-full max-w-[420px]">
          <div className="login-brand login-rise" style={{ animationDelay: "60ms" }}>
            <div className="login-logo">
              <span className="login-logo-ring" />
              <span className="login-logo-core">PB</span>
            </div>
            <h1 className="mt-5 text-[26px] font-semibold tracking-tight text-slate-50">Welcome back</h1>
            <p className="mt-1.5 text-[13px] text-slate-400">Sign in to your Polybot control panel</p>
          </div>

          <form
            ref={formRef}
            onSubmit={submit}
            noValidate
            className="login-card login-rise mt-8"
            style={{ animationDelay: "160ms" }}
          >
            <label className="login-field" style={{ animationDelay: "260ms" }}>
              <span className="label">Username</span>
              <div className="login-input-wrap">
                <IconUser size={16} className="login-input-icon" />
                <input
                  className="login-input"
                  type="text"
                  name="username"
                  autoComplete="username"
                  spellCheck={false}
                  autoCapitalize="none"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  aria-invalid={error ? true : undefined}
                />
              </div>
            </label>

            <label className="login-field mt-4" style={{ animationDelay: "340ms" }}>
              <span className="label flex items-center justify-between">
                Password
                {capsLock ? (
                  <span className="flex items-center gap-1 normal-case tracking-normal text-amber">
                    <IconAlert size={11} /> Caps Lock is on
                  </span>
                ) : null}
              </span>
              <div className="login-input-wrap">
                <IconLock size={16} className="login-input-icon" />
                <input
                  ref={passwordRef}
                  className="login-input pr-11"
                  type={show ? "text" : "password"}
                  name="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyUp={trackCaps}
                  onKeyDown={trackCaps}
                  onBlur={() => setCapsLock(false)}
                  aria-invalid={error ? true : undefined}
                />
                <button
                  type="button"
                  className="login-eye"
                  onClick={() => setShow((v) => !v)}
                  aria-label={show ? "Hide password" : "Show password"}
                  tabIndex={-1}
                >
                  {show ? <IconEyeOff size={16} /> : <IconEye size={16} />}
                </button>
              </div>
            </label>

            <div className={cx("login-error", error && "login-error-open")} role="alert" aria-live="assertive">
              <div className="login-error-inner">
                <IconAlert size={14} />
                <span>{error}</span>
              </div>
            </div>

            <button
              type="submit"
              disabled={busy || done}
              className={cx("login-submit", done && "login-submit-done")}
              style={{ animationDelay: "420ms" }}
            >
              <span className="login-submit-sheen" aria-hidden="true" />
              {done ? (
                <>
                  <IconCheck size={16} /> Signed in
                </>
              ) : busy ? (
                <>
                  <Spinner /> Signing in…
                </>
              ) : (
                <>Sign in</>
              )}
            </button>

            <p className="mt-4 flex items-center justify-center gap-1.5 text-[11px] text-slate-500">
              <IconShield size={11} />
              You stay signed in on this browser until you log out.
            </p>
          </form>

          {phase === "unreachable" ? (
            <p className="mt-4 text-center text-[11.5px] text-amber">Backend unreachable. Retrying…</p>
          ) : null}

          <p className="login-rise mt-7 text-center text-[10px] uppercase tracking-[0.18em] text-slate-600" style={{ animationDelay: "520ms" }}>
            Polybot · Polymarket BTC · Secure access
          </p>
        </div>
      </main>
    </div>
  );
}

/** Splash shown while the app asks the backend whether this browser is already signed in. */
export function AuthSplash({ unreachable, onRetry }: { unreachable: boolean; onRetry: () => void }) {
  return (
    <div className="login-root">
      <Backdrop />
      <div className="relative z-10 flex h-full flex-col items-center justify-center gap-5">
        <div className="login-logo">
          <span className="login-logo-ring" />
          <span className="login-logo-core">PB</span>
        </div>
        {unreachable ? (
          <div className="text-center">
            <p className="text-[13px] text-amber">Backend unreachable. Retrying…</p>
            <button type="button" className="btn mt-3" onClick={onRetry}>
              Retry now
            </button>
          </div>
        ) : (
          <div className="login-dots" aria-label="Loading">
            <span />
            <span />
            <span />
          </div>
        )}
      </div>
    </div>
  );
}
