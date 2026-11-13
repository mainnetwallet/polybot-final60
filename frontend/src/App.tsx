import { useEffect, useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { ModeSwitchModal, StartLiveModal, KillSwitchModal } from "./components/SafetyModals";
import { ToastStack } from "./components/ToastStack";
import { IconAlert } from "./components/Icons";
import { AuthProvider, useAuth } from "./hooks/AuthContext";
import { BotProvider, useBot } from "./hooks/BotContext";
import { PAGE_META, pageFromHash, type Page } from "./lib/navigation";
import { Analytics } from "./pages/Analytics";
import { Logs } from "./pages/Logs";
import { AuthSplash, Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Settings } from "./pages/Settings";
import { Strategy } from "./pages/Strategy";
import { Trading } from "./pages/Trading";

function PageView({ page }: { page: Page }) {
  switch (page) {
    case "strategy":
      return <Strategy />;
    case "trading":
      return <Trading />;
    case "analytics":
      return <Analytics />;
    case "settings":
      return <Settings />;
    case "logs":
      return <Logs />;
    default:
      return <Overview />;
  }
}

function Shell() {
  const { snapshot, feedState } = useBot();
  const [page, setPage] = useState<Page>(() => pageFromHash());
  const [modeOpen, setModeOpen] = useState(false);
  const [liveOpen, setLiveOpen] = useState(false);
  const [killOpen, setKillOpen] = useState(false);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    document.title = `${PAGE_META[page].title} · Polybot`;
  }, [page]);

  const navigate = (next: Page) => {
    if (window.location.hash !== `#${next}`) window.location.hash = next;
    setPage(next);
  };

  const targetMode = snapshot?.bot.mode === "live" ? "paper" : "live";

  return (
    <div className="flex h-full">
      <Sidebar page={page} onNavigate={navigate} />

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar
          page={page}
          onNavigate={navigate}
          onOpenMode={() => setModeOpen(true)}
          onStartLive={() => setLiveOpen(true)}
          onOpenKill={() => setKillOpen(true)}
        />

        <main className="flex-1 overflow-y-auto scrollbar">
          <div className="mx-auto w-full max-w-[1560px] px-4 py-5 sm:px-6">
            {feedState === "offline" && !snapshot ? (
              <div className="mb-4 flex items-center gap-2.5 rounded-xl border border-amber/25 bg-amber/[0.06] px-4 py-3 text-[12px] text-amber/90">
                <IconAlert size={15} />
                Backend unreachable. Start it with <span className="mono">start.bat</span> or{" "}
                <span className="mono">uvicorn app.main:app --port 8000</span>; this dashboard reconnects automatically.
              </div>
            ) : null}
            <PageView page={page} />
          </div>
        </main>
      </div>

      <ModeSwitchModal open={modeOpen} onClose={() => setModeOpen(false)} target={targetMode} />
      <StartLiveModal open={liveOpen} onClose={() => setLiveOpen(false)} />
      <KillSwitchModal open={killOpen} onClose={() => setKillOpen(false)} />
      <ToastStack />
    </div>
  );
}

function Gate() {
  const { phase, retry } = useAuth();
  if (phase === "checking" || phase === "unreachable") {
    return <AuthSplash unreachable={phase === "unreachable"} onRetry={retry} />;
  }
  if (phase === "login") return <Login />;
  // BotProvider (REST + WebSocket) only starts once the browser is signed in
  return (
    <BotProvider>
      <div className="app-enter h-full">
        <Shell />
      </div>
    </BotProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}
