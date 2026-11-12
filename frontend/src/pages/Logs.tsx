import { useMemo, useState } from "react";
import type { LogRow } from "../api/types";
import { LevelChip, ModeBadge } from "../components/Badges";
import { EmptyState } from "../components/EmptyState";
import { IconAlert, IconLogs, IconPause, IconPlay, IconSearch, IconTrash } from "../components/Icons";
import { useBot } from "../hooks/BotContext";
import { cx, fmtTime } from "../lib/format";

const LEVELS = ["ALL", "INFO", "WARNING", "ERROR"] as const;

export function Logs() {
  const { logs, clearLogs } = useBot();
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("ALL");
  const [category, setCategory] = useState("ALL");
  const [query, setQuery] = useState("");
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<LogRow[]>([]);

  const categories = useMemo(
    () => ["ALL", ...Array.from(new Set(logs.map((row) => row.category))).sort()],
    [logs],
  );

  const source = paused ? frozen : logs;
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return source.filter((row) => {
      if (level !== "ALL") {
        if (level === "ERROR" ? row.level !== "ERROR" && row.level !== "CRITICAL" : row.level !== level) {
          return false;
        }
      }
      if (category !== "ALL" && row.category !== category) return false;
      if (!needle) return true;
      return (
        row.message.toLowerCase().includes(needle) ||
        row.category.toLowerCase().includes(needle) ||
        JSON.stringify(row.payload ?? {}).toLowerCase().includes(needle)
      );
    });
  }, [source, level, category, query]);

  const errors = logs.filter((row) => row.level === "ERROR" || row.level === "CRITICAL").length;
  const warnings = logs.filter((row) => row.level === "WARNING").length;

  const togglePause = () => {
    if (paused) {
      setPaused(false);
    } else {
      setFrozen(logs);
      setPaused(true);
    }
  };

  return (
    <div className="space-y-4">
      <section className="panel p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/8 bg-white/[0.03] text-slate-400">
              <IconLogs size={17} />
            </div>
            <div>
              <div className="text-[13px] font-semibold text-slate-100">Event stream</div>
              <div className="mt-0.5 text-[11px] text-slate-500">
                Persisted to SQLite and streamed over the dashboard WebSocket · newest first
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="chip border-white/8 bg-white/[0.03] text-slate-500">{logs.length} buffered</span>
            <span className="chip border-amber/25 bg-amber/10 text-amber">
              <IconAlert size={9} />
              {warnings} warn
            </span>
            <span className="chip border-down/25 bg-down/10 text-down">
              <IconAlert size={9} />
              {errors} err
            </span>
            <button type="button" onClick={togglePause} className={cx("btn h-8", paused && "border-amber/35 bg-amber/10 text-amber")}>
              {paused ? <IconPlay size={12} /> : <IconPause size={12} />}
              {paused ? "Resume" : "Pause"}
            </button>
            <button type="button" onClick={clearLogs} className="btn btn-ghost h-8">
              <IconTrash size={12} />
              Clear view
            </button>
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <IconSearch size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
            <input
              className="input pl-9"
              placeholder="Filter messages, categories, payloads…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-lg border border-white/8 bg-black/30 p-0.5">
              {LEVELS.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setLevel(value)}
                  className={cx(
                    "rounded-md px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-[0.1em]",
                    level === value ? "bg-white/8 text-slate-100" : "text-slate-600 hover:text-slate-400",
                  )}
                >
                  {value}
                </button>
              ))}
            </div>
            <select
              className="input w-auto appearance-none py-2 text-[11px]"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              {categories.map((value) => (
                <option key={value} value={value} className="bg-ink-900">
                  {value === "ALL" ? "All categories" : value}
                </option>
              ))}
            </select>
          </div>
        </div>
      </section>

      <section className="panel overflow-hidden">
        <div className="flex items-center justify-between border-b hairline px-5 py-3 text-[10px] text-slate-500">
          <span>
            Showing {filtered.length} of {source.length}
            {paused ? " · stream paused" : ""}
          </span>
          <span className="mono">local SQLite · paper & live tagged</span>
        </div>
        {filtered.length ? (
          <div className="max-h-[calc(100vh-320px)] overflow-y-auto scrollbar">
            {filtered.map((row, index) => (
              <div
                key={row.id ?? `${row.created_at}-${index}`}
                className="flex flex-col gap-1 border-b border-white/[0.03] px-5 py-2.5 font-mono text-[11.5px] hover:bg-white/[0.015] sm:flex-row sm:items-start sm:gap-3"
              >
                <span className="shrink-0 text-slate-600" title={row.created_at}>
                  {fmtTime(row.created_at)}
                </span>
                <span className="shrink-0">
                  <LevelChip level={row.level} />
                </span>
                <span className="shrink-0 rounded border border-white/6 bg-white/[0.02] px-1.5 py-0.5 text-[9.5px] uppercase tracking-[0.1em] text-slate-500">
                  {row.category}
                </span>
                {row.mode === "live" || row.mode === "paper" ? (
                  <span className="shrink-0">
                    <ModeBadge mode={row.mode} size="sm" />
                  </span>
                ) : null}
                <div className="min-w-0 flex-1">
                  <div className={cx("break-words", row.level === "ERROR" || row.level === "CRITICAL" ? "text-down/90" : row.level === "WARNING" ? "text-amber/90" : "text-slate-300")}>
                    {row.message}
                  </div>
                  {row.payload && Object.keys(row.payload).length ? (
                    <div className="mt-0.5 truncate text-[10px] text-slate-600">{JSON.stringify(row.payload)}</div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<IconLogs size={26} />}
            title={logs.length ? "No logs match this filter" : "No log entries yet"}
            hint={
              logs.length
                ? "Adjust the level, category or search filter."
                : "Start the backend and the bot to populate the persisted event stream."
            }
          />
        )}
      </section>
    </div>
  );
}
