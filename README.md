# FINAL60 - Polymarket BTC 5-minute bot

A local Windows application for Polymarket BTC 5-minute UP/DOWN markets.

The strategy is fixed and intentionally simple:

- **First 4 minutes: no trade.** No confirmation signals.
- **Final 60 seconds: monitor UP and DOWN.** If `UP best ask >= 0.80`, submit a **BUY at the current market price**. Same for DOWN.
- `0.80` is a **trigger threshold, never a limit price**.
- Execution is WebSocket/event-driven. There are no polling sleeps or artificial delays in the trigger path.

Paper trading is the default. Live trading is implemented but disabled by default and guarded by explicit safety gates.

---

## Requirements

- Windows 10 or 11
- Python 3.12+ (the code is 3.11-compatible; this host was verified on 3.11.9)
- Node.js 20.19+ or 22.12+ (verified on 24.20.0)
- Internet access to `gamma-api.polymarket.com`, `clob.polymarket.com`, and the CLOB WebSocket
- A correctly synchronized system clock (Windows Settings > Time & Language > Sync now)

---

## Quick start

```text
setup.bat     one-time install (Python venv, dependencies, .env, database)
start.bat     start backend + dashboard and open the dashboard
stop.bat      stop both services
```

`start.bat` runs setup automatically if `.venv`, `frontend\node_modules`, or `.env` is missing.

URLs:

- Dashboard: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000`
- API docs: `http://127.0.0.1:8000/docs`

The launcher refuses to start a second copy when ports 8000/5173 are already served by this app, and fails clearly when an unrelated process holds a port. PIDs are tracked in `logs\pids`.

---

## How it works

```text
Gamma API (market discovery, 3 s refresh)
        |
        v
active 5-minute BTC market + UP/DOWN token ids
        |
        v
CLOB WebSocket (book, price_change, best_bid_ask)
        |
        v
strategy engine: in final window? ask >= trigger?
        |
        v
execution guards: fresh data, price cap, risk, duplicate/conflict policy, expiration
        |
        v
paper engine (simulated fills)  or  live engine (real CLOB FOK order)
        |
        v
SQLite (orders, fills, positions, triggers, settings) + portfolio analytics
        |
        v
WebSocket/HTTP state -> React dashboard
```

Key properties:

- Market validation is against the active event slug for the current window (`btc-updown-5m-<window_start_unix>`).
- Orders are never submitted after market expiration.
- A marketable BUY uses the **current ask**, capped by `MAX_EXECUTION_PRICE`; it is not a resting order.
- Duplicate protection is enforced with per-market locks, an idempotency key, and `DUPLICATE_POLICY`.
- Conflicting simultaneous side triggers are resolved by `CONFLICT_POLICY`.
- Orders, fills, positions, trigger decisions, and settings overrides are persisted in `data\polymarket_bot.db`.
- Restart recovery keeps the bot **STOPPED** and reloads persisted trades/positions/settings.

### Backend modules

| Module | Responsibility |
| --- | --- |
| `market_discovery` | Gamma series/event lookup, active market, resolution |
| `market_data` | CLOB WebSocket client, book state, freshness |
| `strategy` | Final-60s window and ask-threshold evaluation |
| `risk_manager` | Daily loss/target, trade count, open positions, kill switch |
| `order_manager` | Order decisions, idempotency, duplicate/conflict policy |
| `paper_trading` | Simulated immediate-market BUY, walking the ask ladder |
| `live_trading` | Real CLOB marketable FOK BUY with ack verification |
| `portfolio` | Balances, positions, settlement, analytics |
| `monitoring` | In-memory log stream and persisted file logs |
| `services.bot` | Orchestration, state snapshot, WebSocket broadcast |
| `api` | FastAPI routes and `/api/ws` |

### Dashboard pages

- **Overview** - market phase, countdown, UP/DOWN asks, account envelope, feed health, recent orders
- **Strategy** - final-window rules, live evaluation meters, trigger audit trail
- **Trading** - mode banner, performance stats, open orders, order history, positions
- **Analytics** - mode-separated PnL, win rate, latency, volume, fees
- **Settings** - runtime-tunable values, live readiness, paper reset
- **Logs** - live server log stream with level/category/search filters

---

## Paper vs live

### Paper (default)

- Uses live Polymarket data from the CLOB WebSocket.
- Simulates the current-market BUY against the ask ladder with configurable slippage, fill ratio (partial fills), and an approximate fee.
- Never sends real orders.
- Paper balances and statistics are stored separately from live.

### Live

Live mode is off by default and is never switched on automatically. Enabling it requires all of the following:

1. `LIVE_TRADING_ENABLED=true` in `.env`
2. `POLYMARKET_GEO_VERIFIED=true` in `.env` (set this only after confirming your jurisdiction/polymarket eligibility)
3. Private key and funder address present in `.env`
4. The bot stopped with no outstanding orders
5. Switching mode in the dashboard and typing the confirmation phrase (`GO LIVE`)
6. Passing the live validation/start flow

The backend enforces these gates; the frontend mirrors them but is not the security boundary.

Private keys and API secrets are read from environment variables only. They are never returned by the API and never sent to the browser.

Kill switch behavior: it blocks new orders and stops the bot. It cannot reverse fills that already happened.

---

## Configuration

Edit `.env` (created from `.env.example` by setup). Changes to `.env` require a restart.

Strategy and risk defaults:

| Key | Default | Meaning |
| --- | --- | --- |
| `TRIGGER_THRESHOLD` | `0.80` | Ask level that triggers a BUY |
| `FINAL_WINDOW_SECONDS` | `60` | Trading window at the end of the market |
| `MAX_EXECUTION_PRICE` | `0.99` | Hard cap on the price paid |
| `TRADE_AMOUNT_USDC` | `5.0` | Requested notional per trade |
| `STALE_DATA_MAX_AGE_MS` | `2000` | Maximum book age for execution |
| `MAX_OPEN_POSITIONS` | `1` | Concurrent position cap |
| `DUPLICATE_POLICY` | `one_per_market` | `one_per_market`, `one_per_side`, `allow` |
| `CONFLICT_POLICY` | `up_first` | `up_first`, `down_first`, `highest_ask`, `block_both` |
| `MAX_DAILY_LOSS` | `5.0` | Daily loss stop (USDC) |
| `DAILY_PROFIT_TARGET` | `10.0` | Daily profit target (USDC) |
| `MAX_TRADES_PER_DAY` | `60` | Trade count cap |
| `PAPER_STARTING_BALANCE` | `10.0` | Paper bankroll |
| `PAPER_FILL_RATIO` | `1.0` | Simulated fill fraction (partial fills) |
| `PAPER_SLIPPAGE_BPS` | `0` | Simulated slippage |
| `PAPER_FEE_RATE` | `0.07` | Approximate fee rate |
| `PAPER_FEE_EXPONENT` | `1.0` | Fee curve exponent |
| `LIVE_TRADING_ENABLED` | `false` | Master live switch |
| `POLYMARKET_GEO_VERIFIED` | `false` | Set true only after verifying eligibility |

The dashboard Settings page can change a validated subset without editing `.env`; overrides persist in SQLite:

`trigger_threshold`, `final_window_seconds`, `max_execution_price`, `trade_amount_usdc`,
`max_daily_loss`, `daily_profit_target`, `stale_data_max_age_ms`, `duplicate_policy`,
`conflict_policy`, `paper_slippage_bps`, `paper_fee_rate`, `max_trades_per_day`, `max_open_positions`.

Secrets, `paper_fill_ratio`, `paper_fee_exponent`, `paper_starting_balance`, `live_trading_enabled`, and `geo_verified` are environment-only.

---

## Data and logs

| Path | Contents |
| --- | --- |
| `data\polymarket_bot.db` | Permanent SQLite database (WAL mode) |
| `logs\backend.log` | Rotating application log (5 MB x 5) |
| `logs\backend.out.log` / `.err.log` | Uvicorn stdout/stderr |
| `logs\frontend.out.log` / `.err.log` | Vite stdout/stderr |
| `logs\pids\*.pid` | PIDs of launcher-started services |

Paper reset is available in Settings only while the bot is stopped. Reset deletes paper orders/positions and restores the configured starting balance; live records are untouched.

---

## Testing

Backend suite (strategy, risk, order flow, API, portfolio):

```bat
cd backend
..\.venv\Scripts\python.exe -m pytest -q
```

Frontend type-check and production build:

```bat
cd frontend
npm run build
```

Results on this machine (2026-11-12):

- `pytest`: **50 passed** (3.2 s)
- `npm run build`: PASS (TypeScript strict, Vite 53 modules)
- End-to-end paper evidence: UP ask reached `0.80` with `59.394 s` left in the final window; order filled `6.25` shares at `0.80`, fee `0.07`, measured latency `4 ms`; a second trigger was blocked by `blocked_duplicate`; the market resolved UP for `+1.18 USDC` realized; a stop/start cycle reloaded identical order, position, and analytics records.

These are paper-execution results. See limitations below.

---

## Limitations (honest list)

- **Live execution was not exercised with real funds.** The live path, gates, and order construction are implemented and unit-tested, but no real order has been submitted from this machine.
- **Polymarket geo eligibility is unverified.** `POLYMARKET_GEO_VERIFIED` stays `false` by default; do not set it without checking your jurisdiction.
- **Paper latency is simulated.** The measured `4 ms` is internal processing time, not a CLOB round-trip. Live latency will be network-dependent.
- **Paper fees are approximate.** The fee formula is an approximation of the verified schedule and should be reviewed before relying on absolute paper PnL.
- **Gamma resolution delay.** Gamma may keep `closed:false` (with near-1/0 prices) for a while after expiry. Resolution requires `closed:true` and published 1/0 prices, so a paper position can remain open across a restart until Gamma publishes the result.
- **On-chain reconciliation is best-effort.** Live fills depend on the CLOB API responses; there is no separate on-chain balance reconciler.
- **Single market family.** Discovery targets the `btc-up-or-down-5m` series only.
- **Clock skew matters.** Windows clock drift can make books look stale and block trades. Fix time sync rather than relaxing `STALE_DATA_MAX_AGE_MS`.
- **Python version.** The project targets 3.12+; the verified host runs 3.11.9.

---

## Troubleshooting

**"Port 8000/5173 is already in use"** - close the other process, or run `stop.bat` if it is a previous copy of this app.

**Startup failed** - read `logs\backend.err.log`, `logs\frontend.err.log`, then `logs\backend.log`. `start.bat` stops only the services it started this run.

**Bot does not trade** - confirm the bot is RUNNING, the market is in its final 60 seconds, the ask actually reaches the trigger, book data is fresh, the daily risk limits are intact, and no duplicate/conflict rule blocked the trigger (see the Strategy page audit trail).

**Paper reset does nothing** - stop the bot first; in-memory state would otherwise overwrite the reset.

**Live options are disabled** - this is the intended default. See the live gates above.

---

## Risk warning

This software can place real orders if you enable live trading. Prediction markets are risky and you can lose your entire balance. Use paper mode first, verify your jurisdiction and API credentials, and start with small sizes. This project is not financial advice.
